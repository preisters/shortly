"""Regression coverage for the October story-vs-advertising editorial correction."""
import copy
import unittest

from shortly.generate.script_gen import _SYSTEM_PROMPT, _FLOW_FACT_REMINDER, _style_context, _prefer_hook_line
from shortly.generate.editorial import review_story
from shortly.generate.image_reco import _recommend_llm


class Capture:
    available = True

    def complete_json(self, system, user, **kwargs):
        self.system, self.user = system, user
        return {}


class StoryReworkTests(unittest.TestCase):
    def test_statistics_are_observations_not_forced_length(self):
        profile = {'stats': {'avg_duration_sec': 30, 'dialogue_stats': {
            'hook_char_len': {'avg': 12, 'min': 6, 'max': 15},
            'char_count_no_space': {'min': 100, 'max': 200, 'avg': 150}}}}
        original = copy.deepcopy(profile)
        text = _style_context(profile)
        self.assertEqual(profile, original)
        self.assertIn('관찰 평균 12자', text)
        self.assertIn('평균 분량·문장 수는 강제하지 않는다', text)
        self.assertNotIn('설명형·질문형 제목 금지', text)
        self.assertNotIn('최소 6~최대 15', text)

    def test_title_selection_does_not_erase_curiosity_words_or_choose_shortest(self):
        self.assertEqual(_prefer_hook_line('맥심이 수출을 못 하는 이유', '커피의 반전'), '맥심이 수출을 못 하는 이유')
        self.assertEqual(_prefer_hook_line('공장에 따라 맛이 다른 에이스?', '에이스의 비밀'), '공장에 따라 맛이 다른 에이스?')
        self.assertEqual(_prefer_hook_line('', '새 훅'), '새 훅')

    def test_story_prompt_does_not_force_a_verdict_after_resolution(self):
        self.assertIn('상품 추천·구매 설득형 레퍼런스', _SYSTEM_PROMPT)
        self.assertIn('별도의 총평·교훈·최종 판정을 덧붙이지 마라', _SYSTEM_PROMPT)
        self.assertIn('이해 전제까지 삭제', _SYSTEM_PROMPT)
        self.assertNotIn('단정적인 최종 판정이어야 한다', _SYSTEM_PROMPT)

    def test_reviewer_requires_specific_progression_and_payoff_evidence(self):
        llm = Capture()
        review_story(llm, {'title': '맥심', 'segments': []}, '', '', '', {})
        self.assertIn('앞뒤 구절과 새로 공개된 행동/답을 특정', llm.system)
        self.assertIn('의무적인 총평·교훈·최종 판정을 요구하지', llm.system)

    def test_real_subject_reference_applies_to_unbranded_food_too(self):
        llm = Capture()
        segment = {'index': 0, 'narration': '얇은 네 겹이 바삭함', 'cuts': 1}
        _recommend_llm(llm, {}, {}, {'segments': [segment]})
        self.assertIn('브랜드뿐 아니라 주제인 음식·식재료·사물', llm.user)
        self.assertIn('참조 입력이 아니다', llm.user)
        self.assertIn('참조를 제공할 수 없는 자동 생성 경로', llm.user)

    def test_origin_story_connects_to_present_without_arbitrary_restrictions(self):
        self.assertIn('의미 없는 범위 수식어', _FLOW_FACT_REMINDER)
        self.assertIn('확인된 보급·제품군 확장 경로', _FLOW_FACT_REMINDER)
        self.assertIn('같은 레시피·제품의 직접 변신', _FLOW_FACT_REMINDER)

    def test_soda_video_requires_temporal_behavior_not_static_bubble_decoration(self):
        llm = Capture()
        _recommend_llm(llm, {}, {}, {'segments': [{'index': 0, 'narration': '웰치스 포도 탄산', 'cuts': 1}]})
        self.assertIn('기포가 아래에서 위로 올라가 수면에서 사라지고', llm.user)
        self.assertIn('실물 사진을 추천', llm.user)


if __name__ == '__main__':
    unittest.main()
