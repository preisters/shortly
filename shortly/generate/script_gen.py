"""주제→스타일 반영 대본 생성 / 대본 입력 시 스타일 보정."""

from __future__ import annotations

import copy
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
    "대본을 만든다. 목표는 도파민·호기심을 터뜨려 시청자가 끝까지 보게 만드는 것이다. "
    "각 세그먼트는 한 호흡에 읽을 짧은 문장으로 나눈다.\n"
    "가장 중요(말투): [실제 대사 예시]와 [자주 쓰는 문장 끝맺음]의 어미·화법을 그대로 따라 써라. "
    "이 채널은 '썰 풀어주는' 반말 나레이션체다. '~다고 함', '~했는데', '~다는데', '~라고' 같은 "
    "실제 어미를 사용하고, '~입니다', '~습니다', '~해요', '~하죠', '~됩니다' 같은 정중한 리뷰/설명체는 "
    "절대 쓰지 마라. 지정된 [목표 대사 분량]의 글자수·문장 수 범위도 최대한 맞춘다.\n"
    "가장 중요(제목/훅): title 과 hook 은 같은 한 줄로 써라. [훅 예시]의 패턴을 그대로 복제한다. "
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
    "가장 중요(쇼츠 긴장): 흥미는 연결어 체크리스트가 아니라 정보 공개 타이밍·대치·이해관계로 만든다. "
    "'사실/하지만/그건 바로'는 경향일 뿐 필수 슬롯이 아니다. 특히 첫 본문 문장에 '사실'을 습관적으로 "
    "붙이지 마라. drama_beats 가 있으면 그 순서로 긴장을 쌓아라. "
    "훅에서 결말을 스포했으면 같은 내용을 후반에 가짜 반연출하지 말고, "
    "확인된 왜/어떻게(요구·거절·대가)로 계속 보게 만들어라.\n"
    "반드시 JSON 하나만 출력한다."
)

_SCHEMA = (
    '{"title": string(훅과 동일·짧은 선언형 한 줄), '
    '"hook": string(title과 동일·[훅 예시] 패턴), '
    '"segments": [{"index": number, "text": string, "intent": string}]}'
)

_FLOW_FACT_REMINDER = (
    "[전개·재료 재확인]\n"
    "- 확인된 갈등/대치(drama_beats·hard_facts)는 적극 연출. 밋밋한 요약으로 죽이지 말 것.\n"
    "- 없는 사건·인정 요구·주체 뒤집기만 금지. 스니펫에 있는 요구→거절은 사용.\n"
    "- 시점: '올해/작년/최근'은 절대 연도가 그 주체·사건에 붙을 때만. "
    "보도·재조명 시점을 사건 발생으로 쓰지 말고, 연도 불명이면 시점 표현을 빼라.\n"
    "- 한 기사에 여러 회사 이야기가 있으면 주체를 섞지 마라.\n"
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
            if any(k in s for k in ("사실", "하지만", "그건 바로", "연결어")):
                softened.append(
                    "연결어(사실/하지만/그건 바로 등)는 필요할 때만 자연스럽게 "
                    "(매 대본 필수 슬롯 아님)"
                )
            else:
                softened.append(s)
        seen = set()
        uniq = []
        for s in softened:
            if s not in seen:
                seen.add(s)
                uniq.append(s)
        out["do"] = uniq
    voice = str(out.get("voice") or "")
    if voice and "연결어" in voice:
        out["voice"] = (
            voice
            + " 단, 연결어는 습관적 경향일 뿐 모든 문장/구간에 넣을 필요는 없다."
        )
    return out


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
    endings = profile.get("ending_patterns", [])
    voice = recipe.get("voice", "")
    endings_txt = ", ".join(f"'{e}'" for e, _ in endings[:8]) if endings else ""
    samples_txt = "\n".join(f"  - {s}" for s in samples[:6]) or "(없음)"
    hooks_txt = "\n".join(f"  - {h}" for h in hook_examples[:8]) or "(없음)"
    recipe_hook = recipe.get("hook", "")

    return (
        f"[훅 예시 — title/hook 은 이 패턴을 그대로 복제]\n{hooks_txt}\n\n"
        f"[훅 작성 지침]\n{recipe_hook}\n"
        f"title 과 hook 은 동일 문구. 목표 훅 길이: {hook_len_spec}. "
        f"설명형·질문형 제목 금지. 훅 임팩트는 약화하지 말 것.\n\n"
        f"[채널 스타일 요약]\n{profile.get('style_summary', '')}\n\n"
        f"[말투 지침]\n{voice}\n\n"
        f"[실제 대사 예시 — 말투·어미 모방용. 전개 템플릿을 기계적으로 복제하지 말 것]\n"
        f"{samples_txt}\n\n"
        f"[자주 쓰는 문장 끝맺음(어미)]\n{endings_txt}\n\n"
        f"[레시피 — 편집/말투 참고. do 의 연결어는 필수 아님]\n{recipe}\n\n"
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
            return _finalize_llm_script(data)
    except LLMError as exc:
        log.info("대본 생성 LLM 실패, 휴리스틱 사용: %s", exc)
    return _heuristic_script(profile, text, is_script)


def revise_script(llm: LLMClient, profile: Dict, original_script: Dict,
                  instruction: str, research: Optional[Dict] = None) -> Dict:
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
    task = (
        "아래 '기존 대본'을 사용자의 '수정 요청' 방향대로 다시 써라. "
        "채널 말투·어미·분량 규칙은 그대로 유지하고, 요청에 없는 부분은 최대한 원본의 "
        "내용과 흐름을 보존한다. "
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
            return _finalize_llm_script(data)
        log.info("대본 수정 LLM 응답에 segments 가 없음")
    except LLMError as exc:
        log.info("대본 수정 LLM 실패: %s", exc)
    fallback = dict(original_script)
    fallback["_source"] = "revise_failed"
    return fallback
