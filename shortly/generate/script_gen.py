"""주제→스타일 반영 대본 생성 / 대본 입력 시 스타일 보정."""

from __future__ import annotations

import copy
import json
import re
from collections import Counter
from typing import Dict, List, Optional

from ..llm.base import LLMClient, LLMError, LLMQuotaExceeded
from ..utils import log
from .editorial import (
    WHOLE_VIDEO_REVIEW_GUIDANCE, parse_items, optional_item, preserve_title, flow_change_supported,
)


def _split_sentences(text: str) -> List[str]:
    parts = re.split(r"(?<=[.!?。！？\n])\s+", text.strip())
    return [p.strip() for p in parts if p.strip()]


def _chars_no_space(text: str) -> int:
    return len(re.sub(r"\s", "", text or ""))


def _heuristic_script(profile: Dict, text: str, is_script: bool) -> Dict:
    if is_script:
        sentences = _split_sentences(text)
        hook = sentences[0] if sentences else text[:30]
    else:
        # A failed provider call must never attach an unrelated learned hook to
        # the user's topic. The service quality gate marks this fallback as not
        # publishable, but it still needs to remain recognizably on-topic.
        first = re.split(r"[.。!?\n]", text.strip())[0].strip()
        hook = first[:36] or "대본 생성 확인 필요"
        sentences = _split_sentences(text) or [text.strip()]
    segments = [{"index": i, "text": s} for i, s in enumerate(sentences)]
    return {
        "title": hook,
        "hook": hook,
        "segments": segments,
        "_source": "heuristic",
    }


_SYSTEM_PROMPT = (
    "사용자가 명시한 제목·도입·결말·수정 범위·목표 분량은 아래 일반 편집 취향보다 우선한다. "
    "제목에 '진실/정체/이유'가 있더라도 사용자가 지정했다면 그대로 유지한다. "
    "고정 분량 안에서 필수 내용을 먼저 배치하고 선택 정보는 생략한다. "
    "너는 유튜브 쇼츠 대본 작가다. 아래 채널 자료에서는 훅의 온도·나레이션 말투·어미·"
    "전체 속도만 재현해 대본을 만든다. 기존 영상의 사건 배열, 연결어 위치, 문장 골격, "
    "세그먼트 수는 채널 스타일이 아니므로 모방하지 마라. "
    "상품 추천·구매 설득형 레퍼런스를 사건·유래 썰의 전개 기준으로 삼지 마라. "
    "썰형 레퍼런스에서는 구체적인 의문이 생기고 새 행동·반응으로 바뀌고 해소되는 기능만 참고하며 "
    "대사·사건·줄거리는 복제하지 않는다. "
    "채널 스타일은 훅·말투·속도 같은 표면 스타일에만 적용하고 "
    "이야기 구조·논리·가독성은 아래 품질 원칙을 우선한다. 목표는 도파민·호기심을 "
    "터뜨려 시청자가 끝까지 보게 만드는 것이다. "
    "각 세그먼트는 자막 조각이 아니라 자연스럽게 낭독되는 하나의 생각 단위로 쓴다. 한 문장을 "
    "기계적으로 여러 비트에 찢지 말고, 자막 분할은 후속 편집 단계에 맡긴다.\n"
    "가장 중요(이야기 구조): 먼저 소재에서 가장 강한 인과 축을 하나 고른 뒤 그 축에 필요한 "
    "정보만 공개한다. 훅·정체·상황·대치·선택·대가·보상은 사용할 수 있는 후보이지 채워야 할 "
    "고정 슬롯이 아니다. 소재에 맞지 않거나 약한 단계는 과감히 합치거나 생략한다. 각 세그먼트는 앞 문장에 없던 "
    "새 정보나 감정 변화를 하나씩 추가해야 한다. 인접 세그먼트에서 같은 사실을 표현만 바꿔 "
    "반복하지 마라. 장애물을 제시한 다음 세그먼트는 그 장애물을 실제로 뚫은 방법, 제품에 생긴 "
    "효과, 검증된 결과 중 하나를 답해야 한다. '그런데 그 제품이 판을 뒤집었다'처럼 앞의 역설만 "
    "다시 말하는 연결 문장을 독립 세그먼트로 두지 마라. 마지막은 훅에서 약속한 궁금증을 주인공 회사·제품의 가장 강한 검증 사실 "
    "하나로 회수한다. 사용자가 비교를 요청하지 않았다면 비슷한 사건의 다른 회사, 주변 인물, "
    "중심 사건과 무관한 후속 캠페인 같은 곁가지를 새 결말로 삼지 마라. 사용자 필수 장면이나 "
    "중심 사건의 직접적인 결과인 복귀 행사 등은 곁가지로 취급하지 않는다. 요청한 결말 방향의 근거가 부족하면 "
    "다른 주체의 사실로 바꾸지 말고, 주인공에게 확인된 가장 가까운 결과만 정직하게 써라.\n"
    "마지막 문장은 남아 있던 의문을 실제 행동·반응·결과로 해소하고 자연스럽게 닫는다. "
    "이미 그 장면에서 이야기가 끝났다면 별도의 총평·교훈·최종 판정을 덧붙이지 마라. "
    "모든 소재에 성공·위상 상승·반전을 강제하거나 '~된 거임'으로 통일하지 않는다. "
    "실제로 미해결인 사건은 그 미해결 자체를 정확히 밝혀 닫을 수 있지만, 답이 있는 사건을 "
    "막연한 가능성이나 시청자 질문으로 회피하지 않는다.\n"
    "사용자가 제목·주제·추가 조사 항목에서 특정 장면이나 문화 현상을 지정했다면 그 장면은 훅에서 "
    "명확히 보여주고 결말에서 반드시 회수하라. 다만 장면 자체의 모양·제작법·장식을 필요 이상으로 "
    "설명해 소재를 잃지 마라. 제품이 어떻게 불리한 시장 조건을 깨고 그 문화 현상까지 도달했는지가 "
    "더 강한 이야기라면, 검증된 시장 장벽·해결 행동·성과를 중간의 주된 도파민 축으로 사용한다. "
    "고정된 비트 비율이나 분량 할당은 두지 말고, 제목의 약속과 그 약속을 놀랍게 만드는 원인·대가·성과를 "
    "하나의 변환 서사로 연결하라.\n"
    "사실이라고 해서 모두 대본에 넣을 가치가 있는 것은 아니다. 회사가 화제 장면을 마케팅에 "
    "활용했다는 후속 정보, 관계자 코멘트, 기념 캠페인은 단어가 아니라 중심 사건과의 관련성으로 "
    "판단한다. 검증된 사실 안에서 사용자 지정 필수 결말 > 중심 사건의 직접적인 결과 > 일반 편집 취향 "
    "순으로 우선한다. 사용자가 지정했거나 훅을 회수하는 실제 결과인 행사를 임의 삭제하지 마라. "
    "그 밖의 무관한 기업 메타정보만 제외한다. 결말은 시청자의 처음 궁금증을 해소한다. "
    "주인공의 위상 변화나 성공이 없는 소재에 그런 결과를 만들지 않는다.\n"
    "결말은 출처를 설명하는 보고서나 의무적인 반전 한 방이 아니다. 훅에 나온 "
    "결과를 같은 말로 반복하지 말고, 확인된 생활 장면·사람의 반응·구체적인 후속 결과 중 하나로 "
    "그 결과의 의미를 한 단계 확장하라. "
    "'(몇 년 보도 기준)', '기사에 따르면' 같은 편집자 메모·괄호형 면책 문구를 음성 대사에 "
    "넣지 마라. 연도는 출시·계약·리뉴얼·기록 변화·통계 기준처럼 그 시점이 사실의 의미나 "
    "전개 순서를 바꿀 때만 말한다. 단순히 기사가 나온 해, 누군가 비교·확인한 해처럼 연도를 "
    "빼도 핵심 사실이 그대로인 경우에는 조사 메모에만 보존하고 음성 대사에서는 삭제한다. "
    "예를 들어 '2022년에 비교해 보니 같은 레시피였다'는 해당 연도에만 같았거나 그해 변경된 "
    "사실이 확인되지 않았다면 '비교해 보니 같은 레시피였다'로 쓴다. 서로 다른 검증 사실을 "
    "함께 쓸 때는 지역·주체·인과를 합치지 말고, "
    "대치와 결과의 대비만 구어체로 강하게 연결하라. 수출국 확대·해외 매출 성장처럼 "
    "상승 방향이 검증됐다면 숫자를 보고서처럼 나열하지 말고 '주문이 폭발했다', "
    "'없어서 못 팔 지경' 같은 쇼츠식 결과 이미지로 압축해도 된다. 이는 감정적 과장이지 "
    "재고 품절의 사실 단정이 아니어야 하며, 특정 국가의 품절·판매 순위는 근거 없이는 만들지 마라.\n"
    "가장 중요(말투): [실제 대사 예시]와 [자주 쓰는 문장 끝맺음]은 참고하되 오류·잘린 문장·"
    "불필요한 반복은 모방하지 마라. "
    "이 채널은 '썰 풀어주는' 반말 나레이션체다. '~다고 함', '~했는데', '~다는데', '~라고' 같은 "
    "실제 어미를 사용하고, '~입니다', '~습니다', '~해요', '~하죠', '~됩니다' 같은 정중한 리뷰/설명체는 "
    "피한다. 연결어는 논리 관계가 실제로 바뀔 때만 쓰고 '근데/그래서/하지만/그리고/사실'을 "
    "리듬용으로 강제하지 마라. '근데/하지만'은 앞 문장의 방향을 실제로 뒤집을 때만 쓰고, "
    "품절 뒤 해외 성공처럼 같은 방향의 결과를 더 쌓을 때는 '그리고'를 쓰거나 연결어를 생략한다. "
    "쉼표로 세 가지 이상 정보를 한 문장에 몰아넣지 마라. "
    "[실제 대사 예시]에 서술자 질문형이 반복되지 않으면 '망할 줄 알았지?', '궁금하지?' 같은 "
    "수사적 질문을 새로 만들지 말고 선언형 썰 문장으로 밀어붙인다. "
    "사용자가 명시한 분량만 제약으로 적용한다. 학습된 평균 글자수·문장 수·길이는 관찰값이며 "
    "필수 전제·행동·후반의 흥미를 지우거나 군더더기로 채우는 기준이 아니다.\n"
    "강조 부사는 의미에 맞을 때만 쓴다. '하필'은 원치 않은 선택·불운한 우연·부정적인 결과가 "
    "겹치는 상황에만 허용하고, 단순한 의외성·긍정적 성취·놀라운 정체 공개에는 쓰지 마라. "
    "규모·위상·긍정적 놀라움을 강조할 때는 문맥상 자연스러우면 '무려'를 쓸 수 있지만, 이것도 "
    "리듬을 채우려고 넣지 마라. '하필·무려·급기야·심지어' 같은 강조어는 각각 한 대본에서 "
    "한 번을 넘기지 말고, 의미가 없어도 문장은 자연스러우면 아예 생략하라.\n"
    "가장 중요(제목/훅): title 과 hook 은 같은 한 줄로 써라. 제목 판단 순서는 "
    "1) 실제 사건과 동사의 의미 적합성, 2) 시청자의 궁금증, 3) 채널의 자극 온도다. "
    "[훅 예시]는 길이·압축감·자극의 온도를 보여주는 참고 사례일 뿐, 예시의 동사나 "
    "문장 구조를 반드시 쓰라는 뜻이 아니다. 소재에 맞지 않으면 과감히 버려라. "
    "친숙한 음식/브랜드와 구체적으로 이상한 일을 짧고 분명하게 연결한다. "
    "질문형이나 '이유/진실/정체'라는 단어만으로 제목을 배제하지 않는다. "
    "그 말밖에 없어 무엇이 이상한지 모호하거나 원인과 결과까지 전부 알려 볼 이유가 없으면 다시 쓴다. "
    "'털려버린', '기강 잡아버린', '해킹한', '큰일났다는' 같은 예시 동사는 주어와 목적어의 "
    "실제 관계가 자연스러울 때만 사용할 수 있다. 사건을 설명하지 못하는 억지 동사는 자극적이어도 "
    "금지한다. 가장 강한 검증 사실을 소재에 맞는 동사로 압축하되 대상과 관계가 이해될 만큼 남긴다. "
    "학습된 훅 글자 수는 강제하지 않는다. "
    "훅 직후 본문을 '사실,' 로 시작하지 마라. 바로 장면·갈등·디테일로 들어가라.\n"
    "사용자가 시작 문구나 특정 대사를 지정하면 의미·핵심 표현·배치 의도는 보존하되, '그대로', "
    "'한 글자도 바꾸지 말고'처럼 축자 보존을 명시하지 않은 이상 조사·팩트와 충돌하지 않는 "
    "조사·어미·띄어쓰기 오류는 자연스럽게 바로잡아라.\n"
    "가장 중요(환각 금지 ≠ 재미 삭제): 하드 팩트/드라마 비트는 [사용자 입력]과 [웹 조사 메모]에 "
    "있는 것만 사건으로 쓴다. 확인된 갈등(예: 일본 측이 독도 표기 수정을 요구·요청 → 회사가 거부·고수 "
    "→ 일본 수출/시장 포기)이 조사에 있으면 그걸 쇼츠의 한 방으로 살리고, "
    "'수출이 어려워져서 포기했다'처럼 수동·요약으로 죽이지 마라. "
    "금지하는 건 없는 사건 창작과 주체 뒤집기·'인정하라' 창작이지, "
    "스니펫에 있는 요구→거절 서사를 빼는 것이 아니다. "
    "세부 대화문·금액·날짜가 없으면 그 세부만 생략하고 갈등 골격은 유지하라. "
    "소프트 과장과 [소프트 각도]는 시각 분위기·관심도 참고에만 허용한다. 그 안의 소품·장소·상황을 "
    "나레이션 속 사실이나 새 사건으로 승격하지 마라. 하드 팩트나 사용자 입력에 없는 선물 상자·행사 "
    "장면 같은 구체물을 대사로 만들지 마라.\n"
    "사용자가 직접 확인한 온라인 개인 일화를 명시적으로 사용 승인한 경우에는 원문 주소를 다시 "
    "확보하지 못했더라도 '한 네티즌은 ...고 함'처럼 개인 회상에 귀속한 소프트 일화로 사용할 수 "
    "있다. 단, 이를 일반 관습·통계·검증된 사실로 확대하거나 사용자가 주지 않은 가족관계·시점·국적을 "
    "덧붙이지 마라. 중심 소재의 국가와 충돌하는 국적을 그대로 옮기지도 마라.\n"
    "가장 중요(수치 신뢰성): 매출·수출액·비율·증가율·가격·인원 등 숫자는 "
    "[출처·범위가 정리된 수치]에 주체·범위·기준연도·단위·출처가 모두 정리된 항목만 사용한다. "
    "숫자를 쓸 때는 전국/지역, 전체 품목/세부 품목, 업계 전체/특정 회사 중 무엇인지와 "
    "기준연도를 대본 안에서 오해 없게 유지한다. 업계 전체 통계를 특정 회사의 실적이나 "
    "성공 원인처럼 연결하지 마라. 쇼츠 길이에 이 정보를 함께 담기 어렵다면 숫자 자체를 "
    "빼고 검증된 방향성만 말한다. 출처에 없는 숫자를 계산·반올림·보간하지 마라.\n"
    "가장 중요(쇼츠 긴장): 흥미는 연결어 체크리스트가 아니라 정보 공개 타이밍·대치·이해관계로 만든다. "
    "'사실/하지만/그건 바로'는 경향일 뿐 필수 슬롯이 아니다. 특히 첫 본문 문장에 '사실'을 습관적으로 "
    "붙이지 마라. drama_beats 는 사용자 요청의 중심 인과축에 직접 기여하는 것만 골라 긴장을 쌓아라. "
    "같은 제품이 나오는 별도 일화라는 이유만으로 결말이나 반전 비트에 넣지 마라. "
    "사용자가 특정 사건이나 풍습을 핵심으로 지정했다면 명절 선물·답례품 같은 인접 소재로 "
    "도입부나 결말을 바꾸지 마라. "
    "훅에서 결말을 스포했으면 같은 내용을 후반에 가짜 반연출하지 말고, "
    "확인된 왜/어떻게(요구·거절·대가)로 계속 보게 만들어라.\n"
    "작성 전 제목의 구체적 의문, 처음 듣는 사람에게 필요한 전제, 중간의 새 행동/발견, "
    "후반에서 처음 알게 될 답을 구분한다. 각 문장이 새로 알려주는 것과 다음 문장이 필요한 이유를 "
    "검토하되 모든 문장에 인위적인 떡밥을 붙이지 않는다. '연구했다→노력했다→성공했다'나 "
    "장점 목록만 남으면 확인된 구체적인 시행착오·선택·반응을 조사 메모에서 찾아 다시 구성한다. "
    "근거가 없으면 빈 부분을 꾸며 채우지 않는다. 제목 중복을 줄인다는 이유로 주체·제품·상황의 "
    "이해 전제까지 삭제하거나 '회사가/그 판/주인공' 같은 지시어만으로 시작하지 않는다.\n"
    "가장 중요(탄생·개척 서사): 사용자가 탄생 비화·유래·출발점을 요청하고 동기, 행동, "
    "실패, 궁여지책, 학습 과정을 구체적으로 적었다면 그 항목을 이야기의 필수 골격으로 삼아라. "
    "주인공이 누구를 위해 무엇을 시작했고, 무엇이 부족해 어떻게 실패하거나 즉석에서 해결했으며, "
    "무엇을 배워 어떤 결과를 만들었는지가 순서대로 보여야 한다. 박물관·체험장·관광지·사망 연도 같은 "
    "현재 정보로 개척 과정을 대체하지 마라. 조사 근거가 부족하면 그 핵심 행동을 빼고 주변 정보로 "
    "채우지 말고, 근거 부족을 드러내 재조사가 필요하도록 한다.\n"
    "가장 중요(과장 경계): 과장은 제목과 감정 표현에만 허용한다. 실제 수치·사건·인과·발언을 "
    "세게 보이려고 바꾸는 것은 과장이 아니라 거짓이므로 금지한다. 자극적인 표현 뒤에는 반드시 "
    "그 표현을 납득시키는 검증 사실이 이어져야 한다. 두 사실이 시간 순서로 이어지거나 강한 대비를 "
    "이룬다는 이유만으로 앞의 장애물이 뒤 결과의 원인·이유가 됐다고 쓰지 마라. 조사 근거가 인과를 "
    "직접 뒷받침하지 않으면 'A를 뚫고 B까지 됐다'처럼 대비로만 표현한다. 특히 '안 팔릴 이유였던 게 "
    "결국 성공한 이유가 됐다' 같은 대칭 문장은 실제 원인 전환이 확인된 경우에만 허용한다.\n"
    "조건·불편·결함도 구분한다. 특정 포장이 냉장 보관을 요구해 유통 범위가 제한된 것과, 그 포장 "
    "자체가 냉장 보관을 어렵게 만든다는 것은 다른 주장이다. 새 방식이 기존 조건을 보완했다면 "
    "'기존에는 무엇이 필수였고 새 방식이 그 제약을 어떻게 없앴는지'로 쓴다. 운영상의 불편을 "
    "제품 결함이나 직접 원인으로 바꾸지 마라.\n"
    "자막 폰트 호환을 위해 title, hook, segments.text에는 한자를 절대 쓰지 말고 "
    "모든 한자 약칭을 한글로 풀어 써라. 예: 日 수출은 '일본 수출', 美 시장은 '미국 시장'으로 쓴다.\n"
    "반드시 JSON 하나만 출력한다."
) + "\n" + WHOLE_VIDEO_REVIEW_GUIDANCE

_SCHEMA = (
    '{"title": string(훅과 동일·구체적 궁금증을 만드는 한 줄), '
    '"hook": string(title과 동일·사용자 고정 제목 우선), '
    '"segments": [{"index": number, "text": string, "intent": string}]}'
)
_SUBJECT_SUFFIXES = (
    "식품",
    "푸드",
    "제과",
    "전자",
    "그룹",
    "브랜드",
    "과자",
    "김",
    "떡",
    "빵",
    "초코파이",
)
_SUBJECT_STOP_TERMS = {
    "대기업",
    "중소기업",
    "식품회사",
    "한국식품",
    "유아용쌀과자",
}

_FLOW_FACT_REMINDER = (
    "[전개·재료 재확인]\n"
    "- 확인된 갈등/대치(drama_beats·hard_facts)는 적극 연출. 밋밋한 요약으로 죽이지 말 것.\n"
    "- 없는 사건·인정 요구·주체 뒤집기만 금지. 스니펫에 있는 요구→거절은 사용.\n"
    "- 시점: '올해/작년/최근'은 절대 연도가 그 주체·사건에 붙을 때만. "
    "보도·재조명 시점을 사건 발생으로 쓰지 말고, 연도 불명이면 시점 표현을 빼라.\n"
    "- 불필요한 연도: 출시·계약·변경·통계 기준처럼 의미가 달라지는 연도만 대사에 남긴다. "
    "기사·조사·비교가 이뤄진 해는 연도를 빼도 주장과 시간 범위가 같다면 음성에서 삭제할 것.\n"
    "- 한 기사에 여러 회사 이야기가 있으면 주체를 섞지 마라.\n"
    "- 의미 없는 범위 수식어: 판매처·국가·용도를 좁히는 말은 사건 이해나 실제 차이에 필요할 때만 쓴다. "
    "'편의점 웰치스'처럼 브랜드 기원과 무관한 판매처를 붙여 설명을 흐리지 말 것.\n"
    "- 익숙한 현재 제품으로 관심을 끈 브랜드 기원 썰은 최초 발명만 말하고 끝내지 않는다. "
    "처음 제품과 현재 제품이 다르면 확인된 보급·제품군 확장 경로를 연결하되, "
    "브랜드의 계승을 같은 레시피·제품의 직접 변신으로 오인시키지 말 것.\n"
    "- 인과: 필수 보관 조건·유통상 불편·제품 자체의 결함을 구분한다. 해결책이 실제로 보완한 "
    "제약만 말하고, 불편을 제품이 문제를 일으킨 직접 원인으로 바꾸지 말 것.\n"
    "- 수치: [출처·범위가 정리된 수치]에 완전한 항목만 사용. 주체·범위·기준연도를 생략해 "
    "다른 통계로 오해하게 만들지 말고, 업계 수치를 회사 실적으로 연결하지 말 것.\n"
    "- 훅 다음 문장을 '사실,' 로 시작하지 말 것.\n"
    "- 장애물 다음에는 역설을 다시 선언하지 말고, 검증된 해결 방법이나 제품 효과를 바로 답할 것.\n"
    "- 결말은 훅의 문장을 반복하지 말고, 확인된 생활 장면·사람의 반응·후속 결과로 의미를 확장할 것.\n"
    "- 마지막은 남은 의문을 실제 행동·반응·결과로 닫을 것. 이미 닫힌 이야기 뒤에 총평·교훈·"
    "최종 판정을 의무적으로 붙이지 말고, 근거 없는 성공이나 열린 질문으로 대체하지 말 것.\n"
    "- 제목의 풍습·장면은 훅과 결말에서 회수하되 제작법 설명으로 분량을 채우지 말 것. 그 장면을 놀랍게 만든 검증된 장벽·해결·성과를 중간 도파민 축으로 사용할 것.\n"
    "- 사용자 승인 온라인 일화는 '한 네티즌은 ...고 함'처럼 개인 증언에 귀속하고 일반 사실로 확대하지 말 것.\n"
    "- '하필'은 원치 않은 선택·불운처럼 부정적인 결과가 겹치는 상황에만 사용. 긍정적 놀라움에는 '무려' 또는 무강조 표현을 사용할 것.\n"
    "- 연결어 필수 슬롯 아님. 훅은 세게, 본문은 확인된 대치로 도파민."
) + "\n" + WHOLE_VIDEO_REVIEW_GUIDANCE


def _recipe_for_prompt(recipe: Dict) -> Dict:
    """레시피 do/연결어가 '필수 체크리스트'로 읽히지 않게 완화한 사본을 만든다."""
    if not isinstance(recipe, dict):
        return {}
    out = copy.deepcopy(recipe)
    do_list = out.get("do")
    if isinstance(do_list, list):
        softened = []
        for item in do_list:
            s = str(item)
            if any(
                k in s
                for k in (
                    "사실",
                    "하지만",
                    "그건 바로",
                    "연결어",
                    "쉼표로 긴 문장",
                )
            ):
                continue
            softened.append(s)
        seen = set()
        uniq = []
        for s in softened:
            if s not in seen:
                seen.add(s)
                uniq.append(s)
        out["do"] = uniq
    avoid_list = out.get("avoid")
    if isinstance(avoid_list, list):
        out["avoid"] = [
            str(item)
            for item in avoid_list
            if "문장을 딱 떨어지게 끝맺기" not in str(item)
        ]
        out["avoid"].extend(
            [
                "같은 사실을 표현만 바꿔 반복",
                "쉼표로 여러 정보를 한 문장에 몰아넣기",
                "연결어를 문장 시작에 습관적으로 배치",
            ]
        )
    voice = str(out.get("voice") or "")
    if voice:
        safe_sentences = [
            sentence.strip()
            for sentence in re.split(r"(?<=[.!?])\s+", voice)
            if sentence.strip()
            and not any(
                marker in sentence
                for marker in (
                    "문장을 딱 끊지",
                    "쉼표로 이어",
                    "연결어",
                )
            )
        ]
        out["voice"] = (
            " ".join(safe_sentences)
            + " 실제 대사의 어미와 구어체 온도만 참고한다. "
            "이 관찰값은 표면 말투 참고용이며 음성 인식 오류, 잘린 문장, "
            "쉼표 연쇄, 연결어 반복은 모방하지 않는다. '~는데', '~지만'처럼 "
            "다음 내용이 필요한 어미는 같은 생각 안에서 자연스럽게 이어질 때만 쓰고, "
            "각 비트를 일부러 미완결 문장으로 끝내지 않는다."
        ).strip()
    return out


def _safe_style_summary(summary: str) -> str:
    sentences = [
        sentence.strip()
        for sentence in re.split(r"(?<=[.!?])\s+", summary or "")
        if sentence.strip()
    ]
    kept = [
        sentence
        for sentence in sentences
        if not any(
            marker in sentence
            for marker in (
                "연결어",
                "긴 호흡",
                "쉼표",
            )
        )
    ]
    return " ".join(kept)


def _safe_endings(endings: List) -> List[str]:
    grammatical = (
        "다고",
        "라고",
        "이라고",
        "였다고",
        "었다고",
        "았다고",
        "는다고",
        "진다고",
        "이었음",
        "였음",
        "했음",
    )
    values = []
    for item in endings:
        if not isinstance(item, (list, tuple)) or not item:
            continue
        value = str(item[0]).strip()
        if any(value.endswith(ending) for ending in grammatical):
            values.append(value)
    return values[:8]


def _safe_tone(tone: str) -> str:
    """Keep tone adjectives while dropping learned plot/connector instructions."""

    sentences = [
        sentence.strip()
        for sentence in re.split(r"(?<=[.!?])\s+", tone or "")
        if sentence.strip()
    ]
    kept = [
        sentence
        for sentence in sentences
        if not any(
            marker in sentence
            for marker in (
                "연결어",
                "기승전결",
                "정보를 전개",
                "논리를 빠르게 전개",
                "훅→",
            )
        )
    ]
    return " ".join(kept)


def _safe_dialogue_samples(samples: List) -> List[str]:
    """Select only clean, self-contained excerpts as surface-voice examples."""

    safe_final = re.compile(
        r"(?:다고(?:\s*함)?|라고|이라고|였다고|었다고|았다고|했다고|"
        r"는다고|진다고|이었음|였음|했음|됐음|이었다)$"
    )
    selected: List[str] = []
    for raw in samples or []:
        sample = re.sub(r"\s+", " ", str(raw or "")).strip()
        if not sample or re.search(r"(?:습니다|입니다|해요|하죠)(?:[.!?]?$)", sample):
            continue
        candidate = sample[:180].strip()
        if len(sample) > 180:
            matches = list(safe_final.finditer(candidate))
            if matches:
                candidate = candidate[:matches[-1].end()].strip()
        candidate = candidate.strip(" \t\r\n,;:'\"")
        if not (20 <= len(candidate) <= 180 and safe_final.search(candidate)):
            continue
        selected.append(candidate)
        if len(selected) >= 3:
            break
    return selected


def _style_context(profile: Dict) -> str:
    """LLM 프롬프트에 넣을 채널 말투·분량 컨텍스트 문자열을 구성합니다."""
    recipe = _recipe_for_prompt(profile.get("recipe", {}) or {})
    hook_examples = [h for h in (profile.get("hook_examples") or []) if h]
    stats = profile.get("stats", {})
    avg_dur = stats.get("avg_duration_sec", 40)
    dlg = stats.get("dialogue_stats", {})
    ccn = dlg.get("char_count_no_space", {})
    snt = dlg.get("sentence_count", {})
    cps = dlg.get("chars_per_sec", {})
    hlen = dlg.get("hook_char_len", {})
    hook_len_spec = (
        f"관찰 평균 {hlen.get('avg', 12)}자(관찰 범위 {hlen.get('min', 6)}~{hlen.get('max', 15)}자; 제약 아님)"
        if hlen else "대상과 이상한 일이 이해되는 길이; 고정 글자 수 없음"
    )
    target_spec = (
        f"대사 총 {ccn.get('min', 0)}~{ccn.get('max', 0)}자(목표 {ccn.get('avg', 0)}자), "
        f"문장 {snt.get('min', 0)}~{snt.get('max', 0)}개, 말속도 {cps.get('avg', 0)}자/초, "
        f"훅 {hook_len_spec}"
    ) if dlg else f"약 {avg_dur}초 분량"

    samples = _safe_dialogue_samples(profile.get("dialogue_samples", []))
    endings = _safe_endings(profile.get("ending_patterns", []))
    voice = recipe.get("voice", "")
    endings_txt = ", ".join(f"'{e}'" for e in endings) if endings else ""
    samples_txt = "\n".join(f"  - {sample}" for sample in samples) or (
        "(신뢰할 수 있는 완결 발췌 없음 — 합성된 말투 지침과 어미만 사용)"
    )
    hooks_txt = "\n".join(f"  - {h}" for h in hook_examples[:8]) or "(없음)"
    recipe_hook = recipe.get("hook", "")
    return (
        f"[스타일 적용 경계]\n"
        f"- 학습 자료의 훅의 온도·반말 나레이션 말투·어미는 참고한다. 평균 분량·문장 수는 강제하지 않는다.\n"
        f"- 기존 영상의 전개 순서·연결어·쉼표 위치·문장 골격·비트 수·편집 방식은 모방하지 않는다.\n"
        f"- 아래 실제 대사는 표현 참고용 발췌이며, 소재·사건 배열·문장 구조를 가져오지 않는다.\n\n"
        f"[최우선 품질 기준]\n"
        f"- 채널 스타일보다 이야기의 인과·정보 공개 순서·가독성과 소재 적합성을 우선한다.\n"
        f"- 소재의 가장 강한 인과 축만 남기고 요청 분량에 맞는 생각 단위로 구성한다. "
        f"훅·정체·상황·대치·선택·대가·보상은 고정 슬롯이 아니다.\n"
        f"- 각 비트는 새 정보 하나만 전달하고, 같은 사실 반복과 마지막 정보 덤프를 금지한다.\n"
        f"- 장애물 다음 비트는 검증된 해결 방법·제품 효과·결과를 답한다. 역설만 다시 선언하는 징검다리 비트는 삭제한다.\n"
        f"- 결말은 훅을 재진술하지 않고, 확인된 생활 장면·사람의 반응·후속 결과로 의미를 한 단계 확장한다.\n"
        f"- 각 비트는 자연스럽게 낭독되는 생각 단위다. 자막처럼 임의로 문장을 찢지 않는다.\n"
        f"- 연결어가 아니라 앞 문장에서 생긴 궁금증과 다음 문장의 답으로 이어 붙인다.\n"
        f"- 과장은 표현에만 허용하고 사실·수치·인과는 바꾸지 않는다.\n\n"
        f"[훅 예시 — 적용 의무 없음. 단어 복사가 아닌 길이·압축감·온도만 참고]\n{hooks_txt}\n\n"
        f"[훅 작성 지침]\n{recipe_hook}\n"
        f"title 과 hook 은 동일 문구. 훅 길이 참고: {hook_len_spec}. "
        f"질문형/선언형보다 실제 궁금증이 중요하다. 사건과 동사의 의미 적합성을 임팩트보다 먼저 검증하고, "
        f"예시 표현이 소재와 맞지 않으면 사용하지 말 것.\n\n"
        f"[말투 지침]\n{voice}\n\n"
        f"[실제 대사 발췌 — 어휘 온도·어미만 참고. 구조 모방 금지]\n"
        f"{samples_txt}\n\n"
        f"[자주 쓰는 문장 끝맺음(어미)]\n{endings_txt}\n\n"
        f"[톤]\n{_safe_tone(str(recipe.get('tone', '')))}\n\n"
        f"{_FLOW_FACT_REMINDER}\n\n"
        f"[학습 관찰 길이 — 사용자 지정이 없으면 제약 아님] 약 {avg_dur}초\n"
        f"[학습 관찰 대사 분량 — 제약 아님] {target_spec}\n"
        f"[학습 관찰 훅 길이 — 제약 아님] {hook_len_spec}"
    )


def _research_block(research: Optional[Dict]) -> str:
    if not research:
        return (
            "[웹 조사 메모]\n"
            "(없음 — 사용자 입력에 없는 경위·조건·인용·요구를 창작하지 말 것.)\n\n"
        )
    brief = (research.get("brief_text") or "").strip()
    thin = bool(research.get("thin")) or not (research.get("hard_facts") or [])
    header = "[웹 조사 메모]"
    if thin:
        header += " ※재료 빈약: 사건 경위/조건/인용 창작 금지"
    if not brief:
        brief = "(요약 없음 — 하드 사건 창작 금지)"
    return f"{header}\n{brief}\n\n"


def _prefer_hook_line(title: str, hook: str) -> str:
    """Prefer the authored title; wording/length alone cannot assess curiosity."""
    title = (title or "").strip()
    hook = (hook or "").strip()
    if not title:
        return hook
    if not hook:
        return title
    return title


def _finalize_llm_script(data: Dict) -> Dict:
    """LLM 응답의 세그먼트 index·title/hook 정합을 보정하고 출처를 표시합니다."""
    for i, seg in enumerate(data.get("segments") or []):
        seg.setdefault("index", i)
    line = _prefer_hook_line(str(data.get("title") or ""), str(data.get("hook") or ""))
    if line:
        data["title"] = line
        data["hook"] = line
    data["_source"] = "llm"
    return data


_FLOW_CONNECTOR = re.compile(
    r"^\s*(근데|그런데|그래서|그러나|하지만|그리고|사실(?:은)?)\b"
)
_FLOW_TOKEN_STOP = {
    "그런데",
    "그래서",
    "하지만",
    "그리고",
    "사실",
    "바로",
    "이렇게",
    "저렇게",
    "있는데",
    "했는데",
    "이라고",
    "있다고",
    "했다고",
}

_HAPIL_NEGATIVE_CONTEXT = re.compile(
    r"문제|사고|논란|피해|불운|실패|망하|막히|거절|포기|타격|고생|"
    r"원치|불리|최악|잘못|엉뚱|하필이면|터지|걸리|겹치|없었|부족|어렵"
)

_EDITORIAL_YEAR_CUE = re.compile(
    r"(?:19|20)\d{2}년(?:에|의|도)?\s*(?:"
    r"비교(?:해\s*보니|했더니|한\s*결과)?|"
    r"확인(?:해\s*보니|했더니|한\s*결과)?|"
    r"기사|보도|팩트체크|자료)"
)

_WEAK_FINAL_ENDING = re.compile(
    r"(?:(?:그런\s*)?(?:거지|셈이지)|(?:일|였을)지도\s*모름|"
    r"것\s*같(?:음|다)|수도\s*있음|아닐까|까요|까)[.!?\s]*$"
)


def analyze_story_flow(script: Dict) -> List[str]:
    """Detect structural symptoms worth an additional narrative edit."""

    segments = [
        segment
        for segment in script.get("segments") or []
        if isinstance(segment, dict) and str(segment.get("text") or "").strip()
    ]
    issues: List[str] = []
    if not segments:
        issues.append("대본 본문이 비어 있음")

    connector_starts = []
    token_sets = []
    hapil_count = 0
    for index, segment in enumerate(segments, 1):
        text = str(segment.get("text") or "").strip()
        segment_hapil_count = text.count("하필")
        hapil_count += segment_hapil_count
        if segment_hapil_count and not _HAPIL_NEGATIVE_CONTEXT.search(text):
            issues.append(
                f"비트 {index}에서 부정적 우연이 아닌데 '하필'을 강조어로 사용함"
            )
        if text.endswith((",", ";", ":")):
            issues.append(f"비트 {index}가 문장 중간에서 끊김")
        if text.count("\"") % 2 or text.count("'") % 2:
            issues.append(f"비트 {index}에 닫히지 않은 인용부호가 있음")
        if re.search(
            r"(?:만든|하던|하는|되는|된|있는|없는|같은|위한),\s+\S+",
            text,
        ):
            issues.append(f"비트 {index}에 분리된 관형어로 문장이 파편화됨")
        if re.search(
            r"(?:\([^)]*(?:보도|기사|출처)\s*기준[^)]*\)|"
            r"(?:기사에\s*따르면|보도\s*기준(?:으로)?))",
            text,
        ):
            issues.append(
                f"비트 {index}의 음성 대사에 출처 메모·면책 문구가 노출됨"
            )
        if _EDITORIAL_YEAR_CUE.search(text):
            issues.append(
                f"비트 {index}에 사실 의미보다 출처·비교 시점을 설명하는 "
                "불필요한 연도가 노출됐을 수 있음"
            )
        match = _FLOW_CONNECTOR.search(text)
        if match:
            connector_starts.append((index, match.group(1)))
        compact_length = len(re.sub(r"\s+", "", text))
        numeric_claims = re.findall(
            r"\d[\d,.]*\s*(?:%|퍼센트|원|달러|위|배|억|만|조|년)?",
            text,
        )
        if (
            compact_length > 92
            or text.count(",") >= 4
            or (
                index == len(segments)
                and compact_length > 35
                and len(numeric_claims) >= 3
            )
        ):
            issues.append(
                f"비트 {index}에 정보가 과도하게 몰림"
            )
        tokens = {
            token
            for token in re.findall(r"[A-Za-z가-힣0-9]{2,}", text)
            if token not in _FLOW_TOKEN_STOP
        }
        token_sets.append(tokens)

    # Counts alone cannot tell whether a conjunction is logically appropriate.
    if hapil_count > 1:
        issues.append("'하필'이 반복되어 부정적 뉘앙스가 과잉됨")
    for index in range(1, len(token_sets)):
        left = token_sets[index - 1]
        right = token_sets[index]
        shared = left & right
        union = left | right
        if len(shared) >= 3 and union and len(shared) / len(union) >= 0.42:
            issues.append(
                f"비트 {index}와 {index + 1}이 같은 정보를 반복할 가능성"
            )

    # A payoff only feels earned when the decision that caused the cost is
    # actually spoken.  A revision of only the tail can otherwise replace the
    # original refusal/abandonment beat with two reward beats.
    conflict_index: Optional[int] = None
    for index, segment in enumerate(segments):
        text = str(segment.get("text") or "")
        intent = str(segment.get("intent") or "")
        if re.search(r"제안|요구|조건|협상|고민|갈등|대치|빼.*(?:달|자|라)|지우", text + intent):
            conflict_index = index
            break
    if conflict_index is not None:
        resolution_text = " ".join(
            f"{segment.get('text', '')} {segment.get('intent', '')}"
            for segment in segments[conflict_index:]
        )
        has_reward = bool(re.search(
            r"주문|품절|매진|수출길|매출|대박|돈쭐|폭발|없어서\s*못|보상|이어졌",
            resolution_text,
        ))
        has_decision = bool(re.search(
            r"거절|포기|접어|접었|고수|유지|지우지|안\s*된|받지|무산|"
            r"걷어차|선택|결정",
            resolution_text,
        ))
        if has_reward and not has_decision:
            issues.append("대치 뒤 주인공의 선택 없이 보상으로 건너뜀")

    if segments:
        final_segment = segments[-1]
        final_text = str(final_segment.get("text") or "")
        final_intent = str(final_segment.get("intent") or "")
        hook_text = f"{script.get('title', '')} {script.get('hook', '')}"
        hook_tokens = {
            token
            for token in re.findall(r"[가-힣]{2,}", hook_text)
            if token not in {
                "때문에", "이야기", "회사의", "회사", "제품", "사실", "정체",
            }
            and not re.fullmatch(r"회사\d*", token)
        }
        final_tokens = set(re.findall(r"[가-힣]{2,}", final_text))
        has_final_reward = bool(re.search(
            r"주문|품절|매진|수출|매출|대박|폭발|없어서\s*못|보상",
            final_text + final_intent,
        ))
        has_verdict = bool(re.search(
            r"오히려|결국|셈|거임|돼버|된\s*거|돌아왔|대신|훅\s*회수|"
            r"선택|포기|독도|일본",
            final_text,
        ))
        if (
            hook_tokens
            and has_final_reward
            and not (hook_tokens & final_tokens)
            and not has_verdict
        ):
            issues.append("마지막 비트가 결과 나열로 끝나 훅·선택 회수가 부족함")
        if re.search(
            r"(?:마케팅|홍보|광고)(?:용(?:도)?으?로|으?로|에)?\s*"
            r"(?:활용|사용|쓰고|썼|쓴|이용)",
            final_text,
        ):
            issues.append("마지막 비트가 시청자 보상보다 기업의 후속 홍보 정보에 머묾")
        if _WEAK_FINAL_ENDING.search(final_text):
            issues.append("마지막 문장이 질문·가능성·약한 재진술로 끝나 최종 판정이 부족함")
    return list(dict.fromkeys(issues))


def _numeric_signature(script: Dict) -> Counter:
    """Keep factchecked numbers stable during a flow-only rewrite."""

    values = []
    for segment in script.get("segments") or []:
        if not isinstance(segment, dict):
            continue
        text = str(segment.get("text") or "")
        text = re.sub(
            r"\([^)]*(?:보도|기사|출처)\s*기준[^)]*\)",
            "",
            text,
        )
        values.extend(
            value.replace(",", "")
            for value in re.findall(r"\d[\d,.]*", text)
        )
    return Counter(values)


_SENSITIVE_CLAIM_TERMS = (
    "일본", "미국", "대만", "홍콩", "중국", "베트남", "호주", "캐나다",
    "몽골", "미주", "유럽", "품절", "매진", "전 제품", "수출", "협약",
    "제안", "계약", "인수", "매출", "점유율", "최초", "유일", "발언",
    "밝혔다", "말했다",
)


def _sensitive_claim_signature(script: Dict) -> set[str]:
    """Track factual claim categories a flow-only edit must not invent."""

    text = " ".join(
        [
            str(script.get("title") or ""),
            str(script.get("hook") or ""),
            *[
                str(segment.get("text") or "")
                for segment in script.get("segments") or []
                if isinstance(segment, dict)
            ],
        ]
    )
    return {term for term in _SENSITIVE_CLAIM_TERMS if term in text}


def _causal_marker_signature(script: Dict) -> Counter:
    """A flow-only rewrite may clarify causality but must not invent it."""

    text = " ".join(
        str(segment.get("text") or "")
        for segment in script.get("segments") or []
        if isinstance(segment, dict)
    )
    markers = re.findall(
        r"때문에|덕분에|(?:원인|이유)(?:이|가|였|인|로|를|도)?|"
        r"(?:그래서|그로\s*인해|그\s*결과)",
        text,
    )
    return Counter(markers)


def polish_story_flow(
    llm: LLMClient,
    profile: Dict,
    script: Dict,
    *,
    instruction: str = "",
    original_script: Optional[Dict] = None,
    force: bool = False,
    research: Optional[Dict] = None,
    source_text: str = "",
    required_content: str = "",
    review_issues: Optional[List[str]] = None,
) -> Dict:
    """Use the generation model only when deterministic flow checks fail."""

    issues = list(dict.fromkeys([*analyze_story_flow(script), *(review_issues or [])]))
    if not issues and force:
        issues = ["팩트체크 수정 뒤 문법·문장 연결·낭독 호흡 재검토 필요"]
    if not issues or not llm.available:
        result = copy.deepcopy(script)
        if issues:
            result["_flow_issues"] = issues
        return result

    recipe = _recipe_for_prompt(profile.get("recipe", {}) or {})
    system = (
        "너는 조회수와 완성도를 동시에 보는 한국어 쇼츠 스토리 에디터다. "
        "이미 팩트 재료로 작성된 대본의 흐름만 고친다. 새 사건·수치·인용·인과를 "
        "추가하거나 기존 사실의 의미를 바꾸면 안 된다. 채널의 반말 썰 톤과 강한 훅은 "
        "유지하되, 연결어 반복·동일 정보 반복·긴 쉼표 문장·마지막 정보 덤프를 제거한다. "
        "서로 다른 두 사실을 결말에서 대칭적으로 묶더라도 앞 사실을 뒤 결과의 '이유·원인·덕분'으로 "
        "새로 만들지 마라. 근거가 대비뿐이면 '장벽을 뚫고 여기까지 왔다'고만 표현한다. "
        "학습 프로필에서는 훅의 온도·반말 나레이션 말투·어미만 유지한다. 기존 영상의 "
        "전개 순서·문장 골격·비트 수를 재현하지 마라. 각 비트는 자막 조각이 아니라 자연스럽게 "
        "낭독되는 하나의 생각 단위여야 하며, 조각난 관형어·닫히지 않은 인용·어색한 조사와 "
        "단어 결합을 고친다. "
        "'(몇 년 보도 기준)', '기사에 따르면' 같은 출처 메모와 괄호형 면책 문구는 음성 대사에서 "
        "제거한다. 출시·계약·변경·통계 기준처럼 시점이 사실의 의미나 전개를 바꾸는 연도만 "
        "자연스럽게 남긴다. 기사·조사·비교가 이뤄진 해는 그 연도를 빼도 주장과 시간 범위가 "
        "달라지지 않으면 음성 대사에서 삭제한다. "
        "각 비트가 앞 비트의 의문을 받고 새 정보를 하나씩 공개하게 만든다. 실제 선택·거절이 "
        "있었던 사건에만 그 선택을 명시하고, 선택이 없는 소재에 거절·포기 구조를 강요하지 않는다. "
        "결말은 해당 소재에 남아 있는 의문을 확인된 행동·반응·결과로 해소한다. 숫자만 나열하거나 "
        "처음의 훅을 단순 반복하지 말되, 모든 소재에 반전·판정 한 방을 강제하지 않는다. "
        "기업 마케팅·홍보라는 단어만으로 결말을 삭제하지 마라. 검증된 사실 안에서 사용자 지정 필수 결말 "
        "> 중심 사건의 직접적인 결과 > 일반 편집 취향 순으로 우선한다. 사용자가 지정한 행사나 "
        "복귀 파티처럼 중심 사건의 결과를 보여주는 장면은 보존하고, 무관한 운영 정보만 제외한다. "
        "마지막은 남아 있던 의문을 확인된 행동·반응·결과로 해소하고 채널 말투 안에서 자연스럽게 닫는다. "
        "이미 사건이 닫혔다면 별도의 판정·교훈·위상 상승 문장을 덧붙이지 않는다. "
        "실제 미해결 사건이 아니라면 마지막을 막연한 가능성이나 질문으로 회피하지 않는다. "
        "사용자가 마지막 N개 비트만 바꾸라고 했더라도 원래 선택 비트가 교체 범위에 포함됐다면, "
        "첫 교체 비트에 선택과 첫 보상을 함께 담아 인과 고리를 보존한다. "
        "비트 수를 강제하지 말고 요청한 분량 안에서 약한 "
        "단계를 채우려고 문장을 만들지 않는다. JSON만 출력한다."
    )
    user = (
        f"[흐름 문제]\n- " + "\n- ".join(issues) + "\n\n"
        f"[채널 톤]\n{recipe.get('tone', '')}\n"
        f"[말투]\n{recipe.get('voice', '')}\n\n"
        f"[사용자 수정 요청]\n{instruction or '(신규 대본)'}\n\n"
        f"[원래 제작 의도 — 제목·도입·결말·분량을 보존]\n{source_text}\n\n"
        f"[필수/선택 항목 — 선택은 생략 가능]\n{required_content}\n\n"
        "사용자 의도 > 소재 적합성 > 학습 스타일 순으로 적용한다. "
        "분량이 넘치면 선택 정보부터 줄이고, 필수 항목 간 충돌은 임의 삭제로 숨기지 마라.\n"
        f"{_research_block(research)}"
        f"[대본 JSON]\n{json.dumps(script, ensure_ascii=False)}\n\n"
        f"스키마: {_SCHEMA}"
    )
    try:
        data = llm.complete_json(
            system,
            user,
            temperature=0.45,
            max_tokens=4500,
        )
    except LLMQuotaExceeded:
        raise
    except LLMError as exc:
        log.info("대본 흐름 보정 LLM 실패: %s", exc)
        result = copy.deepcopy(script)
        result["_flow_issues"] = issues
        return result
    if not isinstance(data, dict) or not data.get("segments"):
        result = copy.deepcopy(script)
        result["_flow_issues"] = issues
        return result

    candidate = preserve_title(_finalize_llm_script(data), source_text, instruction)
    if original_script is not None and instruction:
        candidate = apply_revision_scope(
            original_script,
            candidate,
            instruction,
        )
    candidate["_source"] = str(
        script.get("_source") or "llm"
    ) + "+flow"
    candidate["_flow_issues_before"] = issues
    numeric_changed = _numeric_signature(candidate) != _numeric_signature(script)
    numeric_removal = numeric_changed and not (_numeric_signature(candidate) - _numeric_signature(script))
    if numeric_changed and not numeric_removal:
        result = copy.deepcopy(script)
        result["_flow_issues"] = issues
        result["_flow_rejected"] = "numeric_claims_changed"
        log.warning("대본 흐름 보정 거부: 검증된 숫자 구성이 변경됨")
        return result
    added_claims = sorted(
        _sensitive_claim_signature(candidate) - _sensitive_claim_signature(script)
    )
    original_causal = _causal_marker_signature(script)
    candidate_causal = _causal_marker_signature(candidate)
    added_causal = candidate_causal - original_causal
    equivalent = False
    if added_claims or added_causal or numeric_removal:
        equivalent = flow_change_supported(llm, script, candidate, research)
    if numeric_removal and not equivalent:
        result = copy.deepcopy(script)
        result["_flow_issues"] = issues
        result["_flow_rejected"] = "numeric_claims_changed"
        return result
    if added_claims and not equivalent:
        result = copy.deepcopy(script)
        result["_flow_issues"] = issues
        result["_flow_rejected"] = "factual_claims_added"
        result["_flow_added_claims"] = added_claims
        log.warning(
            "대본 흐름 보정 거부: 팩트체크 뒤 새 주장 범주가 추가됨 (%s)",
            ", ".join(added_claims),
        )
        return result
    if added_causal and not equivalent:
        result = copy.deepcopy(script)
        result["_flow_issues"] = issues
        result["_flow_rejected"] = "causal_claims_added"
        result["_flow_added_causal_markers"] = dict(added_causal)
        log.warning(
            "대본 흐름 보정 거부: 팩트체크 뒤 새 인과 표현이 추가됨 (%s)",
            ", ".join(sorted(added_causal.elements())),
        )
        return result
    remaining = analyze_story_flow(candidate)
    if remaining:
        candidate["_flow_issues"] = remaining
    return candidate


def extract_subject_anchors(
    script: Dict,
    source_text: str = "",
) -> List[str]:
    """Extract conservative product/company anchors that revisions must retain."""

    explicit = extract_explicit_subject_anchors(source_text)
    if explicit:
        # A prompt can mention a previous episode or comparison target many times.
        # Explicit declarations such as "회사 '올바름'" and "주인공은 올바름"
        # are a stronger signal than those incidental repetitions.
        return explicit

    script_text = " ".join(
        [
            str(script.get("title") or ""),
            str(script.get("hook") or ""),
            *[
                str(segment.get("text") or "")
                for segment in script.get("segments") or []
                if isinstance(segment, dict)
            ],
        ]
    )
    combined = f"{source_text} {script_text}"
    compact = re.sub(r"\s+", "", combined)
    candidates: Dict[str, int] = {}
    suffix_pattern = "|".join(
        re.escape(value) for value in _SUBJECT_SUFFIXES
    )
    for match in re.finditer(
        rf"[A-Za-z가-힣0-9·]{{2,20}}?(?:{suffix_pattern})",
        combined,
    ):
        value = match.group(0).strip()
        normalized = re.sub(r"\s+", "", value)
        if (
            len(normalized) < 3
            or normalized in _SUBJECT_STOP_TERMS
        ):
            continue
        count = compact.count(normalized)
        if count >= 2 or normalized in re.sub(r"\s+", "", source_text):
            candidates[normalized] = max(candidates.get(normalized, 0), count)

    specific_suffixes = [
        value for value in _SUBJECT_SUFFIXES if len(value) >= 3
    ]
    if specific_suffixes:
        specific_pattern = "|".join(
            re.escape(value) for value in specific_suffixes
        )
        for match in re.finditer(
            rf"(?:[A-Za-z가-힣0-9·]{{2,16}}\s+)?(?:{specific_pattern})",
            combined,
        ):
            value = re.sub(r"\s+", " ", match.group(0)).strip()
            prefix = value.split(" ", 1)[0] if " " in value else ""
            if prefix in {
                "팔리는", "판매되는", "유명한", "한국의", "러시아의",
                "태어난", "만든", "먹는", "국민", "대표",
            }:
                value = value.split(" ", 1)[1]
            elif prefix and prefix.endswith(
                ("에서", "에게", "으로", "부터", "까지", "처럼", "보다", "형태의", "나라의")
            ):
                value = value.split(" ", 1)[1]
            compact_value = value.replace(" ", "")
            if compact_value not in _SUBJECT_STOP_TERMS:
                candidates[value] = max(
                    candidates.get(value, 0),
                    compact.count(compact_value),
                )

    for match in re.finditer(
        r"(?:그건|그\s*제품은|제품은|주인공은|정체는)\s*바로\s+"
        r"(.{2,32}?)(?:인데|이라고|였|임|$)",
        script_text,
    ):
        phrase = re.sub(
            r"^(?:우리가\s+흔히\s+먹는|그|이)\s+",
            "",
            match.group(1).strip(),
        )
        phrase = re.sub(r"\s+", " ", phrase).strip(" ,.'\"")
        if len(phrase.replace(" ", "")) >= 3:
            candidates[phrase] = max(candidates.get(phrase, 0), 3)

    return [
        value
        for value, _ in sorted(
            candidates.items(),
            key=lambda item: (
                -int(" " in item[0]),
                -item[1],
                -len(item[0]),
                item[0],
            ),
        )[:5]
    ]


def extract_explicit_subject_anchors(source_text: str) -> List[str]:
    """Extract subjects explicitly declared by the user as the main entity."""

    text = str(source_text or "")
    if not text.strip():
        return []
    quote_open = "['\"‘“]"
    quote_close = "['\"’”]"
    name = r"([A-Za-z가-힣0-9·][A-Za-z가-힣0-9· ]{1,28}?)"
    patterns = (
        rf"(?:회사|업체|브랜드)\s*{quote_open}{name}{quote_close}",
        rf"{quote_open}{name}{quote_close}\s*(?:이라는|이란|라는|란)\s*"
        rf"(?:회사|업체|브랜드)",
        r"(?:본문\s*)?주인공(?:은|는|이|가)?\s*(?:끝까지\s*)?"
        r"['\"‘“]?([A-Za-z가-힣0-9·]{2,24}?)(?=(?:으로|로)?\s*"
        r"(?:유지|고정|진행|삼|설정)|\s*[,.;\n]|$)",
        r"^\s*([A-Za-z가-힣0-9· ]{2,36}?)의?\s*"
        r"(?:탄생|시작|유래|기원)\s*(?:비화|배경|과정|이야기)?(?:[.。!?]|$)",
    )
    values: List[str] = []
    seen = set()
    for pattern in patterns:
        for match in re.finditer(pattern, text, flags=re.I):
            value = re.sub(r"\s+", " ", match.group(1)).strip(" .,'\"‘’“”")
            compact = value.replace(" ", "")
            if (
                len(compact) < 2
                or compact in _SUBJECT_STOP_TERMS
                or compact.casefold() in seen
            ):
                continue
            seen.add(compact.casefold())
            values.append(value)
    return values[:3]


_NARRATIVE_DETAIL_GROUPS = (
    ("동기", ("가난", "농민", "주민", "돕")),
    ("첫 행동", ("산양", "염소", "젖")),
    ("학습", ("유럽", "본토", "기술", "배워", "배웠")),
    ("궁여지책", ("비누틀", "비누갑", "비눗갑", "약탕기", "동굴", "보관소", "숙성고", "땅을 파")),
)


def analyze_narrative_coverage(script: Dict, source_text: str) -> List[str]:
    """Detect when an origin-story prompt lost its user-specified story spine."""

    source = str(source_text or "")
    if not any(
        marker in source
        for marker in ("탄생", "비화", "유래", "기원", "출발점", "개척")
    ):
        return []
    script_text = " ".join(
        str(segment.get("text") or "")
        for segment in script.get("segments") or []
        if isinstance(segment, dict)
    )
    missing = []
    for label, terms in _NARRATIVE_DETAIL_GROUPS:
        if any(term in source for term in terms) and not any(
            term in script_text for term in terms
        ):
            missing.append(label)
    if len(missing) < 2:
        return []
    return [
        "사용자가 지정한 탄생 서사의 핵심 행동이 빠짐: " + ", ".join(missing)
    ]


_REQUIRED_CONTENT_STOP = {
    "대본", "필수", "포함", "내용", "사실", "장면", "전개", "한다",
    "한다는", "이라는", "그리고", "또는", "통해", "대한", "해당", "사용",
}


def parse_required_content(value: str) -> List[str]:
    return parse_items(value)


def analyze_required_content_coverage(
    script: Dict,
    required_content: str,
    research: Optional[Dict] = None,
) -> Dict:
    """Report mandatory beats that are unsupported or absent after generation."""

    items = parse_required_content(required_content)
    script_text = " ".join(
        [
            str(script.get("title") or ""),
            str(script.get("hook") or ""),
            *[
                str(segment.get("text") or "")
                for segment in script.get("segments") or []
                if isinstance(segment, dict)
            ],
        ]
    )
    compact_script = re.sub(r"\s+", "", script_text).casefold()
    coverage_rows = {
        str(row.get("item") or ""): row
        for row in (research or {}).get("required_content_coverage") or []
        if isinstance(row, dict)
    }
    rows = []
    missing: List[str] = []
    unsupported: List[str] = []
    for item in items:
        compact_item = re.sub(r"\s+", "", item).casefold()
        tokens = [
            token.casefold()
            for token in re.findall(
                r"(?:19|20)\d{2}|\d+(?:\.\d+)?%?|[A-Za-z][A-Za-z0-9-]{1,}|[가-힣]{2,}",
                item,
            )
            if token.casefold() not in _REQUIRED_CONTENT_STOP
        ]
        numeric = [token for token in tokens if any(ch.isdigit() for ch in token)]
        matched = [token for token in tokens if token in compact_script]
        threshold = max(1, min(3, (len(tokens) + 1) // 2)) if tokens else 1
        present = compact_item in compact_script or (
            len(matched) >= threshold
            and all(token in compact_script for token in numeric)
        )
        evidence = coverage_rows.get(item)
        supported = bool(evidence and evidence.get("result_count"))
        status = "included" if present and supported else "unsupported" if not supported else "missing"
        optional = optional_item(item)
        if optional:
            status = "optional_included" if present else "optional_omitted"
        elif status == "unsupported":
            unsupported.append(item)
        elif status == "missing":
            missing.append(item)
        rows.append(
            {
                "item": item,
                "supported": supported,
                "included": present,
                "status": status,
                "optional": optional,
            }
        )
    return {
        "items": rows,
        "missing": missing,
        "unsupported": unsupported,
        "passed": not missing and not unsupported,
    }


def script_preserves_subject(script: Dict, subject_anchors: List[str]) -> bool:
    """Return False only when confident subject anchors all disappeared."""

    anchors = [
        re.sub(r"\s+", "", str(value or "")).casefold()
        for value in subject_anchors
        if str(value or "").strip()
    ]
    if not anchors:
        return True
    blob = " ".join(
        [
            str(script.get("title") or ""),
            str(script.get("hook") or ""),
            *[
                str(segment.get("text") or "")
                for segment in script.get("segments") or []
                if isinstance(segment, dict)
            ],
        ]
    )
    compact = re.sub(r"\s+", "", blob).casefold()
    return any(anchor in compact for anchor in anchors)


def apply_revision_scope(
    original_script: Dict,
    candidate_script: Dict,
    instruction: str,
) -> Dict:
    """Deterministically preserve sections outside a narrowly requested edit."""

    request = re.sub(r"\s+", "", instruction or "")
    explicit_tail_match = re.search(
        r"(?:마지막|끝|마무리|엔딩)"
        r"(?:(한|두|세|네|\d+)\s*(?:개|비트|문장|세그먼트))?만",
        request,
    )
    closing_only = bool(explicit_tail_match) or any(
        marker in request
        for marker in (
            "마무리만",
            "마지막만",
            "마지막부분만",
            "엔딩만",
            "끝부분만",
        )
    )
    latter_only = not closing_only and any(
        marker in request
        for marker in (
            "후반부만",
            "후반부를",
            "후반부가",
            "후반부분만",
        )
    )
    title_only = any(
        marker in request
        for marker in ("제목만", "타이틀만", "훅만")
    )
    if not (closing_only or latter_only or title_only):
        return candidate_script

    explicit_tail_count = None
    if explicit_tail_match and explicit_tail_match.group(1):
        count_text = explicit_tail_match.group(1)
        explicit_tail_count = {
            "한": 1,
            "두": 2,
            "세": 3,
            "네": 4,
        }.get(count_text)
        if explicit_tail_count is None and count_text.isdigit():
            explicit_tail_count = max(1, int(count_text))

    merged = copy.deepcopy(original_script)
    original_segments = [
        segment
        for segment in original_script.get("segments") or []
        if isinstance(segment, dict)
    ]
    candidate_segments = [
        segment
        for segment in candidate_script.get("segments") or []
        if isinstance(segment, dict) and str(segment.get("text") or "").strip()
    ]

    if title_only:
        line = _prefer_hook_line(
            str(candidate_script.get("title") or ""),
            str(candidate_script.get("hook") or ""),
        )
        if line:
            merged["title"] = line
            merged["hook"] = line
    elif original_segments and candidate_segments:
        if closing_only:
            if explicit_tail_count is not None:
                tail_count = min(
                    explicit_tail_count,
                    len(original_segments),
                    len(candidate_segments),
                )
            else:
                added_count = max(
                    0,
                    len(candidate_segments) - len(original_segments),
                )
                tail_count = min(4, 1 + added_count)
            tail = copy.deepcopy(candidate_segments[-tail_count:])
            keep_count = len(original_segments) - tail_count
            first_index = int(
                original_segments[keep_count].get(
                    "index",
                    keep_count + 1,
                )
            )
            for offset, segment in enumerate(tail):
                segment["index"] = first_index + offset
            merged["segments"] = [
                *copy.deepcopy(original_segments[:keep_count]),
                *tail,
            ]
        else:
            keep_count = max(1, len(original_segments) // 2)
            replacement_count = len(original_segments) - keep_count
            tail = copy.deepcopy(candidate_segments[-replacement_count:])
            for offset, segment in enumerate(tail, keep_count):
                segment["index"] = original_segments[offset].get(
                    "index",
                    offset,
                )
            merged["segments"] = [
                *copy.deepcopy(original_segments[:keep_count]),
                *tail,
            ]
    merged["_source"] = str(
        candidate_script.get("_source") or "llm"
    )
    return merged


def generate_script(llm: LLMClient, profile: Dict, text: str, is_script: bool = False,
                    research: Optional[Dict] = None,
                    required_content: str = "") -> Dict:
    """채널 스타일을 반영한 대본을 생성/보정합니다."""
    if not llm.available:
        return _heuristic_script(profile, text, is_script)

    research_txt = _research_block(research)
    required_txt = (
        "[대본 필수 포함 내용]\n"
        + "\n".join(f"- {item}" for item in parse_required_content(required_content))
        + "\n필수 항목은 회수하되 한 비트에 자연스럽게 합쳐도 된다. '분량이 허용된다면/가능하면/선택' "
        "항목은 선택 사항이며 요청 분량을 넘기거나 결말이 과밀해지면 먼저 생략한다. "
        "검색 근거가 부족해도 사용자 입력 범위는 보존하되, 입력에 없는 수치·인과·세부를 덧붙이지 마라.\n"
        if parse_required_content(required_content)
        else ""
    )
    task = (
        "아래 대본을 이 채널 말투(어미 포함)로 다듬어라. "
        "핵심 사실은 유지·교정하고, title/hook 은 [훅 예시] 패턴으로 강화하되, "
        "본문 전개 템플릿을 억지로 끼워 맞추지 마라. "
        "웹 조사 메모의 hard_facts 밖 사건·조건·인용은 추가하지 마라. "
        "소프트 과장은 분위기만.\n"
        f"{required_txt}"
        f"{research_txt}"
        f"[사용자 대본]\n{text}"
        if is_script else
        "아래 주제/메모와 웹 조사 메모로 새 쇼츠 대본을 써라. "
        "채널 말투·강한 훅은 유지하고, drama_beats/hard_facts 의 확인된 갈등은 "
        "한 방으로 살려라(요구→거절→포기 등). "
        "'수출이 어려워져서 포기'처럼 수동 요약하지 마라. "
        "훅 다음을 '사실,' 로 시작하지 마라. "
        "없는 인정 요구·주체 뒤집기만 금지한다. 끝까지 사용자 주제의 주인공을 유지하고, "
        "비슷한 사건의 다른 회사는 사용자가 비교를 요구한 경우가 아니면 대본에서 제외한다.\n"
        f"{required_txt}"
        f"{research_txt}"
        f"[사용자 주제/메모]\n{text}"
    )
    user = f"{_style_context(profile)}\n\n{task}\n\n스키마: {_SCHEMA}"
    try:
        data = llm.complete_json(_SYSTEM_PROMPT, user, temperature=0.85, max_tokens=7000)
        if isinstance(data, dict) and data.get("segments"):
            candidate = preserve_title(_finalize_llm_script(data), text)
            if is_script:
                return candidate
            return polish_story_flow(
                llm,
                profile,
                candidate,
                research=research,
                source_text=text,
                required_content=required_content,
            )
    except LLMQuotaExceeded:
        raise
    except LLMError as exc:
        log.info("대본 생성 LLM 실패, 휴리스틱 사용: %s", exc)
    return _heuristic_script(profile, text, is_script)


def revise_script(
    llm: LLMClient,
    profile: Dict,
    original_script: Dict,
    instruction: str,
    research: Optional[Dict] = None,
    subject_anchors: Optional[List[str]] = None,
    source_text: str = "",
    required_content: str = "",
) -> Dict:
    """완성된 대본을 사용자의 수정 요청 방향대로 다시 다듬습니다."""
    original_segments = original_script.get("segments", []) or []
    original_text = "\n".join(
        (seg.get("text") or "").strip() for seg in original_segments
    ).strip()

    if not llm.available:
        fallback = dict(original_script)
        fallback["_source"] = "unavailable"
        return fallback

    research_txt = _research_block(research)
    subject_lock = ", ".join(subject_anchors or []) or "(자동 추출 없음)"
    task = (
        "아래 '기존 대본'을 사용자의 '수정 요청' 방향대로 다시 써라. "
        "채널 말투·어미·분량 규칙은 그대로 유지하고, 요청에 없는 부분은 최대한 원본의 "
        "내용과 흐름을 보존한다. "
        f"원본 주체 잠금: [{subject_lock}]. 이 회사·제품·인물을 다른 주체로 절대 "
        "교체하지 마라. 웹 조사에 비슷한 사건의 다른 회사나 제품이 섞여도 해당 내용은 "
        "무시한다. 수정 요청이 주체 교체를 명시하지 않는 한 잠금 주체를 제목과 본문에 유지한다. "
        "title/hook 을 건드리면 [훅 예시] 패턴의 짧고 강한 선언형 한 줄로 맞추고 "
        "title 과 hook 은 동일하게 유지한다. "
        "웹 조사 hard_facts 밖 사건·조건·인용·행위주체 변경을 새로 넣지 마라. "
        "잘못된 경위(예: 인정 요구 창작)가 있으면 조사/요청에 맞게 고쳐라.\n\n"
        f"{research_txt}"
        f"[기존 제목]\n{original_script.get('title', '')}\n\n"
        f"[기존 훅]\n{original_script.get('hook', '')}\n\n"
        f"[기존 대본]\n{original_text}\n\n"
        f"[원래 제작 의도 — 이번 수정 요청과 충돌하면 최신 요청 우선]\n{source_text}\n\n"
        f"[필수/선택 항목]\n{required_content}\n\n"
        f"[수정 요청]\n{instruction}"
    )
    user = f"{_style_context(profile)}\n\n{task}\n\n스키마: {_SCHEMA}"
    try:
        data = llm.complete_json(_SYSTEM_PROMPT, user, temperature=0.85, max_tokens=7000)
        if isinstance(data, dict) and data.get("segments"):
            candidate = apply_revision_scope(
                original_script,
                _finalize_llm_script(data),
                instruction,
            )
            return polish_story_flow(
                llm,
                profile,
                candidate,
                instruction=instruction,
                original_script=original_script,
                research=research,
                source_text=source_text,
                required_content=required_content,
            )
        log.info("대본 수정 LLM 응답에 segments 가 없음")
    except LLMQuotaExceeded:
        raise
    except LLMError as exc:
        log.info("대본 수정 LLM 실패: %s", exc)
    fallback = dict(original_script)
    fallback["_source"] = "revise_failed"
    return fallback
