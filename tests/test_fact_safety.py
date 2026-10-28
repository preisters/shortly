from __future__ import annotations

import unittest
from unittest.mock import patch

from shortly.generate.factcheck import (
    _extra_temporal_search,
    _format_research,
    apply_factcheck_repairs,
    factcheck_script,
)
from shortly.generate.research import (
    build_queries,
    collect_raw,
    distill_research,
    rank_search_results,
    result_relevant_to_query,
    research_topic,
)
from shortly.llm.base import LLMClient
from shortly.llm.base import LLMResponseParseError


class _ResearchLlm(LLMClient):
    available = True

    def complete_json(self, *args, **kwargs):
        return {
            "quantified_facts": [
                {
                    "subject": "대한민국 조미김 수출",
                    "metric": "수출액",
                    "value": "4",
                    "unit": "억 달러",
                    "period": "2023년",
                    "comparison_value": "2억9400만 달러",
                    "comparison_period": "2022년",
                    "change": "36% 증가",
                    "source_label": "식품의약품안전처 인용 보도",
                    "source_url": "https://example.test/article",
                },
                {
                    "subject": "범위 불명",
                    "metric": "수출액",
                    "value": "9",
                    "unit": "억 달러",
                    "period": "지난해",
                    "source_label": "출처 불명",
                    "source_url": "",
                },
                {
                    "subject": "대한민국 전체 김 수출",
                    "metric": "수출액",
                    "value": "9",
                    "unit": "억 달러",
                    "period": "2023년",
                    "source_label": "존재하지 않는 출처",
                    "source_url": "https://hallucinated.example/article",
                },
            ],
            "hard_facts": [
                "성경식품은 지도표 성경김을 판매한다",
                "조미김 수출액은 2023년 4억 달러였다",
            ],
            "drama_beats": [],
            "soft_angles": [],
            "curiosity_hooks": [],
            "avoid_claims": [],
        }


class _ProjectedResearchLlm(LLMClient):
    available = True

    def complete_json(self, *args, **kwargs):
        return {
            "quantified_facts": [
                {
                    "subject": "성경식품 미국 수출",
                    "metric": "수출액 전망",
                    "value": "2600만",
                    "unit": "달러",
                    "period": "2024년 전망",
                    "comparison_value": "",
                    "comparison_period": "",
                    "change": "",
                    "source_label": "인터뷰",
                    "source_url": "https://example.test/forecast",
                }
            ],
            "hard_facts": [],
            "drama_beats": [],
            "soft_angles": [],
            "curiosity_hooks": [],
            "avoid_claims": [],
        }


class FactcheckResearchFormattingTests(unittest.TestCase):
    def test_user_approved_soft_anecdote_is_separate_from_hard_facts(self) -> None:
        anecdote = (
            "한 베트남 네티즌이 제사가 끝난 뒤 어른들이 초코파이를 "
            "나눠주는 것만 기다렸다고 회상함"
        )

        formatted = _format_research({
            "hard_facts": ["초코파이가 베트남 제단에 오른 모습이 화제가 됨"],
            "user_approved_soft_anecdotes": [anecdote],
        })

        self.assertIn("[사용자 승인 소프트 일화", formatted)
        self.assertIn(anecdote, formatted)
        self.assertNotEqual(formatted.find("[하드 팩트]"), formatted.find(anecdote))

    def test_research_prompt_keeps_explicit_cultural_focus_visible(self) -> None:
        raw = {
            "queries": ["베트남 설 제사상 과자탑 초코파이"],
            "research_focus": ["베트남 설 제사상 과자탑"],
            "research_focus_coverage": [
                {"item": "베트남 설 제사상 과자탑", "result_count": 1}
            ],
            "results": [
                {
                    "title": "베트남 설 과자탑",
                    "snippet": "설 제사상에 초코파이 상자를 탑으로 쌓는다.",
                    "url": "https://example.test/tet-tower",
                    "source_tier": "editorial",
                }
            ],
            "backend": "fixture",
        }
        captured = {}

        class _CaptureLlm(LLMClient):
            available = True

            def complete_json(self, system, user, **kwargs):
                captured["system"] = system
                captured["user"] = user
                return {
                    "narrative_axis": "베트남 설 제사상 과자탑에 초코파이가 오른 과정",
                    "hard_facts": ["초코파이 상자가 설 제사상 과자탑에 쓰인다"],
                    "supporting_facts": ["초코파이가 현지 파이 시장에서 성장했다"],
                    "drama_beats": ["편의점 과자가 조상에게 올리는 과자탑이 됐다"],
                    "soft_angles": [],
                    "curiosity_hooks": [],
                    "avoid_claims": [],
                    "out_of_scope_facts": [],
                    "quantified_facts": [],
                }

        distilled = distill_research(_CaptureLlm(), "초코파이", raw)

        self.assertIn("시장 장벽·해결 행동·성과도 drama_beats", captured["system"])
        self.assertIn("'왜 하필'을 범용 호기심 공식으로 사용하지 마라", captured["system"])
        self.assertIn("베트남 설 제사상 과자탑", captured["user"])
        self.assertIn("과자탑", distilled["narrative_axis"])


class _RetryResearchLlm(LLMClient):
    available = True

    def __init__(self) -> None:
        self.calls = 0

    def complete_json(self, *args, **kwargs):
        self.calls += 1
        if self.calls == 1:
            raise LLMResponseParseError("truncated")
        return {
            "quantified_facts": [],
            "hard_facts": ["성경식품은 독도 표기를 고수했다."],
            "drama_beats": [],
            "soft_angles": [],
            "curiosity_hooks": [],
            "avoid_claims": [],
        }


class _ScopeResearchLlm(LLMClient):
    available = True

    def __init__(self) -> None:
        self.system = ""

    def complete_json(self, system, user, **kwargs):
        self.system = system
        return {
            "narrative_axis": "베트남 제사상에 초코파이가 올라가게 된 문화적 의미",
            "out_of_scope_facts": [
                "별도 사건인 베트남 대학생의 37시간 조난 생존담",
            ],
            "quantified_facts": [],
            "hard_facts": ["베트남에서 초코파이가 제사상에 오르는 사례가 보도됐다"],
            "supporting_facts": ["오리온은 화제가 된 장면을 마케팅에 활용했다"],
            "drama_beats": ["일상 과자가 조상을 기리는 제물로 받아들여졌다"],
            "soft_angles": [],
            "curiosity_hooks": [],
            "avoid_claims": [],
        }


class FactSafetyTests(unittest.TestCase):
    def test_origin_story_queries_preserve_compound_subject_and_story_actions(self) -> None:
        topic = (
            "임실 치즈의 탄생 비화. 가난한 농민을 돕기 위해 산양을 키우고 "
            "유럽에서 기술을 배워 국내 치즈 산업의 출발점을 만든 이야기"
        )

        queries = build_queries(topic, max_queries=5)

        self.assertTrue(all("임실 치즈" in value for value in queries))
        self.assertTrue(any("지정환 신부" in value for value in queries))
        self.assertTrue(any("산양" in value and "농민" in value for value in queries))
        self.assertTrue(any("유럽" in value and "기술" in value for value in queries))
        self.assertFalse(any("유행" in value or "가격" in value for value in queries))

    def test_other_origin_story_does_not_inherit_imsil_person(self) -> None:
        queries = build_queries(
            "어떤 빵 브랜드의 탄생 비화와 초기 실패 이야기",
            max_queries=5,
        )

        self.assertFalse(any("지정환" in value for value in queries))

    def test_non_dokdo_story_factcheck_never_uses_dokdo_queries(self) -> None:
        script = {
            "title": "임실을 치즈로 뒤집은 벨기에 신부",
            "segments": [
                {"text": "1964년 지정환 신부가 임실에 왔음"},
                {"text": "산양유로 치즈를 만들기 시작했음"},
            ],
        }

        with patch(
            "shortly.generate.factcheck.web_search",
            return_value=[],
        ) as search:
            _extra_temporal_search("임실 치즈의 탄생 비화", script)

        queries = [call.args[0] for call in search.call_args_list]
        self.assertTrue(any("임실 치즈" in value for value in queries))
        self.assertFalse(any("일본" in value or "독도" in value for value in queries))

    def test_search_ranking_excludes_adult_noise(self) -> None:
        ranked = rank_search_results(
            [
                {
                    "title": "무료 성인 영상",
                    "snippet": "검색과 무관한 결과",
                    "url": "https://www.xvideos.com/video.fixture",
                },
                {
                    "title": "임실군 공식 연혁",
                    "snippet": "임실 치즈의 시작",
                    "url": "https://www.imsil.go.kr/history",
                },
            ],
            8,
        )

        self.assertEqual(len(ranked), 1)
        self.assertIn("imsil.go.kr", ranked[0]["url"])
    def test_queries_balance_main_event_and_requested_payoff(self) -> None:
        queries = build_queries(
            "성경김이 독도 때문에 일본 수출을 포기했지만 해외에서 성공한 이야기",
            max_queries=5,
        )

        self.assertTrue(any("성경식품" in value and "독도" in value for value in queries))
        self.assertTrue(any("성경김" in value and "해외 수출 실적" in value for value in queries))
        self.assertTrue(any("성경김" in value and "미국 수출" in value for value in queries))
        self.assertTrue(any("일본 수출 포기 언제" in value for value in queries))
        self.assertFalse(any("성경김이 해외" in value for value in queries))

    def test_queries_keep_late_revision_payoff_and_known_subject(self) -> None:
        topic = (
            "지도표 성경김 성경김 "
            + "기존 대본과 사실관계를 다시 확인한다 " * 20
            + "마무리는 일본을 포기했지만 해외에서 없어서 못 팔 정도의 "
            "해피엔딩으로 바꿔줘"
        )

        queries = build_queries(topic, max_queries=5)

        self.assertTrue(
            any("성경김" in value and "해외 수출 실적" in value for value in queries)
        )
        self.assertTrue(
            any("성경김" in value and "매출 비중" in value for value in queries)
        )
        self.assertFalse(any('"지도표" 해외' in value for value in queries))

    def test_queries_prioritize_locked_subject_over_previous_episode_reference(
        self,
    ) -> None:
        topic = (
            "일본 수출을 포기한 유아용 쌀과자 회사 올바름. "
            "첫 대사에서 성경김을 떠올린다고 연결하고, 독도 포장과 "
            "대만 홍콩 수출 및 주문 증가를 확인해줘."
        )

        queries = build_queries(
            topic,
            max_queries=5,
            subject_anchors=["올바름"],
        )

        self.assertTrue(all("올바름" in value for value in queries))
        self.assertFalse(any("성경김" in value or "성경식품" in value for value in queries))

    def test_research_collects_from_every_query_before_filling_limit(
        self,
    ) -> None:
        def fake_search(query: str, max_results: int = 3):
            return [
                {
                    "title": f"{query} 결과 {index}",
                    "snippet": query,
                    "url": f"https://example.test/{abs(hash(query))}/{index}",
                    "query": query,
                    "source_tier": "general",
                    "source_score": 2,
                }
                for index in range(max_results)
            ]

        with patch(
            "shortly.generate.research.web_search",
            side_effect=fake_search,
        ) as search:
            raw = collect_raw(
                "성경김이 독도 때문에 일본 수출을 포기했지만 해외에서 성공한 이야기",
                max_results=8,
                max_queries=5,
            )

        self.assertEqual(search.call_count, 5)
        first_round_queries = {
            row["query"] for row in raw["results"][:5]
        }
        self.assertEqual(first_round_queries, set(raw["queries"]))

    def test_explicit_research_focus_runs_each_item_as_its_own_query(self) -> None:
        focus = (
            "베트남 설 제사의 실제 상차림\n"
            "제사상에 초코파이가 올라간다는 근거\n"
            "과자탑이 전통인지 현대 연출인지"
        )

        with patch(
            "shortly.generate.research.web_search",
            return_value=[],
        ) as search:
            raw = collect_raw(
                "베트남 제사상에 올라가는 초코파이",
                max_results=8,
                max_queries=2,
                subject_anchors=["초코파이"],
                research_focus=focus,
            )

        called_queries = [call.args[0] for call in search.call_args_list]
        self.assertGreaterEqual(len(called_queries), 4)
        for item in focus.splitlines():
            self.assertIn(item, called_queries)
        self.assertEqual(raw["research_focus"], focus.splitlines())
        self.assertEqual(raw["research_focus_queries"], focus.splitlines())

    def test_distillation_separates_same_product_but_unrelated_anecdote(self) -> None:
        llm = _ScopeResearchLlm()
        raw = {
            "queries": ["베트남 제사상 초코파이"],
            "results": [
                {
                    "title": "베트남 제사상에 오른 초코파이와 별도 조난 일화",
                    "snippet": "제사상 사례와 37시간 조난 대학생 사례",
                    "url": "https://example.test/chocopie",
                    "source_tier": "editorial",
                }
            ],
            "backend": "fixture",
        }

        distilled = distill_research(llm, "베트남 제사상에 올라가는 초코파이", raw)

        self.assertIn("같은 제품이 등장한다는 이유만으로", llm.system)
        self.assertIn("서사적 가치를 구분", llm.system)
        self.assertNotIn("조난", " ".join(distilled["drama_beats"]))
        self.assertIn("조난", " ".join(distilled["out_of_scope_facts"]))
        self.assertIn("마케팅", " ".join(distilled["supporting_facts"]))
        self.assertIn("훅·반전·결말 재료로 사용 금지", distilled["brief_text"])
        self.assertIn("중심 서사 밖", distilled["brief_text"])

    def test_search_ranking_excludes_low_quality_noise_when_sources_exist(
        self,
    ) -> None:
        ranked = rank_search_results(
            [
                {
                    "title": "재가공 영상",
                    "snippet": "확인되지 않은 이야기",
                    "url": "https://www.youtube.com/watch?v=fixture",
                },
                {
                    "title": "정부 통계",
                    "snippet": "공식 발표",
                    "url": "https://example.go.kr/report/1",
                },
                {
                    "title": "일반 자료",
                    "snippet": "보조 자료",
                    "url": "https://example.test/article",
                },
            ],
            8,
        )

        self.assertEqual(
            [row["source_tier"] for row in ranked],
            ["authoritative", "general"],
        )
        self.assertTrue(
            all("youtube.com" not in row["url"] for row in ranked)
        )

    def test_search_ranking_unwraps_duckduckgo_redirect(self) -> None:
        ranked = rank_search_results(
            [
                {
                    "title": "기사",
                    "snippet": "본문",
                    "url": (
                        "https://duckduckgo.com/l/?uddg="
                        "https%3A%2F%2Fexample.test%2Farticle%3Fa%3D1"
                    ),
                }
            ],
            1,
        )

        self.assertEqual(
            ranked[0]["url"],
            "https://example.test/article?a=1",
        )

    def test_research_keeps_only_complete_scoped_numeric_facts(self) -> None:
        research = distill_research(
            _ResearchLlm(),
            "성경김",
            {
                "queries": ["성경김"],
                "results": [
                    {
                        "title": "기사",
                        "snippet": (
                            "2023년 대한민국 조미김 수출액은 4억 달러로, "
                            "2022년 2억9400만 달러보다 36% 증가했다."
                        ),
                        "url": "https://example.test/article",
                    }
                ],
                "backend": "fixture",
            },
        )

        self.assertEqual(len(research["quantified_facts"]), 1)
        fact = research["quantified_facts"][0]
        self.assertEqual(fact["subject"], "대한민국 조미김 수출")
        self.assertEqual(fact["period"], "2023년")
        self.assertIn("출처·범위가 정리된 수치", research["brief_text"])
        self.assertIn("2023년", research["brief_text"])
        self.assertNotIn(
            "조미김 수출액은 2023년 4억 달러였다",
            research["hard_facts"],
        )
        self.assertTrue(
            any(
                "구조화된 수치 팩트가 아닌" in value
                for value in research["avoid_claims"]
            )
        )

    def test_research_rejects_forecast_as_realized_performance(self) -> None:
        research = distill_research(
            _ProjectedResearchLlm(),
            "성경김 미국 수출",
            {
                "queries": ["성경김 미국 수출"],
                "results": [
                    {
                        "title": "미국 수출 전망",
                        "snippet": "2024년 미국 수출액 2600만 달러 전망",
                        "url": "https://example.test/forecast",
                    }
                ],
                "backend": "fixture",
            },
        )

        self.assertEqual(research["quantified_facts"], [])

    def test_research_retries_once_after_truncated_json(self) -> None:
        llm = _RetryResearchLlm()
        research = distill_research(
            llm,
            "성경김",
            {
                "queries": ["성경김"],
                "results": [
                    {
                        "title": "독도 표기 고수",
                        "snippet": "성경식품은 독도 표기를 고수했다.",
                        "url": "https://example.test/article",
                    }
                ],
                "backend": "fixture",
            },
        )

        self.assertEqual(llm.calls, 2)
        self.assertIn(
            "성경식품은 독도 표기를 고수했다.",
            research["hard_facts"],
        )

    def test_factcheck_flags_numeric_claim_without_absolute_year(self) -> None:
        script = {
            "title": "fixture",
            "hook": "fixture",
            "segments": [
                {
                    "index": 0,
                    "text": "조미김 수출액도 4억 달러로 1년 만에 36% 뛰었다고",
                    "intent": "마무리",
                }
            ],
        }
        with patch(
            "shortly.generate.factcheck._extra_fact_search",
            return_value=[],
        ):
            result = factcheck_script(
                LLMClient(),
                script,
                research=None,
                force=True,
            )

        self.assertTrue(
            any("절대 기준연도" in issue for issue in result["issues"])
        )

    def test_factcheck_repairs_named_segment_outside_revision_scope(
        self,
    ) -> None:
        original = {
            "title": "원래 제목",
            "hook": "원래 제목",
            "segments": [
                {"index": 1, "text": "안전한 훅", "intent": "hook"},
                {
                    "index": 2,
                    "text": "근거 없는 매출 비중 주장",
                    "intent": "conflict",
                },
                {"index": 3, "text": "사용자가 바꾼 결말", "intent": "ending"},
            ],
            "_source": "llm",
        }
        candidate = {
            "title": "팩트체커가 바꾼 제목",
            "hook": "팩트체커가 바꾼 훅",
            "segments": [
                {"index": 1, "text": "다시 쓴 훅", "intent": "hook"},
                {
                    "index": 2,
                    "text": "확인된 골격만 남긴 문장",
                    "intent": "conflict",
                },
                {"index": 3, "text": "다시 쓴 결말", "intent": "ending"},
            ],
            "_source": "llm+factcheck",
        }

        repaired = apply_factcheck_repairs(
            original,
            candidate,
            issues=["세그먼트 2의 매출 비중은 근거가 약함"],
            fixes=["세그먼트 2: 확인된 골격으로 완화"],
        )

        self.assertEqual(repaired["title"], "원래 제목")
        self.assertEqual(repaired["segments"][0]["text"], "안전한 훅")
        self.assertEqual(
            repaired["segments"][1]["text"],
            "확인된 골격만 남긴 문장",
        )
        self.assertEqual(
            repaired["segments"][2]["text"],
            "사용자가 바꾼 결말",
        )

    def test_factcheck_segment_references_are_one_based_positions(self) -> None:
        original = {
            "segments": [
                {"index": 0, "text": "첫 문장"},
                {"index": 1, "text": "둘째 문장"},
            ]
        }
        candidate = {
            "segments": [
                {"index": 0, "text": "교정된 첫 문장"},
                {"index": 1, "text": "불필요하게 바뀐 둘째 문장"},
            ]
        }

        repaired = apply_factcheck_repairs(
            original,
            candidate,
            fixes=["문장1: 사실 오류 교정"],
        )

        self.assertEqual(repaired["segments"][0]["text"], "교정된 첫 문장")
        self.assertEqual(repaired["segments"][1]["text"], "둘째 문장")

    def test_factcheck_rejects_unrequested_structure_change(self) -> None:
        original = {
            "segments": [
                {"index": 1, "text": "첫 문장"},
                {"index": 2, "text": "둘째 문장"},
            ]
        }
        candidate = {
            "segments": [
                {"index": 1, "text": "하나로 합친 문장"},
            ]
        }

        repaired = apply_factcheck_repairs(
            original,
            candidate,
            fixes=["세그먼트 1: 사실 오류 교정"],
        )

        self.assertEqual(repaired["segments"], original["segments"])
        self.assertEqual(
            repaired["_factcheck_rejected"],
            "segment_count_changed",
        )

    def test_revision_research_excludes_similar_story_for_another_subject(
        self,
    ) -> None:
        raw = {
            "queries": ["성경김 독도 일본 수출"],
            "results": [
                {
                    "title": "독도 못 빼 일본 포기한 쌀과자",
                    "snippet": "전남 장성 유아용 쌀과자 업체 이야기다.",
                    "url": "https://example.test/rice-snack",
                },
            ],
            "backend": "fixture",
        }
        with patch(
            "shortly.generate.research.collect_raw",
            return_value=raw,
        ):
            research = research_topic(
                "성경김 독도 일본 수출",
                llm=_ResearchLlm(),
                subject_anchors=["성경김"],
                seed_results=[
                    {
                        "title": "독도 표기 고수한 성경김",
                        "snippet": "지도표 성경김은 해외에서도 판매된다.",
                        "url": "https://example.test/article",
                    }
                ],
            )

        self.assertEqual(len(research["results"]), 1)
        self.assertIn("성경김", research["results"][0]["title"])
        self.assertEqual(
            research["subject_filter"]["excluded_results"],
            1,
        )

    def test_query_relevance_rejects_generic_country_hit(self) -> None:
        row = {
            "title": "경동나비엔 해외 사업",
            "snippet": "미국과 여러 국가에서 매출이 늘었다.",
        }

        self.assertFalse(
            result_relevant_to_query(
                row,
                "러시아 보따리상 1993년 수출 2003년 법인 2006년 공장 연혁",
            )
        )

    def test_collection_keeps_two_candidates_per_query_when_focus_is_large(self) -> None:
        def fake_search(query: str, max_results: int = 3):
            return [
                {
                    "title": f"{query} 결과 {index}",
                    "snippet": query,
                    "url": f"https://example.test/{abs(hash(query))}/{index}",
                    "query": query,
                    "source_tier": "general",
                    "source_score": 2,
                }
                for index in range(max_results)
            ]

        with patch("shortly.generate.research.web_search", side_effect=fake_search):
            raw = collect_raw(
                "오리온 초코파이 러시아 성공",
                max_results=8,
                max_queries=2,
                subject_anchors=["오리온 초코파이"],
                research_focus="차와 케이크 문화\n현지 생산 연혁\n베리잼 현지화",
                required_content="러시아 판매량 16억 개\n전 세계 판매량의 40%",
            )

        counts = {
            query: sum(row["query"] == query for row in raw["results"])
            for query in raw["queries"]
        }
        self.assertTrue(all(count >= 2 for count in counts.values()))

    def test_factcheck_retries_invalid_json_once(self) -> None:
        class RetryLlm(LLMClient):
            available = True

            def __init__(self):
                self.calls = 0

            def complete_json(self, *args, **kwargs):
                self.calls += 1
                if self.calls == 1:
                    raise LLMResponseParseError("truncated")
                return {
                    "changed": False,
                    "issues": [],
                    "fixes": [],
                    "script": {
                        "title": "제목",
                        "hook": "제목",
                        "segments": [{"index": 1, "text": "검증된 문장", "intent": "hook"}],
                    },
                }

        llm = RetryLlm()
        with patch("shortly.generate.factcheck._extra_fact_search", return_value=[]):
            result = factcheck_script(
                llm,
                {
                    "title": "제목",
                    "hook": "제목",
                    "segments": [{"index": 1, "text": "검증된 문장", "intent": "hook"}],
                },
                research={"results": [{"title": "근거", "snippet": "검증된 문장"}]},
                topic="검증 주제",
                force=True,
            )

        self.assertEqual(llm.calls, 2)
        self.assertFalse(result["failed"])


if __name__ == "__main__":
    unittest.main()
