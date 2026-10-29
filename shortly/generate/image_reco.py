"""가이드 컷별 이미지/매체/효과 추천.

학습된 채널의 image_style(사용 이미지 경향) + editing_style(영상·움짤 비율/줌/전환)과
각 세그먼트의 나레이션·의도를 근거로, 컷마다 어떤 이미지를 어떤 매체(정지/영상·움짤)로
어떤 효과와 함께 넣을지 추천한다. LLM 우선, 실패 시 휴리스틱.
"""

from __future__ import annotations

import re
from collections import Counter
from typing import Dict, List, Optional, Tuple

from ..llm.base import LLMClient, LLMError, LLMQuotaExceeded
from ..utils import log
from .editorial import WHOLE_VIDEO_REVIEW_GUIDANCE

_ASSET_TYPES = {
    "real_image",
    "stock_image",
    "generated_image",
    "generated_video",
    "meme",
    "screenshot",
    "manual_required",
}
_PERSON_DEPICTIONS = {
    "none",
    "public_figure",
    "private_person",
    "fictional_person",
}
_MAX_CUTS_PER_BATCH = 8
_MAX_SEGMENTS_PER_BATCH = 4
_PROFILE_TERM_RE = re.compile(r"[0-9A-Za-z가-힣]{3,}")
_STOCK_QUERY_WORD_RE = re.compile(r"[A-Za-z0-9]+(?:[-'][A-Za-z0-9]+)*")
_KOREAN_TEXT_RE = re.compile(r"[가-힣ㄱ-ㅎㅏ-ㅣ]")
_KOREAN_PARTICLES = (
    "으로부터", "에서부터", "에게서", "이라고", "라는", "으로", "에서",
    "에게", "까지", "부터", "처럼", "보다", "하고", "이며", "이고",
    "의", "은", "는", "이", "가", "을", "를", "와", "과", "로",
)
_GENERIC_PROFILE_TERMS = {
    "사진", "이미지", "영상", "장면", "모습", "화면", "자막", "텍스트",
    "클로즈업", "보여주는", "담겨", "있는", "놓여", "손으로", "사람",
    "제품", "브랜드", "포장", "정면", "배경", "강조", "촬영", "음식",
    "늘어나는", "늘어나", "반으로", "여성이", "남성이", "여러", "하나",
    "실제", "관련", "모짜렐라",
}


def _cut_count(seg: Dict) -> int:
    sentence_count = max(1, int(seg.get("sentence_count") or 1))
    sentence_cap = min(sentence_count * 3, 18)
    return max(1, min(int(seg.get("cuts_suggested") or 1), sentence_cap))


def _fit(items: List[str], n: int, filler: str) -> List[str]:
    """리스트를 정확히 n개로 맞춘다(부족하면 filler/마지막값으로 채움)."""
    items = [str(x) for x in (items or []) if str(x).strip()]
    if not items:
        items = [filler]
    while len(items) < n:
        items.append(items[-1])
    return items[:n]


def _heur_media(editing_style: Dict, n: int) -> List[str]:
    """영상·움짤 비율에 맞춰 컷별 매체(사진 vs 영상/움짤) 배분."""
    ratio = float(editing_style.get("motion_clip_ratio") or 0.0)
    n_clip = int(round(n * ratio))
    out: List[str] = []
    for c in range(n):
        out.append("영상/움짤" if c < n_clip else "사진")
    return out


def _heur_effects(editing_style: Dict, n: int) -> List[str]:
    """채널이 실제 쓰는 효과 위주로 컷별 효과 배분."""
    effects = list(editing_style.get("capcut_effects") or [])
    if not effects:
        dz = editing_style.get("dominant_zoom")
        if dz == "zoom_in":
            effects = ["줌 인"]
        elif dz == "zoom_out":
            effects = ["줌 아웃"]
        else:
            effects = ["없음"]
    return [effects[c % len(effects)] for c in range(n)]


def _heur_images(seg: Dict, _image_style: Dict, n: int) -> List[str]:
    """현재 나레이션에만 근거한 안전 폴백(학습 영상 피사체는 재사용 금지)."""
    narration = " ".join(str(seg.get("narration") or "").split())
    if len(narration) > 120:
        narration = narration[:117].rstrip() + "…"
    intent = " ".join(str(seg.get("intent") or "").split())
    subject = narration or intent or "현재 대본의 핵심 내용"
    roles = (
        "핵심 대상",
        "원인과 갈등",
        "구체적인 행동",
        "결과와 반전",
        "주변 맥락",
        "마무리 결과",
    )
    return [
        f"{roles[index % len(roles)]}: 다음 내용을 직접 보여주는 구체적인 장면 — {subject}"
        for index in range(n)
    ]


def _segment_batches(segments: List[Dict]) -> List[List[Dict]]:
    """응답 JSON이 잘리지 않도록 세그먼트를 컷 예산 기준으로 묶는다."""
    batches: List[List[Dict]] = []
    current: List[Dict] = []
    current_cuts = 0
    for segment in segments:
        cuts = _cut_count(segment)
        if current and (
            current_cuts + cuts > _MAX_CUTS_PER_BATCH
            or len(current) >= _MAX_SEGMENTS_PER_BATCH
        ):
            batches.append(current)
            current = []
            current_cuts = 0
        current.append(segment)
        current_cuts += cuts
    if current:
        batches.append(current)
    return batches


def _normalize_profile_term(token: str) -> str:
    value = token.strip().lower()
    for particle in _KOREAN_PARTICLES:
        if value.endswith(particle) and len(value) - len(particle) >= 3:
            return value[:-len(particle)]
    return value


def _profile_specific_terms(image_style: Dict) -> set[str]:
    """여러 학습 예시에 반복된 주제 고유어를 찾아 누출 검사용으로 사용한다."""
    counts: Counter[str] = Counter()
    for subject in image_style.get("subjects") or []:
        terms = {
            _normalize_profile_term(match.group(0))
            for match in _PROFILE_TERM_RE.finditer(str(subject))
        }
        counts.update(
            term for term in terms
            if len(term) >= 3 and term not in _GENERIC_PROFILE_TERMS
        )
    return {term for term, count in counts.items() if count >= 2}


def _profile_leaks(rec: Dict, context: str,
                   profile_terms: set[str]) -> List[str]:
    normalized_context = context.lower()
    images = " ".join(str(item) for item in rec.get("image_suggestions") or [])
    normalized_images = images.lower()
    return sorted(
        term for term in profile_terms
        if term in normalized_images and term not in normalized_context
    )


def _valid_list(value, n: int, predicate) -> bool:
    return (
        isinstance(value, list)
        and len(value) == n
        and all(predicate(item) for item in value)
    )


def _stock_query_error(value: object) -> str:
    """Validate the compact English query used by global stock providers."""
    query = " ".join(str(value or "").split())
    if not query:
        return "검색어 누락"
    if _KOREAN_TEXT_RE.search(query):
        return "한글이 포함됨"
    words = _STOCK_QUERY_WORD_RE.findall(query)
    if not 2 <= len(words) <= 6:
        return "핵심 단어가 2~6개가 아님"
    if not re.search(r"[A-Za-z]", query):
        return "영어 단어가 없음"
    if len(query) > 80:
        return "80자를 초과함"
    return ""


def _validate_recommendation(segment: Dict, rec: Dict,
                             *, context: str,
                             profile_terms: set[str]) -> Tuple[bool, str]:
    """컷 추천의 모든 병렬 배열과 이전 학습 주제 누출을 검증한다."""
    n = _cut_count(segment)
    checks = (
        ("image_suggestions", lambda x: isinstance(x, str) and bool(x.strip())),
        ("media_suggestions", lambda x: str(x) in ("사진", "영상/움짤")),
        ("effect_suggestions", lambda x: isinstance(x, str)),
        ("asset_type_suggestions", lambda x: str(x) in _ASSET_TYPES),
        ("fact_sensitive_suggestions", lambda x: isinstance(x, bool)),
        ("asset_reason_suggestions", lambda x: isinstance(x, str) and bool(x.strip())),
        ("visual_role_suggestions", lambda x: isinstance(x, str) and bool(x.strip())),
        ("visible_text_suggestions", lambda x: isinstance(x, str)),
        ("stock_query_suggestions", lambda x: isinstance(x, str)),
        ("person_depiction_suggestions", lambda x: str(x) in _PERSON_DEPICTIONS),
        ("ai_disclosure_suggestions", lambda x: isinstance(x, bool)),
    )
    for key, predicate in checks:
        if not _valid_list(rec.get(key), n, predicate):
            return False, f"{key} 배열이 컷 수 {n}과 맞지 않음"
    asset_types = rec["asset_type_suggestions"]
    stock_queries = rec["stock_query_suggestions"]
    for index, asset_type in enumerate(asset_types):
        query = str(stock_queries[index]).strip()
        if str(asset_type) == "stock_image":
            error = _stock_query_error(query)
            if error:
                return False, f"stock_image 컷 {index}의 영어 검색어 오류: {error}"
        elif query:
            return False, f"stock_image가 아닌 컷 {index}에 스톡 검색어가 포함됨"
    leaks = _profile_leaks(rec, context, profile_terms)
    if leaks:
        return False, "현재 주제에 없는 학습 영상 고유어 포함: " + ", ".join(leaks[:3])
    return True, ""


def _recommend_llm(llm: LLMClient, image_style: Dict, editing_style: Dict,
                   guide: Dict, segments: Optional[List[Dict]] = None) -> Dict:
    """지정된 소규모 세그먼트 묶음의 추천을 생성한다."""
    segments = segments if segments is not None else (guide.get("segments") or [])
    # Recommendation batches still need the preceding referents and later reveals.
    story_context = "\n".join(
        f"- index {s.get('index')}: {s.get('narration') or ''}"
        for s in (guide.get("segments") or segments)
    )
    seg_lines = []
    for s in segments:
        change_reasons = s.get("cut_change_reasons") or []
        seg_lines.append(
            f"- index {s.get('index')}: 컷 {_cut_count(s)}개 / 의도 "
            f"'{s.get('intent', '')}' / 자막 '{s.get('caption', '')}' / "
            f"나레이션 \"{s.get('narration') or ''}\" / "
            f"화면 전환 경계 {change_reasons}"
        )
    system = (
        "너는 유튜브 쇼츠 편집 디렉터다. 학습된 채널의 이미지/편집 경향에 맞춰 각 "
        "세그먼트의 컷마다 넣을 이미지, 매체(사진|영상/움짤), 효과와 에셋 확보 방식을 "
        "추천한다. 효과와 매체는 반드시 채널이 실제로 쓰는 범위에서 고른다. "
        "학습 영상에 등장했던 구체적인 브랜드·제품·인물·사건은 현재 대본에 명시된 "
        "경우가 아니면 절대 추천에 재사용하지 않는다. "
        "사실성은 이야기 자체가 사실인지가 아니라 그 화면이 실제 원본·동일한 외형이어야 "
        "하는지를 기준으로 판단한다. 반드시 JSON 하나만 출력한다."
    )
    schema = (
        '{"segments": [{"index": number, '
        '"image_suggestions": string[], "media_suggestions": string[], '
        '"effect_suggestions": string[], '
        '"asset_type_suggestions": '
        '("real_image"|"stock_image"|"generated_image"|"generated_video"|'
        '"meme"|"screenshot"|"manual_required")[], '
        '"fact_sensitive_suggestions": boolean[], '
        '"asset_reason_suggestions": string[], '
        '"visual_role_suggestions": string[], '
        '"visible_text_suggestions": string[], '
        '"stock_query_suggestions": string[], '
        '"person_depiction_suggestions": '
        '("none"|"public_figure"|"private_person"|"fictional_person")[], '
        '"ai_disclosure_suggestions": boolean[]}]} '
        "(각 배열 길이는 그 세그먼트의 컷 수와 정확히 같게)"
    )
    user = (
        f"[프로젝트 맥락] 제목 '{guide.get('title', '')}' / 훅 "
        f"'{guide.get('hook', '')}'\n"
        f"[전체 이야기 맥락 — 참조 전용]\n{story_context}\n"
        f"위 내용은 지시어·상품 정체·정보 공개 순서를 파악하기 위한 것이다. "
        f"출력은 아래 요청된 세그먼트만 작성하고, 뒤에서 공개할 정보를 앞 컷에 그리지 않는다.\n"
        f"{WHOLE_VIDEO_REVIEW_GUIDANCE}\n"
        f"각 추천의 asset_reason_suggestions에는 이 컷의 사건 전달 역할을 적는다. "
        f"제품 홍보컷이 연속되면 대사 명사가 맞아도 전체 광고 인상을 재검토한다. "
        f"확보하지 못한 사건 자료를 가짜 현장이나 가짜 문서로 만들어 해결하지 않는다.\n"
        f"[채널 이미지 형식] 자주 쓰는 일반 형식 "
        f"{image_style.get('common_types', [])}. 학습 영상의 구체적인 피사체 예시는 "
        f"현재 주제와 무관하므로 재사용 금지.\n"
        f"[채널 편집 경향] 영상·움짤 비율 {editing_style.get('motion_clip_ratio')}, "
        f"줌 {editing_style.get('dominant_zoom')}, 전환 "
        f"{editing_style.get('transition_types')}, 권장 효과 "
        f"{editing_style.get('capcut_effects')}\n"
        f"[세그먼트]\n" + "\n".join(seg_lines) + "\n\n"
        f"매체는 '사진' 또는 '영상/움짤'로만. 효과는 '줌 인/줌 아웃/페이드 인/페이드 아웃/없음' 등 "
        f"채널 실제 사용 효과 위주.\n\n"
        f"[쇼츠 화면 연출 원칙]\n"
        f"- 모든 세그먼트를 하나의 연속 스토리보드로 본다. 훅은 즉시 이해되는 이상한 상황, "
        f"정체 공개는 실제 대상, 갈등은 인물의 행동·표정, 선택은 구체적 동작, 결말은 결과가 "
        f"보이는 장면으로 시각적 역할을 바꾼다. 단, 문장 중간에 임의로 새 화면을 만들지 않는다.\n"
        f"- 한 문장은 하나의 주 화면을 유지하는 것이 기본이다. 두 번째·세 번째 컷은 위에 제공된 "
        f"'화면 전환 경계'에서 실제 대상·행동·결과가 바뀔 때만 사용한다. 같은 문장 안의 컷은 "
        f"인물·제품·장소·색감의 연속성을 유지하고, 경계 이후 내용을 앞 컷에 미리 보여주지 않는다.\n"
        f"- 각 컷 프롬프트에 '현재 보여줄 것:'과 '아직 보여주지 않을 것:'을 명시한다. "
        f"같은 문장이라도 앞 절이 초록·보라 제품만 소개하고 다음 절이 파랑·주황 출시를 말하면 "
        f"앞 컷에는 초록·보라만 허용하고 파랑·주황 제품은 다음 컷부터 공개한다. "
        f"여러 출시 시기·버전·결과를 하나의 제품군 사진에 미리 합치지 않는다.\n"
        f"- 단어가 아니라 맥락을 시각화한다. '이를 음식에 그렸다'에서 '이를'이 컬러 케첩이면 "
        f"평범한 핫도그가 아니라 그 시점에 허용된 컬러 케첩 무늬가 실제로 보이는 핫도그를 추천한다. "
        f"핵심 수식어·대상·행동이 화면에 없는 명사 일치만으로 적합하다고 판단하지 않는다.\n"
        f"- 먼저 나레이션을 제공된 문장·절 경계대로 배경·갈등·선택·결과 같은 의미 단위로 나눈 뒤 "
        f"각 컷에 하나씩 배분한다. "
        f"visual_role_suggestions에는 각 컷이 담당하는 의미를 짧게 쓴다. 컷 수가 의미 단위보다 적으면 "
        f"주제 고유성이 가장 큰 갈등과 결과를 우선하고, 배경 설명은 자막에 맡긴다.\n"
        f"- 국가·지역·브랜드·제품·인물·쟁점 같은 고유 앵커와 요구·거부·유지·포기·중단·회수 같은 "
        f"결정적 행동을 추출해 컷 전체에 분산한다. 최소한 각 추천에는 '무엇/누가', '어떤 행동·상태', "
        f"'어떤 장소·맥락' 중 두 가지가 구체적으로 드러나야 한다. '수출 물류', '회의 장면', "
        f"'대표가 서명하는 모습'처럼 다른 이야기에도 그대로 쓸 수 있는 범용 문구만으로 끝내지 않는다.\n"
        f"- 무음 테스트를 적용한다. 자막을 가렸을 때도 시청자가 최소한 이 이야기의 대상과 갈등 또는 "
        f"결과 중 하나를 추론할 수 있어야 한다. 특정 국가·제품·쟁점이 사라져 아무 수출·아무 회사 "
        f"이야기로 보이면 추천을 다시 구체화한다.\n"
        f"- '하지만/면서도/결국/대신/포기/중단'처럼 대비와 인과를 만드는 표현이 있으면 앞뒤를 서로 "
        f"다른 화면으로 보여준다. 특히 마지막 컷은 서명하는 사람의 표정 같은 모호한 반응보다 "
        f"유지된 것·사라진 것·멈춘 행동 등 결과가 확인되는 장면을 우선한다.\n"
        f"- 한 세그먼트에서 창고·상자·팔레트·컨테이너처럼 같은 상위 개념의 B-roll을 두 컷 이상 "
        f"연속 추천하지 않는다. 배경용 범용 B-roll은 세그먼트당 최대 한 컷만 허용한다.\n"
        f"- 같은 제품 정면 사진·같은 인물 구도·같은 회의 장면을 연속 두 컷 넘게 반복하지 않는다. "
        f"제품 클로즈업은 정체 공개와 필요한 마무리에 우선 사용하고, 중간에는 손동작·공간·반응·"
        f"물류·소비 장면처럼 샷 크기와 행동을 바꾼다.\n"
        f"- 생성 영상은 이동·제조·운반·사람의 반응처럼 실제 움직임이 정보나 감정을 강화할 때만 "
        f"추천한다. 정적인 제품·문서·수치 근거는 영상으로 억지 변환하지 않는다.\n"
        f"- 음식의 김·수증기는 기본 연출에서 제외한다. 맛있어 보이게 하거나 따뜻한 분위기를 만들기 "
        f"위해 '김이 모락모락', 'steaming' 같은 지시를 image_suggestions에 넣지 않는다. "
        f"전자레인지로 데운 김밥은 문 열기·접시 꺼내기 등 동작으로 표현하고 김을 추가하지 않는다. "
        f"대사와 해당 컷의 시각적 역할에서 뜨거운 상태를 보여 주는 것이 반드시 필요할 때만 예외다. "
        f"예: 컵라면에 뜨거운 물을 붓는 조리 과정에서 물의 온도를 전달해야 할 때는 약하고 자연스러운 "
        f"김을 허용한다. 음식 종류나 '데웠다'는 언급만으로 예외를 적용하지 않는다. "
        f"예외 컷은 asset_reason_suggestions에 온도 표현이 꼭 필요한 이유를 명시한다. "
        f"김이 허용되어도 과도한 증기·연기·거품·끓어오름을 덧붙이지 않는다.\n"
        f"- generated_video는 3~5초 안에 안정적으로 끝나는 대표 행동 하나만 담는다. 기본값은 전경의 "
        f"주요 인물 한 명, 주요 소품 한 종류, 관찰 가능한 핵심 동작 한 가지다. 배경 인물은 최대 두 명으로 "
        f"제한하고 메모·관찰처럼 작고 단순한 행동만 시킨다. 여러 사람이 각자 음식·도구를 들고 동시에 "
        f"다른 행동을 하는 장면은 만들지 않는다.\n"
        f"- 탄산·액체가 보이는 generated_video는 '탄산이 있음' 같은 정적 묘사로 끝내지 않는다. "
        f"필요한 경우 작은 기포가 아래에서 위로 올라가 수면에서 사라지고 새 기포가 생기는 "
        f"시간에 따른 변화를 구체적으로 적는다. 전체 기포가 정지한 채 손만 움직이거나 기포 무늬가 "
        f"반복되는 결과는 채택하지 않는다. 기포가 서사에 불필요하면 잔을 빼고 단일 제품 동작을 "
        f"택하거나 실물 사진을 추천한다. 프롬프트만으로 검수 완료를 주장하지 않는다.\n"
        f"- '집는다→먹는다→뱉는다→컵을 든다→기록한다'처럼 손·입·소품이 연속으로 바뀌는 다단계 "
        f"동작을 한 영상에 넣지 않는다. 대사에 행동이 여러 개 있어도 전부 문자 그대로 재현하지 말고, "
        f"가장 중요한 한 순간이나 행동 뒤의 표정·결과 하나만 선택한다. 필요하면 서로 다른 컷으로 나눈다.\n"
        f"- 한 손이 짧은 시간 안에 서로 다른 물체를 차례로 다루게 하지 않는다. 영상 내내 유지되어야 할 "
        f"음식·제품·도구는 처음부터 끝까지 같은 형태·색·재질·위치를 유지한다고 명시하고, 의도한 변형이 "
        f"아니면 다른 물체로 변하거나 합쳐지거나 사라지는 장면을 금지한다. 복잡한 섭취·뱉기·붓기·쪼개기 "
        f"동작은 서사상 꼭 필요하지 않으면 표정, 놓인 결과물 또는 단순한 손동작으로 대체한다.\n"
        f"- generated_image/video의 image_suggestions는 그대로 생성 모델에 넣을 수 있는 제작 프롬프트로 "
        f"작성한다. 피사체의 수와 정체, 색·재질·외형, 행동, 장소, 카메라 거리·각도, 전경/배경 배치, "
        f"조명과 감정, 앞뒤 컷과 이어져야 할 요소를 구체적으로 선언한다. '마트 진열대', '제품 사진'처럼 "
        f"짧고 범용적인 명사구만 쓰지 않는다.\n"
        f"- 생성 컷에 포장지·간판·메뉴·상자처럼 글자가 자연스럽게 보이는 물체가 있으면 "
        f"visible_text_suggestions에 화면에 허용할 정확한 문자열과 위치를 쓴다. 예: "
        f"'모든 초록색 김 봉지 정면 중앙에 정확히 \"김\" 한 글자만 같은 흰색 고딕체로 표기; "
        f"그 밖의 글자·숫자·로고 없음'. image_suggestions에도 '화면 글자 지시:'라는 표식 뒤에 "
        f"그 내용을 포함한다. 임의의 브랜드명이나 확인되지 않은 문구는 만들지 않는다.\n"
        f"- 같은 상품이 여러 개 보이면 모든 포장의 색, 도형, 라벨 위치, 철자와 서체가 완전히 같다고 "
        f"명시한다. 실제 브랜드 외형이 핵심이면 무지 용기나 가상 포장으로 바꾸지 않는다. 정적인 제품 "
        f"원본이 적합하면 real_image로 분류하고, 실제 제품이 등장하며 움직임이 필요한 영상은 "
        f"manual_required와 '영상/움짤'로 분류해 제품 사진을 참조 이미지로 제공하는 수동 생성을 요구한다. "
        f"제품 정체가 장면의 의미와 무관한 생성형 재연에서만 "
        f"짧은 일반명 하나만 정확히 선언하고 나머지 읽을 수 있는 글자는 금지한다.\n"
        f"- 실존 상품은 해당 시대·국가·버전의 실물 사진을 먼저 확인한다. 용기 실루엣·비율·재질·"
        f"뚜껑·노즐·라벨·로고를 보존하며, 브랜드명만 적거나 참조를 첨부했다는 이유로 외형 검증이 "
        f"끝났다고 간주하지 않는다. '작은 손에도 잡기 쉬운 병', '좁은 입구'처럼 외형 자체를 "
        f"설명하는 정적 컷은 real_image / fact_sensitive=true로 원본 디테일을 보여준다. "
        f"실물 참조를 확보하지 못하면 manual_required로 남기고 원통형 무지 용기로 대체하지 않는다. "
        f"참조 기반 생성이 필요하면 asset_reason_suggestions에 보존할 외형과 생성 후 실물 대조 "
        f"검수 필요를 명시한다. 당시에 없던 포장·다음에 소개할 색상은 추가하지 않는다.\n"
        f"- 이 실물 기준은 브랜드뿐 아니라 주제인 음식·식재료·사물에도 적용한다. 꼬북칩의 네 겹처럼 "
        f"윤곽·비율·두께·층·표면이 의미를 만드는 대상은 실제 사진을 확보하고 생성 요청에 실제 참조 "
        f"입력으로 전달해야 한다. photorealistic이나 제품명을 적는 것만으로는 참조 입력이 아니다. "
        f"참조를 제공할 수 없는 자동 생성 경로라면 real_image 또는 manual_required로 추천한다. "
        f"asset_reason_suggestions에는 필요한 실제 참조와 비교할 핵심 특징을 구체적으로 적는다. "
        f"품종 미상 딸기/바나나나 추측한 생산 설비를 특정 품종·실제 공정의 증거로 쓰지 않는다.\n"
        f"- 화면에 글자가 필요 없는 컷의 visible_text_suggestions는 '없음'으로 쓰고, "
        f"image_suggestions에는 '화면 글자 지시: 읽을 수 있는 글자 없음, 글자처럼 보이는 무의미한 획도 없음'을 "
        f"포함한다.\n"
        f"- 기본 화면은 인물의 표정·행동, 실제 제품 디테일, 공간, 손동작, 상황 재연처럼 "
        f"즉시 이해되는 한 장면이다. 카드뉴스·정보 카드·발언 카드·포스터·UI 형태를 기본값으로 쓰지 않는다.\n"
        f"- 발언을 강조할 때는 화자의 감정을 보여 주는 인물/상황 장면을 쓰고, 정확한 발언 문구는 "
        f"기존 CapCut 자막이 담당한다. 생성 이미지 안에 인용문이나 자막을 넣지 않는다.\n"
        f"- 생성형 AI에 그래프·차트·표·수치 카드·뉴스 화면·문서·지도·국경·영토 위치를 그리게 하지 않는다. "
        f"수치와 문장은 CapCut 자막으로 표시하고, 화면은 실제 기사 캡처나 관련 실사 장면으로 대체한다. "
        f"지도·제품 표기처럼 위치와 외형이 정확해야 하면 원본 이미지/스크린샷/수동 확보를 선택한다.\n\n"
        f"[에셋 분류 원칙]\n"
        f"- generated_image/video: 실제 기록 화면이 존재하지 않는 재현·연상·상황극·상징·합성·과장 연출. "
        f"사실을 설명하는 대본이어도 화면이 재현 장면이면 생성형을 선택한다.\n"
        f"- real_image: 실제 제품, 포장, 로고 또는 얼굴 자체가 이야기의 핵심인 널리 알려진 공인처럼 "
        f"외형이 정확해야 하고 사용 가능한 원본 사진을 확보할 가치가 큰 경우.\n"
        f"- stock_image: 세계 스톡 사이트에서 흔히 검색되는 일반 도시·거리·풍경·손·사무실처럼 "
        f"특정 지역·문화·브랜드의 정확한 재현이 필요 없는 장면에만 사용한다.\n"
        f"- 한국 고유 제품·가게·가정·학교·회사·인물 분위기처럼 한국적 디테일이 결과의 핵심인데 "
        f"세계 스톡 사이트에서 정확히 찾기 어려운 장면은 stock_image로 분류하지 않는다. "
        f"실제 원본이 필요 없으면 generated_image/video, 실제 외형이 중요하면 real_image 또는 "
        f"manual_required를 선택한다. 단, 서울 전경·경복궁·한강처럼 널리 촬영된 명소와 일반 풍경은 "
        f"검색 가능성이 높으면 stock_image를 사용할 수 있다.\n"
        f"- screenshot/meme: 원본 화면 캡처 또는 사람이 맥락에 맞게 고를 밈.\n"
        f"- manual_required: 원본 영상 등 위 유형으로 처리할 수 없어 사람이 직접 확보해야 하거나, "
        f"실제 제품 참조 이미지를 외부 생성 도구에 넣어 포장·로고·용기 형태를 보존한 채 수동 생성해야 "
        f"하는 경우에 사용한다. 실제 제품이 필요한 장면을 무지·무브랜드 제품으로 자동 생성하지 않는다.\n"
        f"- fact_sensitive=true: 실제 제품·포장·로고·특정 실존 인물·문서처럼 원본과 동일해야 할 때만 사용한다. "
        f"가상 재현 배우나 '협상 장면을 연상시키는 인물'에는 false다.\n"
        f"- person_depiction: 인물이 없으면 none. 대중이 얼굴을 알아보는 연예인·고위 공직자·전국적으로 "
        f"잘 알려진 대기업 총수 등 얼굴 식별이 서사에 중요한 경우만 public_figure다. 일반 회사 대표·점주·"
        f"창업자·인터뷰이는 이름이 기사에 나와도 private_person으로 본다. 실존 인물과 닮지 않은 재연 배우는 "
        f"fictional_person이다.\n"
        f"- private_person은 초상·인터뷰 원본 사진을 기본 추천하지 않는다. 인물의 감정·행동이 장면에 도움이 "
        f"되면 특정 실존 인물과 닮지 않은 fictional_person의 generated_image/video로 바꾸고, 필요 없으면 "
        f"제품·공간·손동작 등 다른 장면으로 교체한다. 이재용처럼 얼굴 자체가 대중적 맥락인 경우는 "
        f"public_figure 예외가 될 수 있다.\n"
        f"- ai_disclosure=true는 가상의 인물로 실존 인물의 역할·상황을 재연하는 생성 이미지/영상에만 쓴다. "
        f"단순 사물·배경 생성에는 false다.\n"
        f"- 예: '일본 바이어 미팅 또는 협상 장면 연상 인물 사진'은 "
        f"generated_image / fact_sensitive=false다.\n"
        f"- 예: '일본이 큰 수출 시장이지만 독도 표기를 지우느니 수출을 포기했다'는 세 컷을 "
        f"'수출 상자 창고 / 팔레트 운반 / 대표 서명'으로 반복하지 않는다. "
        f"'일본 판매처의 한국 김 진열로 시장 맥락 제시 / 실제 제품 포장의 독도 표기 클로즈업 / "
        f"일본행 출하가 멈춘 결과'처럼 시장·쟁점·결과를 한 컷씩 담당시킨다. 실제 독도 표기 외형은 "
        f"real_image 또는 screenshot으로 확보하고 생성형 AI가 임의로 그리지 않게 한다.\n"
        f"- 예: 이름이 덜 알려진 회사 대표의 발언 장면은 특정 얼굴을 복제하지 않은 가상 대표가 단호하게 "
        f"말하는 generated_image / fictional_person / fact_sensitive=false / ai_disclosure=true다. "
        f"발언 문구는 이미지가 아니라 CapCut 자막으로 보여준다.\n"
        f"- asset_reason_suggestions에는 선택 근거를 한 문장으로 쓴다.\n"
        f"- stock_query_suggestions는 stock_image 컷마다 Openverse·Pixabay·Pexels에서 실제 검색할 "
        f"간결한 영어 명사구를 반드시 하나씩 쓴다. 검색어는 영어 핵심 단어 2~6개로 제한하며 "
        f"한국어 번역문, 완전한 문장, 카메라 연출, 감정, 자막 문구를 넣지 않는다. 장소가 검색 결과를 "
        f"좌우하면 Vietnam/Hanoi/Korea/Seoul 같은 영문 지역명을 포함하고, 일반적인 장면이면 피사체와 "
        f"장소 중심으로 쓴다. 예: 'Vietnam tropical city street', 'Dokdo island sea rocks'. "
        f"stock_image가 아닌 컷은 반드시 빈 문자열로 쓴다.\n"
        f"스키마: {schema}"
    )
    data = llm.complete_json(system, user, max_tokens=6000)
    out: Dict = {}
    if isinstance(data, dict):
        for item in data.get("segments", []) or []:
            if isinstance(item, dict) and item.get("index") is not None:
                out[int(item["index"])] = item
    return out


def recommend_cuts(llm: LLMClient, profile: Dict, guide: Dict) -> Dict:
    """가이드 각 세그먼트에 image/media/effect 추천을 채워 넣고 guide 를 반환."""
    image_style = profile.get("image_style") or {}
    editing_style = profile.get("editing_style") or {}
    segments = guide.get("segments") or []
    if not segments:
        return guide

    llm_reco: Dict = {}
    errors: List[Dict] = []
    batch_count = 0
    retry_count = 0
    context = " ".join(
        [
            str(guide.get("title") or ""),
            str(guide.get("hook") or ""),
            *[str(segment.get("narration") or "") for segment in segments],
        ]
    )
    profile_terms = _profile_specific_terms(image_style)
    if getattr(llm, "available", False):
        for batch in _segment_batches(segments):
            batch_count += 1
            pending = list(batch)
            for attempt in range(2):
                indices = [segment.get("index") for segment in pending]
                try:
                    raw = _recommend_llm(
                        llm,
                        image_style,
                        editing_style,
                        guide,
                        segments=pending,
                    )
                except LLMQuotaExceeded:
                    raise
                except (LLMError, ValueError) as exc:
                    error = {
                        "batch": batch_count,
                        "attempt": attempt + 1,
                        "segment_indices": indices,
                        "error_type": type(exc).__name__,
                        "message": str(exc)[:240],
                    }
                    errors.append(error)
                    log.warning(
                        "컷별 추천 LLM 실패 batch=%s attempt=%s segments=%s: %s",
                        batch_count,
                        attempt + 1,
                        indices,
                        exc,
                    )
                    if attempt == 0:
                        retry_count += 1
                    continue

                invalid: List[Dict] = []
                for segment in pending:
                    index = segment.get("index")
                    rec = raw.get(index, {})
                    valid, reason = _validate_recommendation(
                        segment,
                        rec,
                        context=context,
                        profile_terms=profile_terms,
                    )
                    if valid:
                        llm_reco[index] = rec
                    else:
                        invalid.append(segment)
                        errors.append(
                            {
                                "batch": batch_count,
                                "attempt": attempt + 1,
                                "segment_indices": [index],
                                "error_type": "RecommendationValidationError",
                                "message": reason,
                            }
                        )
                        log.warning(
                            "컷별 추천 검증 실패 batch=%s attempt=%s segment=%s: %s",
                            batch_count,
                            attempt + 1,
                            index,
                            reason,
                        )
                pending = invalid
                if not pending:
                    break
                if attempt == 0:
                    retry_count += 1
    else:
        errors.append(
            {
                "batch": 0,
                "attempt": 0,
                "segment_indices": [segment.get("index") for segment in segments],
                "error_type": "LLMUnavailable",
                "message": "컷 추천 모델을 사용할 수 없어 안전 폴백을 사용했습니다.",
            }
        )

    for seg in segments:
        n = _cut_count(seg)
        rec = llm_reco.get(seg.get("index"), {}) if isinstance(seg.get("index"), int) else {}
        images = rec.get("image_suggestions") or _heur_images(seg, image_style, n)
        media = rec.get("media_suggestions") or _heur_media(editing_style, n)
        effects = rec.get("effect_suggestions") or _heur_effects(editing_style, n)
        seg["image_suggestions"] = _fit(images, n, "핵심 장면 이미지")
        seg["media_suggestions"] = _fit(media, n, "사진")
        seg["effect_suggestions"] = _fit(effects, n, "없음")
        if not rec:
            for key in (
                "asset_type_suggestions",
                "fact_sensitive_suggestions",
                "asset_reason_suggestions",
                "visual_role_suggestions",
                "visible_text_suggestions",
                "stock_query_suggestions",
                "person_depiction_suggestions",
                "ai_disclosure_suggestions",
            ):
                seg.pop(key, None)
            seg["asset_classification_source"] = "heuristic_fallback"
            seg["visual_recommendation_warning"] = (
                "상세 컷 추천 모델 실패로 현재 나레이션 기반 안전 폴백을 사용했습니다."
            )
        else:
            seg.pop("visual_recommendation_warning", None)
        asset_types = rec.get("asset_type_suggestions")
        fact_flags = rec.get("fact_sensitive_suggestions")
        reasons = rec.get("asset_reason_suggestions")
        visual_roles = rec.get("visual_role_suggestions")
        stock_queries = rec.get("stock_query_suggestions")
        visible_texts = rec.get("visible_text_suggestions")
        person_depictions = rec.get("person_depiction_suggestions")
        disclosures = rec.get("ai_disclosure_suggestions")
        if (
            isinstance(asset_types, list)
            and len(asset_types) == n
            and all(str(item) in _ASSET_TYPES for item in asset_types)
            and isinstance(fact_flags, list)
            and len(fact_flags) == n
            and all(isinstance(item, bool) for item in fact_flags)
        ):
            seg["asset_type_suggestions"] = [
                str(item) for item in asset_types
            ]
            seg["fact_sensitive_suggestions"] = list(fact_flags)
            seg["asset_reason_suggestions"] = _fit(
                reasons if isinstance(reasons, list) else [],
                n,
                "생성 모델이 컷의 문맥과 확보 가능성을 기준으로 분류했습니다.",
            )
            normalized_visible_texts = _fit(
                visible_texts if isinstance(visible_texts, list) else [],
                n,
                "없음",
            )
            seg["visible_text_suggestions"] = normalized_visible_texts
            for index, asset_type in enumerate(
                seg["asset_type_suggestions"]
            ):
                if asset_type not in ("generated_image", "generated_video"):
                    continue
                prompt = seg["image_suggestions"][index]
                if "화면 글자 지시:" in prompt:
                    continue
                directive = normalized_visible_texts[index].strip()
                if not directive or directive == "없음":
                    directive = (
                        "읽을 수 있는 글자 없음, 글자처럼 보이는 무의미한 획도 없음"
                    )
                seg["image_suggestions"][index] = (
                    f"{prompt.rstrip(' .')}. 화면 글자 지시: {directive}"
                )
            if (
                isinstance(visual_roles, list)
                and len(visual_roles) == n
                and all(isinstance(item, str) for item in visual_roles)
            ):
                seg["visual_role_suggestions"] = [
                    str(item).strip() for item in visual_roles
                ]
            if (
                isinstance(stock_queries, list)
                and len(stock_queries) == n
                and all(isinstance(item, str) for item in stock_queries)
            ):
                seg["stock_query_suggestions"] = [
                    str(item).strip() for item in stock_queries
                ]
            if (
                isinstance(person_depictions, list)
                and len(person_depictions) == n
                and all(
                    str(item) in _PERSON_DEPICTIONS
                    for item in person_depictions
                )
            ):
                normalized_people = [
                    str(item) for item in person_depictions
                ]
                normalized_disclosures = (
                    list(disclosures)
                    if (
                        isinstance(disclosures, list)
                        and len(disclosures) == n
                        and all(
                            isinstance(item, bool)
                            for item in disclosures
                        )
                    )
                    else [False] * n
                )
                for index, depiction in enumerate(normalized_people):
                    if (
                        depiction == "private_person"
                        and seg["asset_type_suggestions"][index]
                        in ("real_image", "generated_image")
                    ):
                        normalized_people[index] = "fictional_person"
                        seg["asset_type_suggestions"][index] = (
                            "generated_image"
                        )
                        seg["fact_sensitive_suggestions"][index] = False
                        seg["image_suggestions"][index] = (
                            "실존 인물과 닮지 않은 가상의 한국인 기업 대표가 "
                            "단호하고 당당하게 말하는 자연스러운 재연 장면, "
                            "실제 인물의 얼굴·외형 복제와 이미지 내 글자 금지"
                        )
                        normalized_disclosures[index] = True
                    if (
                        normalized_people[index] == "fictional_person"
                        and seg["asset_type_suggestions"][index]
                        in ("generated_image", "generated_video")
                    ):
                        normalized_disclosures[index] = True
                seg["person_depiction_suggestions"] = normalized_people
                seg["ai_disclosure_suggestions"] = normalized_disclosures
            seg["asset_classification_source"] = "generation_llm"
    failed_indices = [
        segment.get("index")
        for segment in segments
        if segment.get("index") not in llm_reco
    ]
    if not failed_indices:
        status = "complete"
        source = "generation_llm"
    elif len(failed_indices) == len(segments):
        status = "fallback"
        source = "heuristic_fallback"
    else:
        status = "partial"
        source = "mixed"
    guide["visual_recommendation"] = {
        "status": status,
        "source": source,
        "model": str(getattr(llm, "text_model", "") or ""),
        "batch_count": batch_count,
        "retry_count": retry_count,
        "failed_segment_indices": failed_indices,
        "errors": errors,
    }
    return guide
