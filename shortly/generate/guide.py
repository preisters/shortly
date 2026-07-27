"""구간별 제작 가이드 빌더: TTS 설정 + 컷/자막/전환 지시 + 효과음 추천 타이밍표."""

from __future__ import annotations

import re
from typing import Dict, List, Optional

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


def _estimate_duration(text: str) -> float:
    """한국어 대략 읽기 속도(초). 6~7음절/초 가정."""
    chars = len(re.sub(r"\s", "", text))
    return round(max(1.0, chars / 6.5), 1)


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
    recipe = profile.get("recipe", {})
    tone = recipe.get("tone", "")
    top_sfx_names = [s["name"] for s in profile.get("top_sfx", [])]
    avg_shot = stats.get("avg_shot_sec", 2.0) or 2.0

    segments: List[Dict] = []
    cursor = 0.0
    for seg in script.get("segments", []):
        text = seg.get("text", "")
        intent = seg.get("intent", "")
        dur = _estimate_duration(text)
        # 채널 평균 샷 길이에 맞춰 컷 수 제안
        cuts_in_seg = max(1, round(dur / avg_shot))
        seg_guide = {
            "index": seg.get("index"),
            "start_sec": round(cursor, 1),
            "end_sec": round(cursor + dur, 1),
            "duration_sec": dur,
            "narration": text,
            "intent": intent,
            "caption": text if len(text) <= 22 else text[:20] + "…",
            "cuts_suggested": cuts_in_seg,
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
        "target_pacing": recipe.get("pacing", ""),
        "channel": profile.get("channel", {}),
        "segments": segments,
        "global_notes": {
            "captions": recipe.get("captions", ""),
            "transitions": recipe.get("transitions", ""),
            "tone": tone,
            "do": recipe.get("do", []),
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
    lines.append("")
    lines.append("## 구간별 지시")
    for s in guide.get("segments", []):
        lines.append("")
        lines.append(f"### {s['index'] + 1}. {s['start_sec']}s ~ {s['end_sec']}s ({s['duration_sec']}초)")
        lines.append(f"- 나레이션: {s['narration']}")
        lines.append(f"- 자막: {s['caption']}")
        lines.append(f"- 컷 수: {s['cuts_suggested']} / 카메라: {s['camera']} / 전환: {s['transition_out']}")
        tts = s["tts"]
        lines.append(f"- TTS: 속도 {tts['speed']}, 감정 {tts['emotion']} ({tts['voice_hint']})")
        lines.append(f"- 효과음: {', '.join(s['sfx']) or '없음'}")
        if s.get("image_suggestions"):
            lines.append(f"- 이미지 추천: {', '.join(s['image_suggestions'])}")
        if s.get("media_suggestions"):
            lines.append(f"- 매체: {', '.join(s['media_suggestions'])}")
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
