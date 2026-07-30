from __future__ import annotations

import unittest
from unittest.mock import patch

from shortly.generate.factcheck import (
    apply_factcheck_repairs,
    factcheck_script,
)
from shortly.generate.research import (
    build_queries,
    collect_raw,
    distill_research,
    rank_search_results,
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


class FactSafetyTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
