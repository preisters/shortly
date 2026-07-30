from __future__ import annotations

import unittest
from unittest.mock import patch

from shortly.generate.factcheck import factcheck_script
from shortly.generate.research import distill_research, research_topic
from shortly.llm.base import LLMClient


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


class FactSafetyTests(unittest.TestCase):
    def test_research_keeps_only_complete_scoped_numeric_facts(self) -> None:
        research = distill_research(
            _ResearchLlm(),
            "성경김",
            {
                "queries": ["성경김"],
                "results": [
                    {
                        "title": "기사",
                        "snippet": "2023년 대한민국 조미김 수출액",
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

    def test_revision_research_excludes_similar_story_for_another_subject(
        self,
    ) -> None:
        raw = {
            "queries": ["성경김 독도 일본 수출"],
            "results": [
                {
                    "title": "독도 표기 고수한 성경김",
                    "snippet": "지도표 성경김은 포장지 표기를 유지했다.",
                    "url": "https://example.test/article",
                },
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
            )

        self.assertEqual(len(research["results"]), 1)
        self.assertIn("성경김", research["results"][0]["title"])
        self.assertEqual(
            research["subject_filter"]["excluded_results"],
            1,
        )


if __name__ == "__main__":
    unittest.main()
