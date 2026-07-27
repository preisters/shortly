"""대사 추출: 유튜브 자막(VTT) 우선 → 없으면 ASR → 화면자막 OCR 병행."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Dict, List, Optional

from ..utils import decode_audio_mono, log

_TS_RE = re.compile(r"(\d{2}):(\d{2}):(\d{2})[.,](\d{3})")
_TAG_RE = re.compile(r"<[^>]+>")
_BRACKET_RE = re.compile(r"\[[^\]]*\]")          # [음악], [박수] 등 비언어 태그
_SENT_SPLIT_RE = re.compile(r"[.!?。！？…]+|\n+")


def _clean_caption(text: str) -> str:
    """자막 한 줄 정제: HTML 엔티티/화자표시/비언어 태그 제거."""
    if not text:
        return ""
    text = (text.replace("&gt;", ">").replace("&lt;", "<").replace("&amp;", "&")
            .replace("&nbsp;", " ").replace("&#39;", "'").replace("&quot;", '"'))
    text = re.sub(r">>+", " ", text)             # ">> " 화자 전환 표시
    text = _BRACKET_RE.sub(" ", text)            # [음악] 등
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _merge_rolling_cues(cues: List[Dict]) -> str:
    """유튜브 롤링(누적) 자막을 어절 단위 겹침 제거로 자연스러운 나레이션으로 복원."""
    words: List[str] = []
    for c in cues:
        t = _clean_caption(c.get("text", ""))
        if not t:
            continue
        cw = t.split()
        max_ov = min(len(words), len(cw))
        overlap = 0
        for k in range(max_ov, 0, -1):
            if words[-k:] == cw[:k]:
                overlap = k
                break
        words.extend(cw[overlap:])
    return " ".join(words)


def _text_metrics(clean_text: str, cues: List[Dict]) -> Dict:
    """대사의 정량 지표(글자/어절/문장 수, 발화 속도 등)."""
    no_space = re.sub(r"\s", "", clean_text)
    sentences = [s for s in _SENT_SPLIT_RE.split(clean_text) if s.strip()]
    words = clean_text.split()
    start = cues[0].get("start", 0.0) if cues else 0.0
    end = cues[-1].get("end", 0.0) if cues else 0.0
    dur = round(max(0.0, (end or 0.0) - (start or 0.0)), 2)
    return {
        "char_count": len(clean_text),
        "char_count_no_space": len(no_space),
        "word_count": len(words),
        "sentence_count": len(sentences),
        "cue_count": len(cues),
        "avg_sentence_chars": round(len(no_space) / len(sentences), 1) if sentences else 0.0,
        "avg_word_chars": round(len(no_space) / len(words), 1) if words else 0.0,
        "speech_duration_sec": dur,
        "chars_per_sec": round(len(no_space) / dur, 2) if dur > 0 else 0.0,
        "words_per_sec": round(len(words) / dur, 2) if dur > 0 else 0.0,
    }


def _screen_title_guess(on_screen: List[Dict]) -> str:
    """화면자막 표본들의 어절 단위 최장 공통 접두를 추정 제목으로 반환(노이즈 있음)."""
    lines = [_clean_caption(o.get("text", "")).split() for o in on_screen]
    lines = [ln for ln in lines if ln]
    if len(lines) < 2:
        return " ".join(lines[0]) if lines else ""
    prefix = lines[0]
    for ln in lines[1:]:
        j = 0
        while j < len(prefix) and j < len(ln) and prefix[j] == ln[j]:
            j += 1
        prefix = prefix[:j]
        if not prefix:
            break
    return " ".join(prefix)


def _parse_ts(ts: str) -> Optional[float]:
    m = _TS_RE.match(ts.strip())
    if not m:
        return None
    h, mnt, s, ms = (int(x) for x in m.groups())
    return h * 3600 + mnt * 60 + s + ms / 1000.0


def parse_vtt(path: Path) -> List[Dict]:
    """VTT 자막 -> [{start, end, text}]. 중복 줄 제거."""
    try:
        text = Path(path).read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return []
    cues: List[Dict] = []
    lines = text.splitlines()
    i = 0
    last_text = ""
    while i < len(lines):
        line = lines[i].strip()
        if "-->" in line:
            left, _, right = line.partition("-->")
            start = _parse_ts(left.strip().split(" ")[0])
            end = _parse_ts(right.strip().split(" ")[0])
            i += 1
            buf: List[str] = []
            while i < len(lines) and lines[i].strip() and "-->" not in lines[i]:
                buf.append(_TAG_RE.sub("", lines[i]).strip())
                i += 1
            content = " ".join(x for x in buf if x).strip()
            content = re.sub(r"\s+", " ", content)
            if content and content != last_text and start is not None:
                cues.append({"start": round(start, 3),
                             "end": round(end or start, 3), "text": content})
                last_text = content
        else:
            i += 1
    return cues


def _pick_subtitle(subtitle_paths: List[str]) -> Optional[Path]:
    """수동 ko > 자동 ko > en 우선순위."""
    if not subtitle_paths:
        return None
    paths = [Path(p) for p in subtitle_paths]

    def rank(p: Path) -> int:
        name = p.name.lower()
        if ".ko" in name and "auto" not in name:
            return 0
        if ".ko" in name:
            return 1
        if ".en" in name:
            return 2
        return 3

    return sorted(paths, key=rank)[0]


def _asr_transcribe(video_path: Path) -> List[Dict]:
    """faster-whisper ASR 폴백. 미설치 시 빈 리스트."""
    try:
        from faster_whisper import WhisperModel  # type: ignore
    except ImportError:
        log.info("faster-whisper 미설치: ASR 건너뜀")
        return []
    samples = decode_audio_mono(video_path, sr=16000)
    if samples is None:
        return []
    try:
        model = WhisperModel("small", device="cpu", compute_type="int8")
        segments, _ = model.transcribe(samples, language=None, vad_filter=True)
        return [{"start": round(seg.start, 3), "end": round(seg.end, 3),
                 "text": seg.text.strip()} for seg in segments if seg.text.strip()]
    except Exception as exc:  # noqa: BLE001
        log.warning("ASR 실패: %s", exc)
        return []


def _box_to_item(bbox, txt: str, w: int, h: int) -> Optional[Dict]:
    """easyocr 박스(4점)를 정규화 항목으로 변환."""
    txt = (txt or "").strip()
    if not txt or w <= 0 or h <= 0:
        return None
    xs = [float(p[0]) for p in bbox]
    ys = [float(p[1]) for p in bbox]
    x0, x1 = min(xs), max(xs)
    y0, y1 = min(ys), max(ys)
    return {
        "text": txt,
        "cx": round((x0 + x1) / 2 / w, 3),
        "cy": round((y0 + y1) / 2 / h, 3),
        "bw": round((x1 - x0) / w, 3),
        "bh": round((y1 - y0) / h, 3),
    }


def _ocr_on_screen(video_path: Path, num_frames: int = 8):
    """easyocr 로 화면자막 텍스트+박스를 표본 추출.

    반환: (on_screen[{time,text}], frames_items[[{text,cx,cy,bw,bh}...]])
    미설치/실패 시 ([], []).
    """
    cv2 = None
    try:
        import cv2 as _cv2  # type: ignore
        cv2 = _cv2
        import easyocr  # type: ignore
    except ImportError:
        log.info("opencv/easyocr 미설치: 화면자막 OCR 건너뜀")
        return [], []
    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        return [], []
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    if total <= 0:
        cap.release()
        return [], []
    try:
        reader = easyocr.Reader(["ko", "en"], gpu=False, verbose=False)
    except Exception as exc:  # noqa: BLE001
        log.warning("easyocr 초기화 실패: %s", exc)
        cap.release()
        return [], []
    results: List[Dict] = []
    frames_items: List[List[Dict]] = []
    idxs = [int(total * k / (num_frames + 1)) for k in range(1, num_frames + 1)]
    for fi in idxs:
        cap.set(cv2.CAP_PROP_POS_FRAMES, fi)
        ok, frame = cap.read()
        if not ok:
            continue
        try:
            # detail=1: [bbox, text] (paragraph=True 이면 신뢰도 생략) — 박스 좌표 확보
            entries = reader.readtext(frame, detail=1, paragraph=True)
        except Exception:  # noqa: BLE001
            continue
        fh, fw = frame.shape[:2]
        items: List[Dict] = []
        texts: List[str] = []
        for entry in entries:
            try:
                bbox, txt = entry[0], entry[1]
            except (IndexError, TypeError):
                continue
            item = _box_to_item(bbox, txt, fw, fh)
            if item:
                items.append(item)
                texts.append(item["text"])
        if items:
            frames_items.append(items)
        joined = " ".join(texts)
        if joined:
            results.append({"time": round(fi / fps, 2), "text": joined})
    cap.release()
    return results, frames_items


def extract_transcript(video_info: Dict, do_ocr: bool = True) -> Dict:
    """단일 영상의 대사/화면자막을 추출합니다."""
    subtitle_paths = video_info.get("subtitle_paths") or []
    video_path = video_info.get("video_path")

    cues: List[Dict] = []
    source = "none"
    sub = _pick_subtitle(subtitle_paths)
    if sub is not None:
        cues = parse_vtt(sub)
        if cues:
            source = "youtube_auto" if "auto" in sub.name.lower() else "youtube_manual"

    if not cues and video_path and Path(video_path).is_file():
        cues = _asr_transcribe(Path(video_path))
        if cues:
            source = "asr"

    on_screen: List[Dict] = []
    layout: Optional[Dict] = None
    if do_ocr and video_path and Path(video_path).is_file():
        on_screen, frames_items = _ocr_on_screen(Path(video_path))
        if frames_items:
            from .layout import derive_layout_from_ocr
            layout = derive_layout_from_ocr(frames_items)

    # 롤링/중복 자막을 정제해 실제 나레이션에 가깝게 복원
    clean_text = _merge_rolling_cues(cues)
    raw_joined = " ".join(c["text"] for c in cues).strip()
    metrics = _text_metrics(clean_text, cues)
    return {
        "source": source,
        "cues": cues,
        "full_text": clean_text,          # 정제된 나레이션(중복 제거)
        "raw_full_text": raw_joined,       # 참고용 원본(롤링 포함)
        "on_screen_text": on_screen,
        "screen_title_guess": _screen_title_guess(on_screen),
        "char_count": metrics["char_count"],
        "metrics": metrics,
        "layout": layout,                  # 화면 레이아웃 영역(OCR 박스 추정)
    }
