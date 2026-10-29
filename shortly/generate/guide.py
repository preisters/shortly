"""구간별 제작 가이드 빌더: TTS 설정 + 컷/자막/전환 지시 + 효과음 추천 타이밍표."""

from __future__ import annotations

import math
import re
from itertools import combinations
from typing import Dict, List, Optional
from .script_gen import _recipe_for_prompt, _safe_tone

# 상황 키워드 -> 효과음 성격 매핑(프로파일의 top_sfx 와 교차 사용)
_INTENT_SFX = {
    "질문": ["물음표", "띠링"],
    "충격": ["놀라는배경음", "두둥액션", "긴장공포"],
    "반전": ["두둥액션", "되감기", "서스펜스"],
    "실패": ["슬픈음악", "띠용", "삑~"],
    "성공": ["마리오동전", "명중", "느낌표"],
    "웃음": ["꽥", "뿅", "어엉"],
    "타격": ["펀치", "뚝배기", "쨍그랑"],
    "도망": ["도망", "유성", "다급한브금"],
    "등장": ["까꿍", "안녕하세요"],
}


def _estimate_duration(text: str, chars_per_sec: float = 6.5) -> float:
    """프로필에서 관측한 한국어 말속도를 이용해 예상 길이를 계산한다."""
    chars = len(re.sub(r"\s", "", text))
    rate = chars_per_sec if 4.0 <= chars_per_sec <= 14.0 else 6.5
    return round(max(1.0, chars / rate), 1)


def _profile_speaking_rate(profile: Dict) -> float:
    """채널 자막에서 학습한 초당 글자 수를 반환한다."""
    stats = profile.get("stats") or {}
    dialogue = stats.get("dialogue_stats") or {}
    value = dialogue.get("chars_per_sec")
    if isinstance(value, dict):
        value = value.get("avg")
    try:
        rate = float(value)
    except (TypeError, ValueError):
        return 6.5
    return rate if 4.0 <= rate <= 14.0 else 6.5


_QUOTE_PAIRS = {
    '"': '"',
    "'": "'",
    "“": "”",
    "‘": "’",
    "「": "」",
    "『": "』",
}
_CONNECTIVE_RE = re.compile(
    r"(?<![0-9A-Za-z가-힣])"
    r"(하지만|그렇지만|그런데|그러나|그래서|그러자|그러면서|반면|"
    r"결국|대신|때문에|이후|여기에|그중|게다가|즉|사실|근데|그리고)"
)
_MAX_VISUAL_SEC = 5.0


def _learned_cut_target(profile: Dict, total_duration: float) -> int:
    """채널 통계가 제안하는 전체 컷 수를 계산한다(강제 예산이 아님)."""
    stats = profile.get("stats") or {}
    try:
        cuts_per_min = float(stats.get("avg_cuts_per_min") or 0.0)
    except (TypeError, ValueError):
        cuts_per_min = 0.0
    if 6.0 <= cuts_per_min <= 60.0:
        target = round(total_duration * cuts_per_min / 60.0)
    else:
        try:
            avg_shot = float(stats.get("avg_shot_sec") or 2.0)
        except (TypeError, ValueError):
            avg_shot = 2.0
        avg_shot = avg_shot if 0.8 <= avg_shot <= 10.0 else 2.0
        target = round(total_duration / avg_shot)
    return max(1, int(target))


def _profile_min_visual_sec(profile: Dict) -> float:
    """학습 평균보다 지나치게 짧은 화면이 생기지 않도록 하한을 정한다."""
    stats = profile.get("stats") or {}
    try:
        avg_shot = float(stats.get("avg_shot_sec") or 2.4)
    except (TypeError, ValueError):
        avg_shot = 2.4
    if not 0.8 <= avg_shot <= 10.0:
        avg_shot = 2.4
    return round(max(1.8, min(3.0, avg_shot * 0.85)), 2)


def _sentence_spans(text: str) -> List[tuple[int, int]]:
    """따옴표 안 종결부호를 보존하며 실제 문장 위치를 반환한다."""
    source = text or ""
    if not source.strip():
        return [(0, len(source))]
    spans: List[tuple[int, int]] = []
    start = 0
    quote_end: Optional[str] = None
    for index, char in enumerate(source):
        if quote_end is not None:
            if char == quote_end:
                quote_end = None
            continue
        if char in _QUOTE_PAIRS:
            quote_end = _QUOTE_PAIRS[char]
            continue
        if char == "." and index > 0 and index + 1 < len(source) \
                and source[index - 1].isdigit() and source[index + 1].isdigit():
            # 3.99 같은 소수점은 문장 종결점으로 세지 않는다.
            continue
        if char not in ".!?…。！？":
            continue
        left = start
        while left < index + 1 and source[left].isspace():
            left += 1
        if left < index + 1:
            spans.append((left, index + 1))
        start = index + 1
    left = start
    while left < len(source) and source[left].isspace():
        left += 1
    if left < len(source):
        spans.append((left, len(source)))
    return spans or [(0, len(source))]


def _spoken_chars(text: str) -> int:
    return len(re.sub(r"\s", "", text or ""))


def _clause_candidates(text: str, start: int, end: int) -> List[Dict]:
    """한 문장 안에서만 허용되는 쉼표·접속어 전환 후보를 찾는다."""
    candidates: List[Dict] = []
    quote_end: Optional[str] = None
    for index in range(start, end):
        char = text[index]
        if quote_end is not None:
            if char == quote_end:
                quote_end = None
            continue
        if char in _QUOTE_PAIRS:
            quote_end = _QUOTE_PAIRS[char]
            continue
        if char in ",，、;；:":
            candidates.append(
                {"position": index + 1, "reason": f"'{char}' 뒤 의미 절 경계", "score": 2}
            )

    sentence = text[start:end]
    for match in _CONNECTIVE_RE.finditer(sentence):
        position = start + match.start(1)
        if _spoken_chars(text[start:position]) < 4:
            continue
        candidates.append(
            {
                "position": position,
                "reason": f"접속어 '{match.group(1)}' 앞",
                "score": 3,
            }
        )

    # 쉼표 직후 접속어처럼 사실상 같은 경계인 후보는 하나로 합친다.
    merged: List[Dict] = []
    for candidate in sorted(candidates, key=lambda item: item["position"]):
        if merged and candidate["position"] - merged[-1]["position"] <= 2:
            if candidate["score"] >= merged[-1]["score"]:
                merged[-1] = candidate
            continue
        merged.append(candidate)
    return [
        item
        for item in merged
        if _spoken_chars(text[start:item["position"]]) >= 4
        and _spoken_chars(text[item["position"]:end]) >= 4
    ]


def _best_clause_boundaries(
    text: str,
    start: int,
    end: int,
    sentence_duration: float,
    candidates: List[Dict],
    count: int,
    min_visual_sec: float,
    max_visual_sec: Optional[float] = None,
) -> List[Dict]:
    """짧은 플래시 컷 없이 가장 균형 잡힌 의미 경계를 선택한다."""
    if count <= 0:
        return []
    total_chars = max(_spoken_chars(text[start:end]), 1)
    best: List[Dict] = []
    best_score: Optional[float] = None
    targets = [index / (count + 1) for index in range(1, count + 1)]
    for combo in combinations(candidates, count):
        ordered = sorted(combo, key=lambda item: item["position"])
        edges = [start, *[item["position"] for item in ordered], end]
        chunk_chars = [
            _spoken_chars(text[edges[index]:edges[index + 1]])
            for index in range(len(edges) - 1)
        ]
        chunk_durations = [
            sentence_duration * chars / total_chars for chars in chunk_chars
        ]
        if min(chunk_durations or [0.0]) < min_visual_sec:
            continue
        if max_visual_sec is not None and max(chunk_durations or [0.0]) > max_visual_sec:
            continue
        ratios = [
            _spoken_chars(text[start:item["position"]]) / total_chars
            for item in ordered
        ]
        imbalance = sum(abs(value - target) for value, target in zip(ratios, targets))
        score = sum(int(item["score"]) for item in ordered) - imbalance * 4.0
        if best_score is None or score > best_score:
            best = ordered
            best_score = score
    return best


def _time_balance_candidates(
    text: str,
    start: int,
    end: int,
    semantic_candidates: List[Dict],
) -> List[Dict]:
    """의미 경계가 부족할 때 5초 상한을 지킬 수 있는 단어 경계를 보충한다."""

    by_position = {item["position"]: item for item in semantic_candidates}
    sentence = text[start:end]
    for match in re.finditer(r"\s+", sentence):
        position = start + match.end()
        if position <= start or position >= end or position in by_position:
            continue
        if _spoken_chars(text[start:position]) < 4 \
                or _spoken_chars(text[position:end]) < 4:
            continue
        by_position[position] = {
            "position": position,
            "reason": "5초 상한을 위한 자연스러운 어절 경계",
            "score": 0,
        }
    return sorted(by_position.values(), key=lambda item: item["position"])


def _plan_sentence_aware_cuts(
    profile: Dict,
    source_segments: List[Dict],
    durations: List[float],
) -> Dict:
    """문장당 1컷을 우선하고 의미 경계가 있을 때만 최대 3컷을 허용한다."""
    min_visual_sec = _profile_min_visual_sec(profile)
    segment_plans: List[Dict] = []
    sentence_options: List[Dict] = []

    for segment_position, segment in enumerate(source_segments):
        text = str(segment.get("text") or "")
        duration = durations[segment_position]
        spans = _sentence_spans(text)
        sentence_weights = [max(_spoken_chars(text[start:end]), 1) for start, end in spans]
        total_weight = sum(sentence_weights) or 1
        options: List[Dict] = []
        for sentence_position, ((start, end), weight) in enumerate(zip(spans, sentence_weights)):
            sentence_duration = duration * weight / total_weight
            candidates = _clause_candidates(text, start, end)
            best_one = _best_clause_boundaries(
                text, start, end, sentence_duration, candidates, 1, min_visual_sec
            )
            best_two = _best_clause_boundaries(
                text, start, end, sentence_duration, candidates, 2, min_visual_sec
            )
            semantic_capacity = 2 if best_two else (1 if best_one else 0)
            required_cut_count = min(
                3,
                max(1, int(math.ceil(sentence_duration / _MAX_VISUAL_SEC))),
            )
            required_extra = required_cut_count - 1
            balanced_candidates = _time_balance_candidates(
                text, start, end, candidates
            )
            best: Dict[int, List[Dict]] = {1: best_one, 2: best_two}
            capacity = max(semantic_capacity, required_extra)
            for boundary_count in range(1, capacity + 1):
                compliant = _best_clause_boundaries(
                    text,
                    start,
                    end,
                    sentence_duration,
                    candidates,
                    boundary_count,
                    min_visual_sec,
                    _MAX_VISUAL_SEC,
                )
                if not compliant:
                    compliant = _best_clause_boundaries(
                        text,
                        start,
                        end,
                        sentence_duration,
                        balanced_candidates,
                        boundary_count,
                        min_visual_sec,
                        _MAX_VISUAL_SEC,
                    )
                if not compliant and boundary_count <= required_extra:
                    # 15초를 넘는 한 문장처럼 3컷 제한 안에서 상한 준수가
                    # 불가능한 특수 사례는 가장 균형 잡힌 어절 경계를 사용한다.
                    compliant = _best_clause_boundaries(
                        text,
                        start,
                        end,
                        sentence_duration,
                        balanced_candidates,
                        boundary_count,
                        min_visual_sec,
                    )
                if compliant:
                    best[boundary_count] = compliant
            option = {
                "segment_position": segment_position,
                "sentence_position": sentence_position,
                "start": start,
                "end": end,
                "duration": sentence_duration,
                "capacity": capacity,
                "selected_extra": required_extra,
                "required_extra": required_extra,
                "best": best,
            }
            options.append(option)
            sentence_options.append(option)
        segment_plans.append({"text": text, "spans": spans, "options": options})

    base_cut_count = len(sentence_options)
    time_floor_cut_count = sum(
        1 + int(option["required_extra"]) for option in sentence_options
    )
    max_cut_count = base_cut_count + sum(option["capacity"] for option in sentence_options)
    learned_target = _learned_cut_target(profile, sum(durations))
    target = max(time_floor_cut_count, min(learned_target, max_cut_count))
    extras_needed = target - time_floor_cut_count

    # 모든 문장에 두 번째 컷을 배분한 뒤에만 세 번째 컷을 검토한다.
    for level in (1, 2):
        eligible = [option for option in sentence_options if option["capacity"] >= level]
        eligible.sort(key=lambda option: option["duration"], reverse=True)
        for option in eligible:
            if option["selected_extra"] >= level:
                continue
            if extras_needed <= 0:
                break
            option["selected_extra"] = level
            extras_needed -= 1

    counts: List[int] = []
    change_ratios: List[List[float]] = []
    change_reasons: List[List[str]] = []
    for plan in segment_plans:
        text = plan["text"]
        total_chars = max(_spoken_chars(text), 1)
        boundaries: List[Dict] = []
        for option in plan["options"]:
            selected_extra = int(option["selected_extra"])
            boundaries.extend(option["best"].get(selected_extra) or [])
        for _start, end in plan["spans"][:-1]:
            boundaries.append(
                {"position": end, "reason": "문장 종결 뒤 전환", "score": 4}
            )
        ordered = sorted(boundaries, key=lambda item: item["position"])
        ratios = [
            round(_spoken_chars(text[:item["position"]]) / total_chars, 4)
            for item in ordered
        ]
        ratios = [value for value in ratios if 0.0 < value < 1.0]
        counts.append(len(ratios) + 1)
        change_ratios.append(ratios)
        change_reasons.append([item["reason"] for item in ordered][:len(ratios)])

    return {
        "counts": counts,
        "change_ratios": change_ratios,
        "change_reasons": change_reasons,
        "sentence_counts": [len(plan["spans"]) for plan in segment_plans],
        "learned_target": learned_target,
        "base_cut_count": base_cut_count,
        "time_floor_cut_count": time_floor_cut_count,
        "max_cut_count": max_cut_count,
        "min_visual_sec": min_visual_sec,
        "max_visual_sec": _MAX_VISUAL_SEC,
    }


def _tts_settings(intent: str, tone: str) -> Dict:
    speed = 1.1
    emotion = "normal"
    it = (intent or "") + (tone or "")
    if any(k in it for k in ("충격", "반전", "긴장")):
        emotion, speed = "excited", 1.15
    elif any(k in it for k in ("슬픔", "실패")):
        emotion, speed = "sad", 1.0
    elif any(k in it for k in ("웃음", "코믹", "유머")):
        emotion, speed = "happy", 1.15
    return {"voice_hint": "채널 대표 보이스와 유사한 톤", "speed": speed,
            "emotion": emotion, "pitch": 0}


def _recommend_sfx(intent: str, text: str, top_sfx_names: List[str]) -> List[str]:
    recs: List[str] = []
    key_src = (intent or "") + " " + text
    for key, names in _INTENT_SFX.items():
        if key in key_src:
            recs.extend(names)
    # 채널이 실제 자주 쓰는 효과음 우선 배치
    prioritized = [n for n in top_sfx_names if n in recs]
    others = [n for n in recs if n not in prioritized]
    merged = prioritized + others
    # 중복 제거, 최대 3개
    seen = []
    for n in merged:
        if n not in seen:
            seen.append(n)
    return seen[:3] or (top_sfx_names[:1] if top_sfx_names else [])


def build_guide(profile: Dict, script: Dict) -> Dict:
    """대본 + 스타일 프로파일 -> 구간별 제작 가이드."""
    stats = profile.get("stats", {})
    recipe = _recipe_for_prompt(profile.get("recipe", {}))
    tone = _safe_tone(str(recipe.get("tone", "")))
    top_sfx_names = [s["name"] for s in profile.get("top_sfx", [])]
    speaking_rate = _profile_speaking_rate(profile)

    source_segments = list(script.get("segments", []))
    durations = [
        _estimate_duration(seg.get("text", ""), speaking_rate)
        for seg in source_segments
    ]
    total_duration = round(sum(durations), 1)
    cut_plan = _plan_sentence_aware_cuts(profile, source_segments, durations)
    cut_counts = cut_plan["counts"]

    segments: List[Dict] = []
    cursor = 0.0
    for position, seg in enumerate(source_segments):
        text = seg.get("text", "")
        intent = seg.get("intent", "")
        dur = durations[position]
        cuts_in_seg = cut_counts[position]
        seg_guide = {
            "index": seg.get("index"),
            "start_sec": round(cursor, 1),
            "end_sec": round(cursor + dur, 1),
            "duration_sec": dur,
            "narration": text,
            "intent": intent,
            "caption": text if len(text) <= 22 else text[:20] + "…",
            "cuts_suggested": cuts_in_seg,
            "sentence_count": cut_plan["sentence_counts"][position],
            "cut_change_ratios": cut_plan["change_ratios"][position],
            "cut_change_reasons": cut_plan["change_reasons"][position],
            "camera": profile.get("stats", {}).get("zoom_distribution") and "줌인 강조" or "고정",
            "transition_out": "하드컷",
            "tts": _tts_settings(intent, tone),
            "sfx": _recommend_sfx(intent, text, top_sfx_names),
        }
        segments.append(seg_guide)
        cursor += dur

    return {
        "title": script.get("title", ""),
        "hook": script.get("hook", ""),
        "total_duration_sec": round(cursor, 1),
        "pacing_calculation": {
            "speaking_rate_chars_per_sec": speaking_rate,
            "target_cut_count": sum(cut_counts),
            "learned_cut_target": cut_plan["learned_target"],
            "sentence_base_cut_count": cut_plan["base_cut_count"],
            "time_floor_cut_count": cut_plan["time_floor_cut_count"],
            "sentence_structure_max_cut_count": cut_plan["max_cut_count"],
            "minimum_visual_duration_sec": cut_plan["min_visual_sec"],
            "maximum_visual_duration_sec": cut_plan["max_visual_sec"],
            "estimated_avg_cut_sec": round(cursor / sum(cut_counts), 2)
            if cut_counts and sum(cut_counts)
            else 0.0,
            "basis": "sentence_structure_then_profile_pacing",
            "policy": (
                "문장당 기본 1컷. 일반적인 컷은 5초를 넘기지 않으며, 쉼표·접속어 "
                "등 의미 경계를 우선하고 부족하면 자연스러운 어절에서 나눈다. "
                "한 문장은 최대 3컷."
            ),
        },
        "target_pacing": (
            "문장당 기본 1컷을 유지하되 일반적인 컷은 5초를 넘기지 않는다. "
            "의미 있는 쉼표·접속어를 우선하고 필요하면 자연스러운 어절에서 나누며, "
            "복수 경계가 있는 긴 문장은 최대 3컷을 사용한다."
        ),
        "channel": profile.get("channel", {}),
        "segments": segments,
        "global_notes": {
            "captions": "의미 단위로 읽기 쉽게 최대 두 줄로 구성한다. 실제 템플릿의 자막 영역과 이미지 여백을 지키며 화면 점유율을 강제하지 않는다.",
            "transitions": "문장과 장면의 의미 경계에서 단순 컷을 우선한다. 학습 영상의 전환 횟수는 목표 횟수가 아니다.",
            "tone": tone,
            "do": [item for item in recipe.get("do", [])
                   if not re.search(r"\d+(?:\.\d+)?\s*%|텍스트 오버레이|초당.*샷", str(item))],
            "avoid": recipe.get("avoid", []),
        },
    }


def render_guide_markdown(guide: Dict) -> str:
    lines: List[str] = []
    lines.append(f"# 쇼츠 제작 가이드: {guide.get('title', '')}")
    lines.append("")
    lines.append(f"- 훅: \"{guide.get('hook', '')}\"")
    lines.append(f"- 예상 길이: {guide.get('total_duration_sec')}초")
    lines.append(f"- 목표 페이스: {guide.get('target_pacing')}")
    visual_status = guide.get("visual_recommendation") or {}
    if visual_status.get("status") in ("partial", "fallback"):
        failed = visual_status.get("failed_segment_indices") or []
        lines.append(
            "- ⚠️ 시각 추천 상태: 상세 추천 모델 일부 실패 — "
            f"구간 {', '.join(str(item) for item in failed)}은 "
            "현재 나레이션 기반 안전 폴백이며 에셋 생성 전 재검토가 필요합니다."
        )
    lines.append("")
    lines.append("## 구간별 지시")
    for s in guide.get("segments", []):
        lines.append("")
        lines.append(f"### {s['index'] + 1}. {s['start_sec']}s ~ {s['end_sec']}s ({s['duration_sec']}초)")
        lines.append(f"- 나레이션: {s['narration']}")
        lines.append(f"- 자막: {s['caption']}")
        lines.append(f"- 컷 수: {s['cuts_suggested']} / 카메라: {s['camera']} / 전환: {s['transition_out']}")
        if s.get("cut_change_reasons"):
            lines.append(
                f"- 화면 전환 경계: {' / '.join(s['cut_change_reasons'])}"
            )
        tts = s["tts"]
        lines.append(f"- TTS: 속도 {tts['speed']}, 감정 {tts['emotion']} ({tts['voice_hint']})")
        lines.append(f"- 효과음: {', '.join(s['sfx']) or '없음'}")
        if s.get("image_suggestions"):
            lines.append(f"- 이미지 추천: {', '.join(s['image_suggestions'])}")
        if s.get("visual_role_suggestions"):
            lines.append(
                f"- 컷별 시각 역할: {' / '.join(s['visual_role_suggestions'])}"
            )
        if s.get("visible_text_suggestions"):
            lines.append(
                f"- 화면 글자 명세: {' / '.join(s['visible_text_suggestions'])}"
            )
        if s.get("media_suggestions"):
            lines.append(f"- 매체: {', '.join(s['media_suggestions'])}")
        if s.get("asset_type_suggestions"):
            lines.append(
                f"- 에셋 유형: {', '.join(s['asset_type_suggestions'])}"
            )
        if s.get("asset_reason_suggestions"):
            lines.append(
                f"- 에셋 판단: {' / '.join(s['asset_reason_suggestions'])}"
            )
        if s.get("stock_query_suggestions"):
            queries = [
                item
                for item in s["stock_query_suggestions"]
                if str(item).strip()
            ]
            if queries:
                lines.append(f"- 스톡 검색어: {', '.join(queries)}")
        if s.get("ai_disclosure_suggestions") and any(
            s["ai_disclosure_suggestions"]
        ):
            lines.append("- 가상 인물 고지: 해당 생성 컷에 자동 표시")
        if s.get("effect_suggestions"):
            lines.append(f"- 효과 추천: {', '.join(s['effect_suggestions'])}")
    g = guide.get("global_notes", {})
    lines.append("")
    lines.append("## 전체 노트")
    lines.append(f"- 자막: {g.get('captions')}")
    lines.append(f"- 전환: {g.get('transitions')}")
    lines.append(f"- 톤: {g.get('tone')}")
    if g.get("do"):
        lines.append(f"- 권장: {', '.join(g['do'])}")
    if g.get("avoid"):
        lines.append(f"- 지양: {', '.join(g['avoid'])}")
    lines.append("")
    return "\n".join(lines)
