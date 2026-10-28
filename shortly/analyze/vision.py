"""대표 프레임 비전 해석 + 단일 쇼츠의 훅/재미/구조 LLM 분석(휴리스틱 폴백 포함)."""

from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Optional

from ..llm.base import LLMClient, LLMError, LLMQuotaExceeded
from ..utils import log

_FRAME_PROMPT = (
    "이 이미지는 유튜브 쇼츠의 한 프레임이다. 한국어로 한 문장으로 간결히 묘사하라. "
    "화면에 큰 자막 텍스트가 보이면 그 내용을 따옴표로 포함하라."
)


def describe_keyframes(llm: LLMClient, keyframes: List[str],
                       max_frames: int = 4) -> List[Dict]:
    """비전 모델로 대표 프레임을 묘사합니다. 사용 불가 시 빈 리스트."""
    if not llm.available or not keyframes:
        return []
    out: List[Dict] = []
    for kf in keyframes[:max_frames]:
        p = Path(kf)
        if not p.is_file():
            continue
        try:
            desc = llm.describe_image(p, _FRAME_PROMPT)
        except LLMQuotaExceeded:
            raise
        except LLMError as exc:
            log.info("프레임 묘사 실패: %s", exc)
            continue
        if desc:
            out.append({"frame": p.name, "description": desc})
    return out


def _heuristic_short_analysis(transcript: Dict, cv_result: Dict) -> Dict:
    metrics = transcript.get("metrics", {})
    title_guess = transcript.get("screen_title_guess", "")
    cues = transcript.get("cues", [])
    hook_text = title_guess or (cues[0]["text"] if cues else "")
    full = transcript.get("full_text", "")
    return {
        "topic": full[:40],
        "hook_text": hook_text,
        "hook_type": "질문형" if "?" in hook_text or "까" in hook_text else "선언형",
        "humor_devices": [],
        "structure": f"컷 {cv_result.get('num_cuts', 0)}개, {cv_result.get('pacing', 'unknown')} 페이스",
        "tone": "unknown",
        "dialogue_metrics": metrics,
        "_source": "heuristic",
    }


def analyze_short(llm: LLMClient, transcript: Dict, cv_result: Dict,
                  sfx_result: Dict, frame_descs: List[Dict]) -> Dict:
    """단일 쇼츠의 훅/재미/구조를 분석합니다."""
    if not llm.available:
        return _heuristic_short_analysis(transcript, cv_result)

    metrics = transcript.get("metrics", {})
    sfx_names = [u["name"] for u in sfx_result.get("sfx_usages", [])][:10]
    frames_txt = "\n".join(f"- {d['description']}" for d in frame_descs) or "(없음)"
    on_screen = transcript.get("on_screen_text") or []
    on_screen_txt = "\n".join(f"- {o.get('text', '')}" for o in on_screen[:6]) or "(없음)"
    metrics_txt = (
        f"총 글자수(공백제외) {metrics.get('char_count_no_space', 0)}자, "
        f"어절 {metrics.get('word_count', 0)}개, 문장 {metrics.get('sentence_count', 0)}개, "
        f"문장당 평균 {metrics.get('avg_sentence_chars', 0)}자, "
        f"발화 {metrics.get('speech_duration_sec', 0)}초, "
        f"말속도 {metrics.get('chars_per_sec', 0)}자/초"
    )
    system = (
        "너는 유튜브 쇼츠 편집·기획 분석가다. 주어진 정보로 이 쇼츠의 훅과 재미 요소, "
        "구조를 분석한다. 반드시 아래 스키마의 JSON 하나만 출력한다.\n"
        "매우 중요: [대사 전문]은 유튜브 자동자막/음성인식 결과이고 [화면자막 표본]은 "
        "OCR 결과라 오탈자·띄어쓰기 오류·인접 문장 혼입이 잦다(예: '핫도그'가 '아아도그'로, "
        "'영업기밀'이 '영업김에'로). 원문을 그대로 베끼지 말고 문맥으로 자연스럽게 오인식을 "
        "보정해 실제 발화/제목을 복원하라. hook_text 에는 화면 상단 제목(고정 자막)을 보정한 "
        "완성 문장을 넣되, 뒤에 딸려온 다음 문장 조각은 제거하라.\n"
        "voice_style/sentence_endings 에는 이 영상 대사의 실제 말투와 문장 끝맺음(어미)을 "
        "관찰해 담아라(예: '~다고 함', '~했는데', '~다는데'). '정보 전달형' 같은 추상 표현 대신 "
        "실제 어미를 근거로 적어라."
    )
    schema = (
        '{"topic": string, "hook_text": string(보정된 실제 첫 훅/제목), '
        '"hook_type": string(질문형|반전예고형|충격장면형|선언형|공감형|기타), '
        '"humor_devices": string[], "structure": string, '
        '"tone": string, "connective_devices": string[], '
        '"voice_style": string(화법·말투를 실제 어미 예시와 함께. 예: "썰 풀듯 반말체, ~다고 함/~했는데"), '
        '"sentence_endings": string[](실제로 쓰인 문장 끝맺음 어미 목록, 예: ["다고 함","했는데","다는데"]), '
        '"corrected_dialogue_sample": string(오인식 보정한 대사 앞부분 1~2문장), '
        '"suspected_misrecognitions": string[](원문→보정 형태, 예: "아아도그→핫도그")}'
    )
    user = (
        f"[대사 전문(자동자막·오류 포함)]\n{transcript.get('full_text', '')[:1500]}\n\n"
        f"[첫 자막 줄(원문)]\n{(transcript.get('cues') or [{}])[0].get('text', '')}\n\n"
        f"[화면자막 표본(OCR·오류 포함)]\n{on_screen_txt}\n\n"
        f"[화면 제목 추정(어절 공통, 노이즈 있음)]\n{transcript.get('screen_title_guess', '') or '(없음)'}\n\n"
        f"[대사 정량지표]\n{metrics_txt}\n\n"
        f"[편집 지표] 길이 {cv_result.get('duration_sec')}s, 컷 {cv_result.get('num_cuts')}개, "
        f"페이스 {cv_result.get('pacing')}, 줌경향 {cv_result.get('zoom_tendency')}, "
        f"전환 {cv_result.get('transition_summary')}\n\n"
        f"[감지된 효과음] {', '.join(sfx_names) or '없음'}\n\n"
        f"[대표 프레임 묘사]\n{frames_txt}\n\n"
        f"스키마: {schema}"
    )
    try:
        # reasoning 모델은 추론에 수천 토큰을 쓰므로 넉넉히 확보(기본 4096으로는 잘릴 수 있음)
        data = llm.complete_json(system, user, max_tokens=7000)
        if isinstance(data, dict):
            data["_source"] = "llm"
            data.setdefault("dialogue_metrics", metrics)
            return data
    except LLMQuotaExceeded:
        raise
    except LLMError as exc:
        log.info("쇼츠 LLM 분석 실패, 휴리스틱 사용: %s", exc)
    return _heuristic_short_analysis(transcript, cv_result)
