"""여러 쇼츠 분석을 종합해 '채널 스타일 프로파일'과 사람용 리포트를 만듭니다."""

from __future__ import annotations

import re
from collections import Counter
from statistics import mean
from typing import Dict, List, Optional

from ..llm.base import LLMClient, LLMError
from ..utils import log
from .layout import aggregate_layout


def _safe_mean(values: List[float]) -> float:
    values = [v for v in values if isinstance(v, (int, float))]
    return round(mean(values), 2) if values else 0.0


def _range(values: List[float]) -> Dict:
    """평균·최소·최대를 묶어 반환(조건을 범위로 세세하게 설정하기 위함)."""
    values = [v for v in values if isinstance(v, (int, float))]
    if not values:
        return {"avg": 0.0, "min": 0.0, "max": 0.0}
    return {"avg": round(mean(values), 1), "min": round(min(values), 1),
            "max": round(max(values), 1)}


def _aggregate_dialogue(per_short: List[Dict], hook_examples: List[str]) -> Dict:
    """영상별 대사 정량지표를 모아 채널 공통 조건(범위)을 산출."""
    metrics = []
    for s in per_short:
        m = (s.get("transcript", {}) or {}).get("metrics")
        if not m:
            m = (s.get("analysis", {}) or {}).get("dialogue_metrics")
        if m:
            metrics.append(m)
    if not metrics:
        return {}
    hook_lens = [len(re.sub(r"\s", "", h)) for h in hook_examples if h]
    return {
        "char_count_no_space": _range([m.get("char_count_no_space", 0) for m in metrics]),
        "word_count": _range([m.get("word_count", 0) for m in metrics]),
        "sentence_count": _range([m.get("sentence_count", 0) for m in metrics]),
        "avg_sentence_chars": _range([m.get("avg_sentence_chars", 0) for m in metrics]),
        "chars_per_sec": _range([m.get("chars_per_sec", 0) for m in metrics]),
        "words_per_sec": _range([m.get("words_per_sec", 0) for m in metrics]),
        "hook_char_len": _range(hook_lens),
    }


def _split_sentences(text: str) -> List[str]:
    return [s.strip() for s in re.split(r"[.!?。！？…]+|\n+", text or "") if s.strip()]


def _aggregate_voice(per_short: List[Dict]) -> Dict:
    """실제 나레이션 예시와 문장 끝맺음(어미) 패턴을 모아 '말투'를 포착합니다."""
    samples: List[str] = []
    ending_counter: Counter = Counter()
    for s in per_short:
        an = s.get("analysis", {}) or {}
        tr = s.get("transcript", {}) or {}
        # 오인식 보정된 대사 우선, 없으면 정제 대사 사용
        sample = an.get("corrected_dialogue_sample") or tr.get("full_text", "")
        sample = (sample or "").strip()
        if sample and len(sample) >= 10:
            # 너무 길면 앞 2문장 정도만 예시로
            sents = _split_sentences(sample)
            short = " ".join(sents[:2]) if sents else sample[:120]
            if short not in samples:
                samples.append(short)
        # 어미 집계는 정제 대사 전체에서(문장별 마지막 어절)
        for sent in _split_sentences(tr.get("full_text", "") or sample):
            toks = sent.split()
            if not toks:
                continue
            last = toks[-1]
            # 마지막 어절의 끝 2~4글자를 어미 후보로(숫자/기호 제외)
            tail = re.sub(r"[^가-힣]", "", last)
            if len(tail) >= 2:
                ending_counter[tail[-3:] if len(tail) >= 3 else tail] += 1
    return {
        "samples": samples[:8],
        "ending_patterns": ending_counter.most_common(12),
    }


def _aggregate_sfx(per_short: List[Dict]) -> List[Dict]:
    counter: Counter = Counter()
    context_examples: Dict[str, List[str]] = {}
    on_cut_counter: Counter = Counter()
    for s in per_short:
        for u in s.get("sfx", {}).get("sfx_usages", []):
            name = u["name"]
            counter[name] += 1
            if u.get("context", {}).get("on_cut"):
                on_cut_counter[name] += 1
            dlg = u.get("context", {}).get("dialogue_near")
            if dlg:
                context_examples.setdefault(name, [])
                if len(context_examples[name]) < 3:
                    context_examples[name].append(dlg[:40])
    result = []
    for name, cnt in counter.most_common(20):
        result.append({
            "name": name,
            "count": cnt,
            "on_cut_ratio": round(on_cut_counter[name] / cnt, 2) if cnt else 0.0,
            "dialogue_examples": context_examples.get(name, []),
        })
    return result


def _motion_level(motions: List[float]) -> str:
    if not motions:
        return "unknown"
    m = mean(motions)
    return "high" if m >= 3.0 else ("medium" if m >= 1.2 else "low")


def _aggregate_editing(per_short: List[Dict]) -> Dict:
    """상위 영상의 CV 샷 상세(및 전역 집계)를 모아 편집 기법 프로파일을 만든다.

    - 영상/움짤 vs 정지 이미지 비율(motion_clip_ratio), 줌 경향, 전환 종류,
      평균 샷 길이, 모션 레벨을 산출하고, 캡컷에 실제로 붙일 수 있는 권장
      효과명(capcut_effects)을 함께 제시한다.
    """
    zoom_counter: Counter = Counter()
    trans_counter: Counter = Counter()
    avg_shots: List[float] = []
    motions: List[float] = []
    still = 0
    motion_clips = 0
    for s in per_short:
        cv = s.get("cv") or {}
        if not cv.get("available"):
            continue
        if isinstance(cv.get("avg_shot_sec"), (int, float)):
            avg_shots.append(cv["avg_shot_sec"])
        if isinstance(cv.get("motion_mean"), (int, float)):
            motions.append(cv["motion_mean"])
        shots = cv.get("shots") or []
        if shots:
            for sh in shots:
                mt = sh.get("media_type")
                if mt == "motion_clip":
                    motion_clips += 1
                elif mt == "still_image":
                    still += 1
                zd = sh.get("zoom_dir")
                if zd:
                    zoom_counter[zd] += 1
                ti = sh.get("transition_in")
                if ti:
                    trans_counter[ti] += 1
        else:
            # 샷 상세가 없는 영상은 전역 집계로 폴백
            zt = cv.get("zoom_tendency")
            if zt:
                zoom_counter[zt] += 1
            for k, v in (cv.get("transition_summary") or {}).items():
                trans_counter[k] += v

    total_shots = still + motion_clips
    motion_clip_ratio = round(motion_clips / total_shots, 2) if total_shots else 0.0
    still_ratio = round(still / total_shots, 2) if total_shots else 0.0
    dominant_zoom = zoom_counter.most_common(1)[0][0] if zoom_counter else "static"

    capcut_effects: List[str] = []
    if dominant_zoom == "zoom_in":
        capcut_effects.append("줌 인")
    elif dominant_zoom == "zoom_out":
        capcut_effects.append("줌 아웃")
    if trans_counter.get("dissolve"):
        capcut_effects.append("페이드 인")
        capcut_effects.append("페이드 아웃")

    if not zoom_counter and not trans_counter and not total_shots:
        return {}

    media_txt = (f"영상·움짤 {int(motion_clip_ratio * 100)}% / 정지 이미지 "
                 f"{int(still_ratio * 100)}%") if total_shots else "매체 비율 데이터 부족"
    summary = (
        f"{media_txt}. 주 줌 경향 '{dominant_zoom}', 전환 {dict(trans_counter)}, "
        f"평균 샷 {round(mean(avg_shots), 2) if avg_shots else 0.0}초."
    )
    return {
        "motion_clip_ratio": motion_clip_ratio,
        "still_image_ratio": still_ratio,
        "dominant_zoom": dominant_zoom,
        "zoom_distribution": dict(zoom_counter),
        "transition_types": dict(trans_counter),
        "avg_shot_sec": round(mean(avg_shots), 2) if avg_shots else 0.0,
        "motion_level": _motion_level(motions),
        "capcut_effects": capcut_effects,
        "shots_analyzed": total_shots,
        "summary": summary,
    }


def _aggregate_image_style(llm: LLMClient, per_short: List[Dict]) -> Dict:
    """상위 영상의 컷별 image_usage 를 모아 채널의 '사용 이미지 경향'을 만든다.

    자막과 무관(relevant_to_caption=false)하면서 note 도 없는 항목은 가중치를 낮춰
    이상 이미지가 경향을 왜곡하지 않게 한다. 단, 유명 일화/사건(note 존재)은 유지.
    """
    records: List[Dict] = []
    for s in per_short:
        for r in (s.get("image_usage") or []):
            if isinstance(r, dict):
                records.append(r)
    if not records:
        return {}

    type_weight: Dict[str, float] = {}
    subjects: List[str] = []
    cautions: List[str] = []
    for r in records:
        rel = bool(r.get("relevant_to_caption", True))
        note = (r.get("note") or "").strip()
        # 무관 + 근거 없음 = 이상 이미지로 보고 가중치↓ (완전 배제는 아님)
        weight = 1.0 if rel else (0.5 if note else 0.2)
        it = (r.get("image_type") or "기타").strip() or "기타"
        type_weight[it] = type_weight.get(it, 0.0) + weight
        content = (r.get("content") or "").strip()
        if content and (rel or note) and content not in subjects:
            subjects.append(content)
        if note and note not in cautions:
            cautions.append(note)

    common_types = sorted(type_weight.items(), key=lambda kv: kv[1], reverse=True)
    common_types = [[t, round(w, 1)] for t, w in common_types]

    summary = _synthesize_image_style(llm, common_types, subjects, cautions)
    caution = (
        "자막/주제와 무관한 이상 이미지는 배제하되, 유명 일화·사건 이미지는 참고로 유지."
    )
    if cautions:
        caution += " 참고 사례: " + "; ".join(cautions[:3])
    return {
        "summary": summary,
        "common_types": common_types[:8],
        "subjects": subjects[:10],
        "caution": caution,
        "sample_count": len(records),
    }


def _synthesize_image_style(llm: LLMClient, common_types: List[List],
                            subjects: List[str], cautions: List[str]) -> str:
    """이미지 경향을 한 문장 요약(LLM 우선, 실패 시 휴리스틱)."""
    if llm.available and subjects:
        system = (
            "너는 유튜브 쇼츠 채널의 '화면에 쓰는 이미지 경향'을 요약하는 분석가다. "
            "주어진 이미지 타입 분포와 실제 이미지 설명 예시로 이 채널이 어떤 이미지를 "
            "즐겨 쓰는지 2~3문장으로 한국어 요약하라. 반드시 JSON 하나만 출력한다."
        )
        user = (
            f"[이미지 타입 분포(가중치)] {common_types}\n"
            f"[실제 이미지 설명 예시]\n" + "\n".join(f"- {s}" for s in subjects[:10]) + "\n"
            f"[참고 사례(유명 일화 등)] {cautions[:5]}\n"
            '스키마: {"summary": string}'
        )
        try:
            data = llm.complete_json(system, user, max_tokens=2000)
            if isinstance(data, dict) and data.get("summary"):
                return str(data["summary"])
        except LLMError as exc:
            log.info("이미지 경향 요약 LLM 실패, 휴리스틱 사용: %s", exc)
    top = ", ".join(f"{t}" for t, _ in common_types[:3]) or "데이터 부족"
    return f"주로 {top} 유형의 이미지를 사용. 예: {'; '.join(subjects[:3])}"


def build_image_editing_styles(llm: LLMClient, per_short: List[Dict]) -> Dict:
    """image_style + editing_style 을 함께 산출(집계·backfill 공용)."""
    return {
        "image_style": _aggregate_image_style(llm, per_short),
        "editing_style": _aggregate_editing(per_short),
    }


def aggregate_profile(llm: LLMClient, manifest: Dict, per_short: List[Dict]) -> Dict:
    """분석 결과 종합 -> 스타일 프로파일."""
    cvs = [s.get("cv", {}) for s in per_short if s.get("cv", {}).get("available")]
    durations = [c.get("duration_sec", 0) for c in cvs]
    cuts_pm = [c.get("cuts_per_min", 0) for c in cvs]
    avg_shots = [c.get("avg_shot_sec", 0) for c in cvs]
    text_ratios = [c.get("text_overlay_ratio", 0) for c in cvs]

    pacing_dist = Counter(c.get("pacing") for c in cvs if c.get("pacing"))
    zoom_dist = Counter(c.get("zoom_tendency") for c in cvs if c.get("zoom_tendency"))
    transition_totals: Counter = Counter()
    for c in cvs:
        for k, v in (c.get("transition_summary") or {}).items():
            transition_totals[k] += v

    analyses = [s.get("analysis", {}) for s in per_short if s.get("analysis")]
    hook_types = Counter(a.get("hook_type") for a in analyses if a.get("hook_type"))
    hook_examples = [a.get("hook_text") for a in analyses if a.get("hook_text")][:8]
    humor_devices: Counter = Counter()
    for a in analyses:
        for d in (a.get("humor_devices") or []):
            humor_devices[d] += 1
    connective: Counter = Counter()
    for a in analyses:
        for d in (a.get("connective_devices") or []):
            connective[d] += 1

    sub_sources = Counter(s.get("transcript", {}).get("source") for s in per_short)

    stats = {
        "analyzed_count": len(per_short),
        "avg_duration_sec": _safe_mean(durations),
        "avg_cuts_per_min": _safe_mean(cuts_pm),
        "avg_shot_sec": _safe_mean(avg_shots),
        "avg_text_overlay_ratio": _safe_mean(text_ratios),
        "pacing_distribution": dict(pacing_dist),
        "zoom_distribution": dict(zoom_dist),
        "transition_totals": dict(transition_totals),
        "hook_type_distribution": dict(hook_types),
        "top_humor_devices": humor_devices.most_common(10),
        "top_connective_devices": connective.most_common(8),
        "subtitle_sources": dict(sub_sources),
        "dialogue_stats": _aggregate_dialogue(per_short, hook_examples),
    }
    top_sfx = _aggregate_sfx(per_short)
    voice = _aggregate_voice(per_short)
    layout = aggregate_layout(per_short)

    style_summary, recipe = _synthesize(llm, manifest, stats, hook_examples,
                                        top_sfx, voice)
    # 상위 영상의 이미지 내용/편집 기법 경향(가이드 컷별 추천의 근거)
    img_edit = build_image_editing_styles(llm, per_short)

    return {
        "channel": {
            "input": manifest.get("input"),
            "slug": manifest.get("slug"),
            "shorts_url": manifest.get("shorts_url"),
        },
        "stats": stats,
        "hook_examples": hook_examples,
        "top_sfx": top_sfx,
        # 실제 나레이션 예시 + 자주 쓰는 문장 끝맺음(말투 재현용 핵심 데이터)
        "dialogue_samples": voice.get("samples", []),
        "ending_patterns": voice.get("ending_patterns", []),
        # 화면 레이아웃(제목/이미지/자막 영역, 캡컷 내보내기 위치 반영용)
        "layout": layout,
        # 사용 이미지 경향 / 편집 기법 경향
        "image_style": img_edit.get("image_style", {}),
        "editing_style": img_edit.get("editing_style", {}),
        "style_summary": style_summary,
        "recipe": recipe,
    }


def _synthesize(llm: LLMClient, manifest: Dict, stats: Dict,
                hook_examples: List[str], top_sfx: List[Dict],
                voice: Optional[Dict] = None):
    """LLM 으로 스타일 요약과 제작 레시피를 만듭니다. 실패 시 휴리스틱."""
    voice = voice or {}
    samples = voice.get("samples", [])
    endings = voice.get("ending_patterns", [])
    if llm.available:
        system = (
            "너는 유튜브 쇼츠 채널 스타일 분석가다. 주어진 통계와 '실제 대사 예시'로 이 채널의 "
            "편집·기획·화법 스타일을 요약하고, 같은 느낌으로 만들기 위한 실전 레시피를 제시한다.\n"
            "가장 중요: [실제 대사 예시]의 말투와 문장 끝맺음(어미)을 그대로 관찰해 'voice' 필드에 "
            "구체적으로 기술하라. 이 채널은 '썰 풀어주는' 화법일 수 있으니, 실제로 쓰는 어미"
            "(예: '~다고 함', '~했는데', '~다는데', '~라고')를 예시와 함께 명시하라. "
            "'정보 전달형/리뷰형' 같은 추상적·오해 소지가 있는 표현은 쓰지 말고, 실제 관찰된 화법만 적어라. "
            "반드시 JSON 하나만 출력한다."
        )
        schema = (
            '{"style_summary": string(3~5문장, 화법 포함), '
            '"recipe": {"hook": string, '
            '"voice": string(실제 말투·문장 끝맺음을 예시 어미와 함께 구체적으로. 반드시 채널 실제 대사 기준), '
            '"dialogue_spec": string(대사 총 글자수·문장 수·문장당 길이·말속도 등 구체 수치 범위), '
            '"pacing": string, "captions": string, '
            '"sfx": string, "transitions": string, "tone": string, '
            '"do": string[], "avoid": string[]}}'
        )
        user = (
            f"[채널] {manifest.get('input')}\n"
            f"[통계] {stats}\n"
            f"[대사 정량지표(평균/최소/최대)] {stats.get('dialogue_stats', {})}\n"
            f"[대표 훅 예시(오인식 보정본)] {hook_examples}\n"
            f"[실제 대사 예시(말투 관찰용, 매우 중요)]\n" +
            "\n".join(f"  - {s}" for s in samples[:8]) + "\n"
            f"[자주 쓰는 문장 끝맺음(어미) 상위]\n{endings}\n"
            f"[자주 쓰는 효과음] {[s['name'] for s in top_sfx[:10]]}\n\n"
            "dialogue_spec 에는 위 정량지표를 근거로 '대사 총 몇 자, 문장 몇 개, 문장당 몇 자, "
            "말속도 몇 자/초, 훅 몇 자 내외'처럼 재현 가능한 구체 수치를 반드시 담아라.\n"
            "voice 에는 [실제 대사 예시]에서 관찰되는 말투와 어미를 반드시 실제 예시와 함께 적어라.\n"
            f"스키마: {schema}"
        )
        try:
            # reasoning 모델(Qwen3.6 등)은 추론에 수천 토큰을 쓰므로 넉넉히 확보
            data = llm.complete_json(system, user, max_tokens=7000)
            if isinstance(data, dict) and "style_summary" in data:
                return data.get("style_summary", ""), data.get("recipe", {})
        except LLMError as exc:
            log.info("스타일 종합 LLM 실패, 휴리스틱 사용: %s", exc)

    pacing = max(stats["pacing_distribution"], key=stats["pacing_distribution"].get) \
        if stats["pacing_distribution"] else "medium"
    summary = (
        f"평균 {stats['avg_duration_sec']}초 길이에 분당 약 {stats['avg_cuts_per_min']}컷의 "
        f"'{pacing}' 페이스 편집이 특징입니다. 화면 자막 사용 비율은 "
        f"{stats['avg_text_overlay_ratio']} 수준이며, 자주 쓰는 효과음은 "
        f"{', '.join(s['name'] for s in top_sfx[:5]) or '분석 데이터 부족'} 입니다."
    )
    dlg = stats.get("dialogue_stats", {})
    ccn = dlg.get("char_count_no_space", {})
    snt = dlg.get("sentence_count", {})
    cps = dlg.get("chars_per_sec", {})
    hlen = dlg.get("hook_char_len", {})
    top_endings = ", ".join(f"'{e}'" for e, _ in endings[:6]) if endings else "채널 대사 기준"
    recipe = {
        "hook": f"첫 1~2초에 강한 훅(예: {hook_examples[0] if hook_examples else '질문/충격 장면'}).",
        "voice": (
            f"실제 대사의 말투·어미를 그대로 모방. 자주 쓰는 문장 끝맺음: {top_endings}. "
            f"예시: {samples[0] if samples else ''}"
        ),
        "dialogue_spec": (
            f"대사 총 {ccn.get('min', 0)}~{ccn.get('max', 0)}자(평균 {ccn.get('avg', 0)}자), "
            f"문장 {snt.get('min', 0)}~{snt.get('max', 0)}개, 말속도 {cps.get('avg', 0)}자/초, "
            f"훅 {hlen.get('avg', 0)}자 내외."
        ),
        "pacing": f"분당 {stats['avg_cuts_per_min']}컷 내외, 평균 샷 {stats['avg_shot_sec']}초.",
        "captions": "핵심 대사를 큰 자막으로 상시 노출.",
        "sfx": f"컷 전환·리액션에 {', '.join(s['name'] for s in top_sfx[:5]) or '효과음'} 배치.",
        "transitions": f"{stats['transition_totals']}",
        "tone": "채널 톤 유지",
        "do": ["훅을 앞에 배치", "빠른 컷 유지", "핵심 자막 강조"],
        "avoid": ["느린 도입부", "정보 과다"],
    }
    return summary, recipe


def render_report(profile: Dict) -> str:
    """스타일 프로파일을 사람이 읽는 마크다운으로 렌더링."""
    ch = profile.get("channel", {})
    stats = profile.get("stats", {})
    recipe = profile.get("recipe", {})
    lines: List[str] = []
    lines.append(f"# 채널 스타일 리포트: {ch.get('input', '')}")
    lines.append("")
    lines.append(profile.get("style_summary", ""))
    lines.append("")
    lines.append("## 편집 지표")
    lines.append(f"- 분석 쇼츠 수: {stats.get('analyzed_count')}")
    lines.append(f"- 평균 길이: {stats.get('avg_duration_sec')}초")
    lines.append(f"- 평균 컷/분: {stats.get('avg_cuts_per_min')}")
    lines.append(f"- 평균 샷 길이: {stats.get('avg_shot_sec')}초")
    lines.append(f"- 페이스 분포: {stats.get('pacing_distribution')}")
    lines.append(f"- 줌 경향: {stats.get('zoom_distribution')}")
    lines.append(f"- 전환 종류: {stats.get('transition_totals')}")
    lines.append(f"- 자막 노출 비율(평균): {stats.get('avg_text_overlay_ratio')}")
    lines.append("")
    dlg = stats.get("dialogue_stats", {})
    if dlg:
        lines.append("## 대사 정량지표 (평균/최소~최대)")

        def _fmt(key: str, label: str, unit: str = "") -> str:
            r = dlg.get(key, {})
            return (f"- {label}: 평균 {r.get('avg', 0)}{unit} "
                    f"({r.get('min', 0)}~{r.get('max', 0)}{unit})")
        lines.append(_fmt("char_count_no_space", "대사 글자수(공백제외)", "자"))
        lines.append(_fmt("word_count", "어절 수", "개"))
        lines.append(_fmt("sentence_count", "문장 수", "개"))
        lines.append(_fmt("avg_sentence_chars", "문장당 글자수", "자"))
        lines.append(_fmt("chars_per_sec", "말속도", "자/초"))
        lines.append(_fmt("hook_char_len", "훅 길이", "자"))
        lines.append("")
    lines.append("## 훅 패턴")
    lines.append(f"- 훅 유형 분포: {stats.get('hook_type_distribution')}")
    for h in profile.get("hook_examples", [])[:6]:
        lines.append(f"  - \"{h}\"")
    lines.append("")
    endings = profile.get("ending_patterns", [])
    samples = profile.get("dialogue_samples", [])
    if endings or samples:
        lines.append("## 말투 / 문체")
        if recipe.get("voice"):
            lines.append(f"- 화법: {recipe['voice']}")
        if endings:
            end_txt = ", ".join(f"{e}({c})" for e, c in endings[:10])
            lines.append(f"- 자주 쓰는 문장 끝맺음(어미): {end_txt}")
        for s in samples[:5]:
            lines.append(f"  - 예: {s}")
        lines.append("")
    layout = profile.get("layout")
    if isinstance(layout, dict):
        lines.append("## 화면 레이아웃 (정규화 0~1, 위=0)")

        def _fmt_region(name: str, key: str) -> None:
            r = layout.get(key)
            if not isinstance(r, dict):
                return
            parts = []
            if r.get("cy") is not None:
                parts.append(f"세로중심 {r['cy']}")
            if r.get("h") is not None:
                parts.append(f"높이 {r['h']}")
            if r.get("w") is not None:
                parts.append(f"너비 {r['w']}")
            lines.append(f"- {name}: {', '.join(parts)}")
        _fmt_region("제목 영역", "title")
        _fmt_region("이미지 영역", "image")
        _fmt_region("자막 영역", "caption")
        lines.append(f"- 표본 수: {layout.get('samples')} (출처: {layout.get('source')})")
        lines.append("")
    img_style = profile.get("image_style")
    if isinstance(img_style, dict) and img_style:
        lines.append("## 사용 이미지 경향")
        if img_style.get("summary"):
            lines.append(f"- 요약: {img_style['summary']}")
        if img_style.get("common_types"):
            types_txt = ", ".join(f"{t}({w})" for t, w in img_style["common_types"])
            lines.append(f"- 이미지 타입 분포(가중치): {types_txt}")
        for s in img_style.get("subjects", [])[:5]:
            lines.append(f"  - 예: {s}")
        if img_style.get("caution"):
            lines.append(f"- 주의: {img_style['caution']}")
        lines.append(f"- 표본 수: {img_style.get('sample_count')}")
        lines.append("")
    edit_style = profile.get("editing_style")
    if isinstance(edit_style, dict) and edit_style:
        lines.append("## 편집 기법 경향")
        if edit_style.get("summary"):
            lines.append(f"- 요약: {edit_style['summary']}")
        lines.append(f"- 영상·움짤 비율: {edit_style.get('motion_clip_ratio')} / "
                     f"정지 이미지 비율: {edit_style.get('still_image_ratio')}")
        lines.append(f"- 주 줌 경향: {edit_style.get('dominant_zoom')} "
                     f"(분포: {edit_style.get('zoom_distribution')})")
        lines.append(f"- 전환 종류: {edit_style.get('transition_types')}")
        lines.append(f"- 평균 샷 길이: {edit_style.get('avg_shot_sec')}초 / "
                     f"모션 레벨: {edit_style.get('motion_level')}")
        if edit_style.get("capcut_effects"):
            lines.append(f"- 권장 캡컷 효과: {', '.join(edit_style['capcut_effects'])}")
        lines.append(f"- 분석 샷 수: {edit_style.get('shots_analyzed')}")
        lines.append("")
    lines.append("## 자주 쓰는 효과음 TOP")
    for s in profile.get("top_sfx", [])[:12]:
        ex = "; ".join(s.get("dialogue_examples", []))
        lines.append(f"- {s['name']} (x{s['count']}, 컷동반 {s['on_cut_ratio']}) {('예: ' + ex) if ex else ''}")
    lines.append("")
    lines.append("## 제작 레시피")
    for key in ("hook", "voice", "dialogue_spec", "pacing", "captions", "sfx", "transitions", "tone"):
        if recipe.get(key):
            lines.append(f"- {key}: {recipe[key]}")
    if recipe.get("do"):
        lines.append(f"- 권장: {', '.join(recipe['do'])}")
    if recipe.get("avoid"):
        lines.append(f"- 지양: {', '.join(recipe['avoid'])}")
    lines.append("")
    return "\n".join(lines)
