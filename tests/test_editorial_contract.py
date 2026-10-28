from __future__ import annotations

import copy
import unittest
from unittest.mock import patch

from shortly.generate.editorial import (
    parse_items, preserve_title, explicit_title, duration_check, duration_target, review_story,
)
from shortly.generate.script_gen import (
    analyze_story_flow, analyze_required_content_coverage, generate_script, polish_story_flow,
)
from shortly.generate.research import (
    parse_required_content, parse_research_focus, rank_search_results, row_queries, build_queries, research_topic,
)
from shortly.generate.guide import build_guide
from shortly.service import _finalize_story_quality


class Replies:
    available = True

    def __init__(self, *replies):
        self.replies = list(replies)
        self.calls = []

    def complete_json(self, system, user, **kwargs):
        self.calls.append((system, user))
        if not self.replies:
            raise AssertionError("Unbounded or unexpected model call")
        return copy.deepcopy(self.replies.pop(0))


def script():
    return {"title": "작은 빵집", "hook": "작은 빵집", "_source": "llm", "segments": [
        {"index": i, "text": t} for i, t in enumerate([
            "빵집은 매일 반죽을 만들었음.", "뜨거운 오븐에 반죽을 구웠음.",
            "완성된 빵을 식혔음.", "동네 손님들이 빵집을 찾았음."])]}


def review(passed=True):
    return {"dimensions": {key: {"passed": passed, "reason": "제목과 마지막 장면의 연결 확인"}
            for key in ("intent", "hook", "progression", "payoff", "economy")}}


class EditorialTests(unittest.TestCase):
    def test_all_items_and_full_text_preserved(self):
        value = '\n'.join(f'{i}. ' + str(i) + '내용' * 150 for i in range(1, 14))
        self.assertEqual(len(parse_items(value)), 13)
        self.assertEqual(parse_items(value), parse_required_content(value))
        self.assertEqual(parse_items(value), parse_research_focus(value))
        self.assertGreater(len(parse_items(value)[0]), 240)

    def test_every_explicit_query_retained(self):
        focus = '\n'.join(f'조사 질문 {i}' for i in range(13))
        required = '\n'.join(f'필수 장면 {i}' for i in range(9))
        queries = build_queries('빵집 이야기', research_focus=focus, required_content=required)
        for item in parse_items(focus) + parse_items(required):
            self.assertIn(item, queries)

    def test_optional_absence_not_blocking(self):
        result = analyze_required_content_coverage(script(), '분량이 허용된다면 공장 확장 기록 추가', {})
        self.assertTrue(result['passed'])
        self.assertEqual(result['items'][0]['status'], 'optional_omitted')

    def test_missing_evidence_not_treated_as_verified(self):
        result = analyze_required_content_coverage(script(), '동네 손님들이 빵집을 찾았음', {})
        self.assertFalse(result['items'][0]['supported'])

    def test_fixed_title_overrides_weak_word_heuristic(self):
        source = "영상 제목은 반드시 ‘아무도 믿지 않는 커피우유의 진실’로 정확히 유지한다."
        self.assertEqual(preserve_title(script(), source)['title'], '아무도 믿지 않는 커피우유의 진실')
        self.assertEqual(explicit_title("예시로 '다른 제목'을 참고"), '')

    def test_later_title_assignment_wins(self):
        result = preserve_title(script(), "제목은 '이전 제목'", "제목을 '새로운 진실'로 바꿔")
        self.assertEqual(result['hook'], '새로운 진실')

    def test_unquoted_title_revision_not_reverted_to_root(self):
        result = preserve_title(script(), "제목은 '이전 제목'", "제목을 빵집에 맞게 바꿔")
        self.assertEqual(result['title'], script()['title'])

    def test_cooking_time_not_mistaken_for_video_duration(self):
        self.assertIsNone(duration_target('제품을 20~30초 데운다'))
        self.assertEqual(duration_target('전체 영상은 38~42초. 제품은 20~30초 데운다'), (38, 42))

    def test_generation_preserves_title_and_passes_full_intent(self):
        source = "제목은 '빵집의 진실'. 첫 대사는 빵집으로 시작. 끝에는 손님. 38~42초."
        llm = Replies(script())
        with patch('shortly.generate.script_gen.polish_story_flow', side_effect=lambda l,p,s,**k:s) as polish:
            result = generate_script(llm, {}, source, required_content='끝에 손님이 방문')
        self.assertEqual(result['title'], '빵집의 진실')
        self.assertEqual(polish.call_args.kwargs['source_text'], source)
        self.assertEqual(polish.call_args.kwargs['required_content'], '끝에 손님이 방문')

    def test_four_or_ten_beats_not_automatically_wrong(self):
        self.assertFalse(any('비트가 너무' in i for i in analyze_story_flow(script())))
        value = script(); value['segments'] = [{'text': f'서로 다른 사건 {i}번임'} for i in range(10)]
        self.assertFalse(any('비트가 너무' in i for i in analyze_story_flow(value)))

    def test_duration_matches_guide(self):
        result = duration_check(script(), {}, '영상은 38~42초 안팎')
        self.assertEqual(result['estimated_sec'], build_guide({}, script())['total_duration_sec'])
        self.assertTrue(result['issues'])

    def test_semantic_review_rejects_unrelated_draft(self):
        llm = Replies(review(False))
        result = review_story(llm, script(), '불닭 리콜 이야기', '', '', {})
        self.assertFalse(result['passed'])
        self.assertEqual(len(result['issues']), 5)

    def test_malformed_review_cannot_pass(self):
        for data in ({'passed': True}, {'dimensions': {}}, {'dimensions': {'intent': {'passed': 'true'}}}):
            self.assertFalse(review_story(Replies(data), script(), '', '', '', {})['passed'])

    def test_no_semantic_review_cannot_pass_gate(self):
        llm = Replies(); llm.available = False
        _, quality = _finalize_story_quality(llm, {}, script(), {
            'hard_facts': ['근거'], 'results': [{'source_tier':'authoritative'}, {'source_tier':'editorial'}]})
        self.assertFalse(quality['passed'])
        self.assertEqual(quality['editorial_review']['status'], 'unavailable')

    def test_semantic_pass_required_for_quality_pass(self):
        llm = Replies(review())
        _, quality = _finalize_story_quality(llm, {}, script(), {
            'hard_facts': ['근거'], 'results': [{'source_tier':'authoritative'}, {'source_tier':'editorial'}]})
        self.assertTrue(quality['passed'])
        self.assertEqual(len(llm.calls), 1)

    def test_semantic_repair_rechecks_and_stops(self):
        edited = script(); edited['segments'][-1]['text'] = '완성된 빵은 동네 손님들의 아침이 됐음.'
        llm = Replies(review(False), edited, review(False))
        original = script()
        result, quality = _finalize_story_quality(llm, {}, original, {'hard_facts':['근거']}, source_text='빵집 이야기')
        self.assertEqual(len(llm.calls), 3)
        self.assertFalse(quality['passed'])
        self.assertEqual(original, script())
        self.assertIn('빵집 이야기', llm.calls[1][1])

    def test_natural_connector_change_can_be_accepted(self):
        edited = script(); edited['segments'][2]['text'] = '그래서 완성된 빵을 식혔음.'
        llm = Replies(edited, {'same_facts': True, 'reason': '동일한 조리 순서를 연결함'})
        result = polish_story_flow(llm, {}, script(), force=True)
        self.assertNotIn('_flow_rejected', result)
        self.assertEqual(result['segments'], edited['segments'])

    def test_new_causal_claim_still_rejected(self):
        edited = script(); edited['segments'][-1]['text'] = '그래서 전국적으로 성공했음.'
        llm = Replies(edited, {'same_facts': False, 'reason': '전국 성공 인과 새로 추가'})
        result = polish_story_flow(llm, {}, script(), force=True)
        self.assertEqual(result['_flow_rejected'], 'causal_claims_added')

    def test_optional_numeric_omission_can_be_accepted(self):
        original = script(); original['segments'][2]['text'] += ' 직원은 3명임.'
        llm = Replies(script(), {'same_facts': True, 'reason': '부차적인 인원 전체 생략, 다른 수치 범위 변화 없음'})
        result = polish_story_flow(llm, {}, original, force=True)
        self.assertNotIn('_flow_rejected', result)

    def test_shared_source_keeps_query_provenance(self):
        rows = [{'url':'https://example.com/article', 'title':'기사', 'snippet':'자료', 'query':q}
                for q in ('첫 질문', '마지막 질문')]
        result = rank_search_results(rows, 4)
        self.assertEqual(len(result), 1)
        self.assertEqual(set(row_queries(result[0])), {'첫 질문', '마지막 질문'})

    def test_late_explicit_research_sources_survive_ranking_and_subject_filter(self):
        queries = [f'질문 {i}' for i in range(20)]
        raw = {'results': [{'url': f'https://example.com/article/{i}', 'title': f'문화 자료 {i}',
                           'snippet': f'문화 질문 {i}', 'query': q} for i, q in enumerate(queries)],
               'required_content': queries, 'required_content_queries': queries}
        with patch('shortly.generate.research.collect_raw', return_value=raw), \
             patch('shortly.generate.research.result_relevant_to_query', return_value=True), \
             patch('shortly.generate.research.distill_research', side_effect=lambda llm,topic,data:data):
            result = research_topic('중심 제품', subject_anchors=['브랜드'], max_results=3)
        self.assertEqual(len(result['results']), 20)
        self.assertTrue(all(row['result_count'] for row in result['required_content_coverage']))

    def test_guide_notes_do_not_reintroduce_old_rules(self):
        profile = {'recipe': {'do':['사실, 하지만 등 연결어로 문장 전환','화면 78% 이상 텍스트 오버레이 배치'],
                             'avoid':['문장을 딱 떨어지게 끝맺기'], 'captions':'화면 78% 이상', 'transitions':'667회'}}
        original = copy.deepcopy(profile)
        notes = build_guide(profile, script())['global_notes']
        self.assertNotIn('78%', str(notes)); self.assertNotIn('667회', str(notes))
        self.assertNotIn('문장을 딱 떨어지게 끝맺기', str(notes))
        self.assertEqual(profile, original)


if __name__ == '__main__':
    unittest.main()
