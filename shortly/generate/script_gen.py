"""주제→스타일 반영 대본 생성 / 대본 입력 시 스타일 보정."""

from __future__ import annotations

import copy
import json
import re
from typing import Dict, List, Optional

from ..llm.base import LLMClient, LLMError
from ..utils import log


def _split_sentences(text: str) -> List[str]:
    parts = re.split(r"(?<=[.!?。！？\n])\s+", text.strip())
    return [p.strip() for p in parts if p.strip()]


def _chars_no_space(text: str) -> int:
    return len(re.sub(r"\s", "", text or ""))


def _heuristic_script(profile: Dict, text: str, is_script: bool) -> Dict:
    hook_examples = profile.get("hook_examples", [])
    if is_script:
        sentences = _split_sentences(text)
        hook = sentences[0] if sentences else text[:30]
    else:
        hook = (hook_examples[0] if hook_examples else "이거 진짜 실화냐?")
        sentences = [hook, f"{text} 에 대해 지금부터 보여드릴게요.",
                     "핵심은 바로 이겁니다.", "결과는 예상 밖이었죠.",
                     "끝까지 보면 반전이 있습니다."]
    segments = [{"index": i, "text": s} for i, s in enumerate(sentences)]
    return {
        "title": hook,
        "hook": hook,
        "segments": segments,
        "_source": "heuristic",
    }


_SYSTEM_PROMPT = (
    "너는 유튜브 쇼츠 대본 작가다. 아래 '채널 스타일'과 '실제 대사 예시'를 충실히 모방해 "
    "대본을 만든다. 단, 채널 스타일은 훅·말투·속도 같은 표면 스타일에만 적용하고 "
    "이야기 구조·논리·가독성은 아래 품질 원칙을 우선한다. 목표는 도파민·호기심을 "
    "터뜨려 시청자가 끝까지 보게 만드는 것이다. "
    "각 세그먼트는 한 호흡에 읽을 짧은 문장으로 나눈다.\n"
    "가장 중요(이야기 구조): 훅→정체 공개→구체적 상황→선택/대치→대가→반전/보상 순으로 "
    "정보를 공개하되, 소재에 맞지 않는 단계는 합치거나 생략한다. 각 세그먼트는 앞 문장에 없던 "
    "새 정보나 감정 변화를 하나씩 추가해야 한다. 인접 세그먼트에서 같은 사실을 표현만 바꿔 "
    "반복하지 마라. 마지막은 훅에서 약속한 궁금증을 가장 강한 검증 사실 하나로 회수한다.\n"
    "가장 중요(말투): [실제 대사 예시]와 [자주 쓰는 문장 끝맺음]은 참고하되 오류·잘린 문장·"
    "불필요한 반복은 모방하지 마라. "
    "이 채널은 '썰 풀어주는' 반말 나레이션체다. '~다고 함', '~했는데', '~다는데', '~라고' 같은 "
    "실제 어미를 사용하고, '~입니다', '~습니다', '~해요', '~하죠', '~됩니다' 같은 정중한 리뷰/설명체는 "
    "피한다. 연결어는 논리 관계가 실제로 바뀔 때만 쓰고 '근데/그래서/하지만/그리고/사실'을 "
    "리듬용으로 강제하지 마라. 쉼표로 세 가지 이상 정보를 한 문장에 몰아넣지 마라. "
    "지정된 [목표 대사 분량]의 글자수·문장 수 범위도 최대한 맞춘다.\n"
    "가장 중요(제목/훅): title 과 hook 은 같은 한 줄로 써라. [훅 예시]에서는 "
    "길이·압축감·자극의 온도만 참고하고 단어·문장 구조를 기계적으로 복제하지 마라. "
    "짧고 강한 선언형/반전예고형([과장·행위·결과] + [브랜드/음식])을 우선하고, "
    "질문형(?), '이유', '진실', '정체', '알아보자', '뜻밖의' 같은 설명·정보형 제목은 피하라. "
    "훅의 과장·임팩트 표현(털려버린, 기강 잡아버린, 해킹한, 큰일났다는 등)은 이 채널의 핵심이니 "
    "약화하지 마라. 길이는 [목표 훅 길이]에 맞춘다. "
    "훅 직후 본문을 '사실,' 로 시작하지 마라. 바로 장면·갈등·디테일로 들어가라.\n"
    "가장 중요(환각 금지 ≠ 재미 삭제): 하드 팩트/드라마 비트는 [사용자 입력]과 [웹 조사 메모]에 "
    "있는 것만 사건으로 쓴다. 확인된 갈등(예: 일본 측이 독도 표기 수정을 요구·요청 → 회사가 거부·고수 "
    "→ 일본 수출/시장 포기)이 조사에 있으면 그걸 쇼츠의 한 방으로 살리고, "
    "'수출이 어려워져서 포기했다'처럼 수동·요약으로 죽이지 마라. "
    "금지하는 건 없는 사건 창작과 주체 뒤집기·'인정하라' 창작이지, "
    "스니펫에 있는 요구→거절 서사를 빼는 것이 아니다. "
    "세부 대화문·금액·날짜가 없으면 그 세부만 생략하고 갈등 골격은 유지하라. "
    "소프트 과장은 분위기·관심도에만 허용.\n"
    "가장 중요(수치 신뢰성): 매출·수출액·비율·증가율·가격·인원 등 숫자는 "
    "[출처·범위가 정리된 수치]에 주체·범위·기준연도·단위·출처가 모두 정리된 항목만 사용한다. "
    "숫자를 쓸 때는 전국/지역, 전체 품목/세부 품목, 업계 전체/특정 회사 중 무엇인지와 "
    "기준연도를 대본 안에서 오해 없게 유지한다. 업계 전체 통계를 특정 회사의 실적이나 "
    "성공 원인처럼 연결하지 마라. 쇼츠 길이에 이 정보를 함께 담기 어렵다면 숫자 자체를 "
    "빼고 검증된 방향성만 말한다. 출처에 없는 숫자를 계산·반올림·보간하지 마라.\n"
    "가장 중요(쇼츠 긴장): 흥미는 연결어 체크리스트가 아니라 정보 공개 타이밍·대치·이해관계로 만든다. "
    "'사실/하지만/그건 바로'는 경향일 뿐 필수 슬롯이 아니다. 특히 첫 본문 문장에 '사실'을 습관적으로 "
    "붙이지 마라. drama_beats 가 있으면 그 순서로 긴장을 쌓아라. "
    "훅에서 결말을 스포했으면 같은 내용을 후반에 가짜 반연출하지 말고, "
    "확인된 왜/어떻게(요구·거절·대가)로 계속 보게 만들어라.\n"
    "가장 중요(과장 경계): 과장은 제목과 감정 표현에만 허용한다. 실제 수치·사건·인과·발언을 "
    "세게 보이려고 바꾸는 것은 과장이 아니라 거짓이므로 금지한다. 자극적인 표현 뒤에는 반드시 "
    "그 표현을 납득시키는 검증 사실이 이어져야 한다.\n"
    "자막 폰트 호환을 위해 title, hook, segments.text에는 한자를 절대 쓰지 말고 "
    "모든 한자 약칭을 한글로 풀어 써라. 예: 日 수출은 '일본 수출', 美 시장은 '미국 시장'으로 쓴다.\n"
    "반드시 JSON 하나만 출력한다."
)

_SCHEMA = (
    '{"title": string(훅과 동일·짧은 선언형 한 줄), '
    '"hook": string(title과 동일·[훅 예시] 패턴), '
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
    "- 한 기사에 여러 회사 이야기가 있으면 주체를 섞지 마라.\n"
    "- 수치: [출처·범위가 정리된 수치]에 완전한 항목만 사용. 주체·범위·기준연도를 생략해 "
    "다른 통계로 오해하게 만들지 말고, 업계 수치를 회사 실적으로 연결하지 말 것.\n"
    "- 훅 다음 문장을 '사실,' 로 시작하지 말 것.\n"
    "- 연결어 필수 슬롯 아님. 훅은 세게, 본문은 확인된 대치로 도파민."
)


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
            "쉼표 연쇄, 연결어 반복은 모방하지 않는다."
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
        "는데",
        "지만",
    )
    values = []
    for item in endings:
        if not isinstance(item, (list, tuple)) or not item:
            continue
        value = str(item[0]).strip()
        if any(value.endswith(ending) for ending in grammatical):
            values.append(value)
    return values[:8]


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
        f"{hlen.get('avg', 12)}자 내외(최소 {hlen.get('min', 6)}~최대 {hlen.get('max', 15)})"
        if hlen else "12자 내외"
    )
    target_spec = (
        f"대사 총 {ccn.get('min', 0)}~{ccn.get('max', 0)}자(목표 {ccn.get('avg', 0)}자), "
        f"문장 {snt.get('min', 0)}~{snt.get('max', 0)}개, 말속도 {cps.get('avg', 0)}자/초, "
        f"훅 {hook_len_spec}"
    ) if dlg else f"약 {avg_dur}초 분량"

    samples = profile.get("dialogue_samples", [])
    endings = _safe_endings(profile.get("ending_patterns", []))
    voice = recipe.get("voice", "")
    endings_txt = ", ".join(f"'{e}'" for e in endings) if endings else ""
    samples_txt = "\n".join(
        f"  - {str(sample)[:140]}"
        for sample in samples[:4]
    ) or "(없음)"
    hooks_txt = "\n".join(f"  - {h}" for h in hook_examples[:8]) or "(없음)"
    recipe_hook = recipe.get("hook", "")
    style_do = "\n".join(
        f"  - {item}" for item in recipe.get("do", [])[:6]
    ) or "  - 채널 훅·말투·속도만 참고"
    style_avoid = "\n".join(
        f"  - {item}" for item in recipe.get("avoid", [])[:8]
    ) or "  - 반복과 정보 과적재"

    return (
        f"[최우선 품질 기준]\n"
        f"- 채널 스타일보다 이야기의 인과·정보 공개 순서·가독성을 우선한다.\n"
        f"- 보통 6~8개, 검증 사실이 많은 소재는 최대 9개 비트 안에서 "
        f"훅→정체→상황→선택/대치→대가→반전/보상을 만든다.\n"
        f"- 각 비트는 새 정보 하나만 전달하고, 같은 사실 반복과 마지막 정보 덤프를 금지한다.\n"
        f"- 연결어가 아니라 앞 문장의 의문과 다음 문장의 답으로 이어 붙인다.\n"
        f"- 과장은 표현에만 허용하고 사실·수치·인과는 바꾸지 않는다.\n\n"
        f"[훅 예시 — 단어 복사가 아닌 길이·압축감·온도만 참고]\n{hooks_txt}\n\n"
        f"[훅 작성 지침]\n{recipe_hook}\n"
        f"title 과 hook 은 동일 문구. 목표 훅 길이: {hook_len_spec}. "
        f"설명형·질문형 제목 금지. 훅 임팩트는 약화하지 말 것.\n\n"
        f"[채널 스타일 요약]\n"
        f"{_safe_style_summary(str(profile.get('style_summary') or ''))}\n\n"
        f"[말투 지침]\n{voice}\n\n"
        f"[실제 대사 예시 — 말투 참고용. 음성 인식 오류·잘린 문장·구조는 모방 금지]\n"
        f"{samples_txt}\n\n"
        f"[자주 쓰는 문장 끝맺음(어미)]\n{endings_txt}\n\n"
        f"[채널 스타일에서 유지할 요소]\n{style_do}\n\n"
        f"[채널 스타일에서도 모방하지 않을 요소]\n{style_avoid}\n\n"
        f"[톤]\n{recipe.get('tone', '')}\n\n"
        f"{_FLOW_FACT_REMINDER}\n\n"
        f"[목표 길이] 약 {avg_dur}초\n"
        f"[목표 대사 분량] {target_spec}\n"
        f"[목표 훅 길이] {hook_len_spec}"
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
    """title/hook 중 더 훅다운 한 줄을 고른다. 비어 있으면 남은 쪽을 쓴다."""
    title = (title or "").strip()
    hook = (hook or "").strip()
    if not title:
        return hook
    if not hook:
        return title
    weak_markers = ("이유", "진실", "정체", "알아", "뜻밖의", "놀라운")
    hook_weak = any(m in hook for m in weak_markers) or hook.endswith("?")
    title_weak = any(m in title for m in weak_markers) or title.endswith("?")
    if hook_weak and not title_weak:
        return title
    if title_weak and not hook_weak:
        return hook
    if abs(_chars_no_space(title) - _chars_no_space(hook)) >= 6:
        return title if _chars_no_space(title) < _chars_no_space(hook) else hook
    return hook


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


def analyze_story_flow(script: Dict) -> List[str]:
    """Detect structural symptoms worth an additional narrative edit."""

    segments = [
        segment
        for segment in script.get("segments") or []
        if isinstance(segment, dict) and str(segment.get("text") or "").strip()
    ]
    issues: List[str] = []
    if len(segments) < 5:
        issues.append("비트가 너무 적어 훅·전개·결말이 압축됨")
    elif len(segments) > 9:
        issues.append("비트가 너무 많아 전개가 분산됨")

    connector_starts = []
    token_sets = []
    for index, segment in enumerate(segments, 1):
        text = str(segment.get("text") or "").strip()
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

    if len(connector_starts) >= 3:
        issues.append("연결어로 시작하는 비트가 반복됨")
    for index in range(1, len(token_sets)):
        left = token_sets[index - 1]
        right = token_sets[index]
        shared = left & right
        union = left | right
        if len(shared) >= 3 and union and len(shared) / len(union) >= 0.42:
            issues.append(
                f"비트 {index}와 {index + 1}이 같은 정보를 반복할 가능성"
            )
    return list(dict.fromkeys(issues))


def polish_story_flow(
    llm: LLMClient,
    profile: Dict,
    script: Dict,
    *,
    instruction: str = "",
    original_script: Optional[Dict] = None,
) -> Dict:
    """Use the generation model only when deterministic flow checks fail."""

    issues = analyze_story_flow(script)
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
        "각 비트가 앞 비트의 의문을 받고 새 정보를 하나씩 공개하게 만들고, 결말은 가장 "
        "강한 기존 사실 하나로 훅을 회수한다. 보통 6~8개, 검증 사실이 많은 경우에도 "
        "최대 9개 비트를 넘기지 않는다. JSON만 출력한다."
    )
    user = (
        f"[흐름 문제]\n- " + "\n- ".join(issues) + "\n\n"
        f"[채널 톤]\n{recipe.get('tone', '')}\n"
        f"[말투]\n{recipe.get('voice', '')}\n\n"
        f"[사용자 수정 요청]\n{instruction or '(신규 대본)'}\n\n"
        f"[대본 JSON]\n{json.dumps(script, ensure_ascii=False)[:6000]}\n\n"
        f"스키마: {_SCHEMA}"
    )
    try:
        data = llm.complete_json(
            system,
            user,
            temperature=0.45,
            max_tokens=4500,
        )
    except LLMError as exc:
        log.info("대본 흐름 보정 LLM 실패: %s", exc)
        result = copy.deepcopy(script)
        result["_flow_issues"] = issues
        return result
    if not isinstance(data, dict) or not data.get("segments"):
        result = copy.deepcopy(script)
        result["_flow_issues"] = issues
        return result

    candidate = _finalize_llm_script(data)
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
    remaining = analyze_story_flow(candidate)
    if remaining:
        candidate["_flow_issues"] = remaining
    return candidate


def extract_subject_anchors(
    script: Dict,
    source_text: str = "",
) -> List[str]:
    """Extract conservative product/company anchors that revisions must retain."""

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
            key=lambda item: (-item[1], -len(item[0]), item[0]),
        )[:5]
    ]


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
    closing_only = any(
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
            replacement = copy.deepcopy(candidate_segments[-1])
            replacement["index"] = original_segments[-1].get(
                "index",
                len(original_segments),
            )
            merged["segments"] = [
                *copy.deepcopy(original_segments[:-1]),
                replacement,
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
                    research: Optional[Dict] = None) -> Dict:
    """채널 스타일을 반영한 대본을 생성/보정합니다."""
    if not llm.available:
        return _heuristic_script(profile, text, is_script)

    research_txt = _research_block(research)
    task = (
        "아래 대본을 이 채널 말투(어미 포함)로 다듬어라. "
        "핵심 사실은 유지·교정하고, title/hook 은 [훅 예시] 패턴으로 강화하되, "
        "본문 전개 템플릿을 억지로 끼워 맞추지 마라. "
        "웹 조사 메모의 hard_facts 밖 사건·조건·인용은 추가하지 마라. "
        "소프트 과장은 분위기만.\n"
        f"{research_txt}"
        f"[사용자 대본]\n{text}"
        if is_script else
        "아래 주제/메모와 웹 조사 메모로 새 쇼츠 대본을 써라. "
        "채널 말투·강한 훅은 유지하고, drama_beats/hard_facts 의 확인된 갈등은 "
        "한 방으로 살려라(요구→거절→포기 등). "
        "'수출이 어려워져서 포기'처럼 수동 요약하지 마라. "
        "훅 다음을 '사실,' 로 시작하지 마라. "
        "없는 인정 요구·주체 뒤집기만 금지한다.\n"
        f"{research_txt}"
        f"[사용자 주제/메모]\n{text}"
    )
    user = f"{_style_context(profile)}\n\n{task}\n\n스키마: {_SCHEMA}"
    try:
        data = llm.complete_json(_SYSTEM_PROMPT, user, temperature=0.85, max_tokens=7000)
        if isinstance(data, dict) and data.get("segments"):
            candidate = _finalize_llm_script(data)
            if is_script:
                return candidate
            return polish_story_flow(llm, profile, candidate)
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
            )
        log.info("대본 수정 LLM 응답에 segments 가 없음")
    except LLMError as exc:
        log.info("대본 수정 LLM 실패: %s", exc)
    fallback = dict(original_script)
    fallback["_source"] = "revise_failed"
    return fallback
