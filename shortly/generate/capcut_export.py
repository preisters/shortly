"""제작 가이드를 CapCut 초안(draft) JSON 으로 내보냅니다.

주의: CapCut 정식 draft_content.json 스키마는 방대하고 버전마다 다릅니다.
여기서는 편집 시작점으로 쓸 수 있는 '구조화 초안'을 생성합니다.
- 텍스트(자막) 트랙, 효과음 트랙, 나레이션(TTS) 자리표시 트랙을 타임라인으로 배치
- CapCut 에 그대로 임포트되기보다는, 트랙/타이밍 설계도로 활용하고
  실제 편집 시 참고하도록 설계했습니다(확장 여지 있음).
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import shutil
import time
import uuid
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from ..utils import log, write_json

_US = 1_000_000  # CapCut 은 마이크로초 단위 사용


def _uid() -> str:
    return uuid.uuid4().hex


def export_capcut_draft(guide: Dict, out_path: Path) -> Dict:
    """가이드 -> CapCut 초안 JSON 저장 후 draft dict 반환."""
    text_segments: List[Dict] = []
    audio_segments: List[Dict] = []
    text_materials: List[Dict] = []
    audio_materials: List[Dict] = []

    for s in guide.get("segments", []):
        start_us = int(round(s["start_sec"] * _US))
        dur_us = int(round(s["duration_sec"] * _US))

        # 자막(텍스트) 소재/세그먼트
        tmat_id = _uid()
        text_materials.append({
            "id": tmat_id,
            "type": "text",
            "content": _caption_safe_korean(s.get("caption", "")),
            "font_size": 12,
            "text_color": "#FFFFFF",
        })
        text_segments.append({
            "id": _uid(),
            "material_id": tmat_id,
            "target_timerange": {"start": start_us, "duration": dur_us},
            "note": f"나레이션: {s.get('narration', '')}",
        })

        # 효과음 세그먼트(추천 첫 번째를 컷 시작에 배치)
        for k, sfx_name in enumerate(s.get("sfx", [])[:2]):
            amat_id = _uid()
            audio_materials.append({
                "id": amat_id,
                "type": "sound_effect",
                "name": sfx_name,
                "note": "sfx_sound 라이브러리에서 해당 이름 파일 사용",
            })
            audio_segments.append({
                "id": _uid(),
                "material_id": amat_id,
                "target_timerange": {"start": start_us + k * 150_000,
                                     "duration": min(dur_us, 1_200_000)},
            })

    total_us = int(round(guide.get("total_duration_sec", 0) * _US))
    draft = {
        "shortly_schema": "capcut-draft-v0",
        "title": guide.get("title", ""),
        "hook": guide.get("hook", ""),
        "duration": total_us,
        "canvas_config": {"width": 1080, "height": 1920, "ratio": "9:16"},
        "materials": {
            "texts": text_materials,
            "audios": audio_materials,
        },
        "tracks": [
            {"type": "text", "segments": text_segments},
            {"type": "audio", "attribute": "sfx", "segments": audio_segments},
            {"type": "audio", "attribute": "narration_tts_placeholder", "segments": []},
        ],
        "notes": [
            "이 파일은 편집 설계도(초안)입니다.",
            "효과음 이름은 sfx_sound 폴더의 파일과 매칭해 배치하세요.",
            "나레이션 트랙에는 생성한 TTS 음성을 올리세요.",
        ],
    }
    write_json(out_path, draft)
    return draft


# ==========================================================================
# 이 PC의 CapCut 프로젝트로 직접 내보내기
# --------------------------------------------------------------------------
# CapCut 의 정식 draft 스키마(draft_content.json)는 매우 방대하고 버전마다 다릅니다.
# 안전하게 "열리는" 프로젝트를 만들기 위해, 이 PC 에 이미 존재하는 최신 프로젝트를
# 구조 템플릿으로 삼아(=현재 설치된 CapCut 버전과 100% 호환) 복제한 뒤,
# 트랙/소재를 우리 자막으로 교체하는 방식을 사용합니다.
# ==========================================================================

_ID_CANDIDATES = (
    "CapCut/User Data/Projects/com.lveditor.draft",
    "JianyingPro/User Data/Projects/com.lveditor.draft",
)


def _new_id() -> str:
    return str(uuid.uuid4()).upper()


def find_capcut_draft_root(override: Optional[str] = None) -> Optional[Path]:
    """이 PC 의 CapCut 드래프트(프로젝트) 폴더를 찾습니다."""
    if override:
        p = Path(override)
        return p if p.is_dir() else None
    roots: List[Path] = []
    local = os.environ.get("LOCALAPPDATA")
    appdata = os.environ.get("APPDATA")
    for base in (local, appdata):
        if not base:
            continue
        for rel in _ID_CANDIDATES:
            roots.append(Path(base) / rel)
    for r in roots:
        if r.is_dir():
            return r
    return None


def _read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


_CANVAS_W = 1080
_CANVAS_H = 1920
_MAX_LINE_CHARS = 12       # 자막 한 줄 최대 글자수(가로 넘침 방지)
_MAX_FONT_SIZE = 8.0       # 자막 폰트 크기 상한(넘침 방지)
_PHOTO_DURATION = 10_800_000_000  # CapCut 스틸 이미지 기본 길이(us)

# --- 소스 채널 레이아웃 근사(분석 결과 기반) ---
# 소스: 상단 고정 제목 + 테두리(프레임) 안의 이미지 + 하단에 한 줄씩 바뀌는 자막.
_IMG_SCALE = 0.86          # 컷 이미지 스케일(화면을 꽉 채우지 않고 테두리 여백을 남김)
_IMG_TRANSFORM_Y = 0.02    # 컷 이미지 세로 위치(살짝 위)
_TITLE_TRANSFORM_Y = 0.80  # 제목 위치(상단)
_TITLE_MAX_CHARS = 12      # 제목 한 줄 최대 글자수
_TITLE_FONT_SIZE = 11.0    # 제목 폰트 크기(자막보다 큼)
_CAPTION_TRANSFORM_Y = -0.74  # 자막 위치(하단)
_CAPTION_MAX_CHARS = 14    # 자막 한 줄 최대 글자수
_CAPTION_UNIT_LEN = 18     # 자막을 이 글자수 내외의 짧은 단위로 쪼개 순차 노출
_RECO_FONT_SIZE = 5.5      # 컷 이미지 아래 편집용 추천 메모 폰트(작게)
_RECO_MAX_CHARS = 22       # 추천 메모 한 줄 최대 글자수
_RECO_Y_GAP = 0.06         # 컷 카드 아래로 띄우는 간격(transform y)
_BG_COLOR = (30, 32, 41)   # 기본 프레임 배경색(어두운 슬레이트)
_CARD_FILL = (240, 240, 240)   # 컷 이미지 카드 배경
_CARD_BORDER = (120, 120, 130)  # 컷 이미지 카드 테두리

# 챙맛 채널에서 사람이 직접 조정한 프로젝트. 이 프로젝트들의 콘텐츠 이미지
# 세그먼트를 소재 템플릿으로만 사용하고, 원본 프로젝트 자체는 수정하지 않는다.
_REFERENCE_PROJECT_MARKERS = (
    "사실 너만 모르던 스테디셀러 떡",
    "사실 너만 모르던 스터디셀러 떡",
    "장난감이 소금빵이 돼버린 왁뿌볼",
    "억지로 유행시킨 버터떡",
    "사실 정체를 아무도 모르는 우베",
)
_REFERENCE_SCAFFOLD_FILES = (
    "챙맛.png",
    "프로필_이미지-removebg-preview.png",
    "blank_1080x1920.png",
)

def _find_font(*needles: str) -> Optional[str]:
    """설치된 폰트 폴더(사용자/시스템)에서 파일명이 needle 을 포함하는 폰트를 찾습니다."""
    dirs = [
        os.path.join(os.environ.get("LOCALAPPDATA", ""), "Microsoft", "Windows", "Fonts"),
        os.path.join(os.environ.get("WINDIR", ""), "Fonts"),
    ]
    for d in dirs:
        p = Path(d)
        if not p.is_dir():
            continue
        for f in sorted(p.iterdir()):
            if f.is_file() and f.suffix.lower() in (".ttf", ".otf", ".ttc"):
                nm = f.name.lower()
                if any(n in nm for n in needles):
                    return str(f)
    return None


# 자막·제목·컷 이미지 글씨에 사용할 폰트: 배민 도현체(BM DoHyeon)
_CAPTION_FONT_PATH = _find_font("bmdohyeon", "dohyeon")
_CAPTION_FONT_NAME = "BM DoHyeon"

_KR_FONTS = tuple(
    p for p in (
        _CAPTION_FONT_PATH,
        r"C:\Windows\Fonts\malgunbd.ttf",
        r"C:\Windows\Fonts\malgun.ttf",
        r"C:\Windows\Fonts\gulim.ttc",
    ) if p
)


def _to_transform_y(cy: float) -> float:
    """정규화 세로중심(위=0, 아래=1) -> CapCut transform y(+위/-아래, 중앙=0)."""
    return round(max(-0.95, min(0.95, (0.5 - cy) * 2)), 3)


def _layout_params(layout: Optional[Dict]) -> Dict:
    """학습된 레이아웃(정규화)을 CapCut 배치값으로 변환. 없으면 기본 상수."""
    p = {"title_y": _TITLE_TRANSFORM_Y, "caption_y": _CAPTION_TRANSFORM_Y,
         "img_scale": _IMG_SCALE, "img_y": _IMG_TRANSFORM_Y}
    if not isinstance(layout, dict):
        return p
    t, c, im = layout.get("title"), layout.get("caption"), layout.get("image")
    if isinstance(t, dict) and isinstance(t.get("cy"), (int, float)):
        p["title_y"] = _to_transform_y(t["cy"])
    if isinstance(c, dict) and isinstance(c.get("cy"), (int, float)):
        p["caption_y"] = _to_transform_y(c["cy"])
    if isinstance(im, dict):
        if isinstance(im.get("h"), (int, float)) and im["h"] > 0:
            p["img_scale"] = round(max(0.35, min(0.98, float(im["h"]))), 3)
        if isinstance(im.get("cy"), (int, float)):
            p["img_y"] = _to_transform_y(im["cy"])
    return p


def _index_materials(mats: Dict) -> Dict[str, tuple]:
    """소재 id -> (배열이름, 소재dict) 색인."""
    idx: Dict[str, tuple] = {}
    for arr_name, arr in (mats or {}).items():
        if isinstance(arr, list):
            for m in arr:
                if isinstance(m, dict) and "id" in m:
                    idx[m["id"]] = (arr_name, m)
    return idx


def _pick_templates(root: Path):
    """base 스켈레톤 + 텍스트/이미지(비디오) 세그먼트 템플릿을 수집합니다.

    현재 설치된 CapCut 이 실제로 만든 요소를 그대로 복제하므로 버전 호환이 보장됩니다.
    """
    projs = [d for d in root.iterdir()
             if d.is_dir() and (d / "draft_content.json").is_file()]
    projs.sort(key=lambda d: d.stat().st_mtime, reverse=True)
    base = None
    text_tpl = None
    image_tpl = None
    for d in projs:
        try:
            dc = _read_json(d / "draft_content.json")
        except (OSError, ValueError):
            continue
        mats = dc.get("materials") or {}
        idx = _index_materials(mats)
        if base is None:
            try:
                meta = _read_json(d / "draft_meta_info.json")
            except (OSError, ValueError):
                meta = None
            base = {"content": dc, "meta": meta}
        if text_tpl is None:
            for t in dc.get("tracks", []):
                if t.get("type") == "text" and t.get("segments"):
                    text_tpl = {"seg": t["segments"][0], "materials": mats}
                    break
        if image_tpl is None or not image_tpl.get("is_photo"):
            for t in dc.get("tracks", []):
                if t.get("type") != "video" or not t.get("segments"):
                    continue
                photo_seg = None
                for seg in t["segments"]:
                    m = idx.get(seg.get("material_id"))
                    if m and m[1].get("type") == "photo":
                        photo_seg = seg
                        break
                if photo_seg is not None:
                    image_tpl = {"seg": photo_seg, "materials": mats,
                                 "is_photo": True}
                    break
                if image_tpl is None:
                    image_tpl = {"seg": t["segments"][0], "materials": mats,
                                 "is_photo": False}
        if base and text_tpl and image_tpl and image_tpl.get("is_photo"):
            break
    return base, text_tpl, image_tpl


def _pick_reference_image_template(root: Path):
    """지정된 챙맛 레퍼런스 프로젝트에서 실제 콘텐츠 이미지 세그먼트를 찾는다."""

    try:
        projects = [
            path
            for path in root.iterdir()
            if path.is_dir()
            and (path / "draft_content.json").is_file()
            and any(marker in path.name for marker in _REFERENCE_PROJECT_MARKERS)
        ]
    except OSError:
        projects = []
    projects.sort(key=lambda path: path.stat().st_mtime, reverse=True)
    for project in projects:
        try:
            document = _read_json(project / "draft_content.json")
        except (OSError, ValueError):
            continue
        materials = document.get("materials") or {}
        index = _index_materials(materials)
        for track in document.get("tracks") or []:
            if track.get("type") != "video":
                continue
            for segment in track.get("segments") or []:
                info = index.get(segment.get("material_id"))
                if not info:
                    continue
                material = info[1]
                if material.get("type") != "photo":
                    continue
                filename = Path(str(material.get("path") or "")).name
                if filename in _REFERENCE_SCAFFOLD_FILES:
                    continue
                return {
                    "seg": segment,
                    "materials": materials,
                    "project": project.name,
                }
    return None


def _reference_asset_layout(
    width: int,
    height: int,
    media_kind: str = "image",
) -> Tuple[float, float]:
    """수동 레퍼런스 108개 클립의 종횡비별 중앙값에 가까운 배치를 반환한다."""

    ratio = width / height if width > 0 and height > 0 else 2 / 3
    if media_kind == "video" and ratio < 0.8:
        return 0.74, -0.1
    if ratio < 0.8:
        return 0.64, -0.21
    if ratio <= 1.2:
        return 0.72, -0.12
    return 0.8, -0.1


def _asset_dimensions(path: Path, media_kind: str) -> Tuple[int, int]:
    if media_kind != "video":
        try:
            from PIL import Image

            with Image.open(path) as image:
                width, height = image.size
                if width > 0 and height > 0:
                    return int(width), int(height)
        except (ImportError, OSError, ValueError):
            pass
    return _CANVAS_W, _CANVAS_H


def _clone_seg(seg: Dict, src_mats: Dict):
    """세그먼트와 그것이 참조하는 모든 소재를 새 id 로 복제(참조 무결성 유지)."""
    idx = _index_materials(src_mats)
    new_seg = copy.deepcopy(seg)
    new_seg["id"] = _new_id()
    remap: Dict[str, str] = {}
    cloned: Dict[str, List[Dict]] = {}
    main_new = None
    refs: List[str] = []
    if seg.get("material_id"):
        refs.append(seg["material_id"])
    refs += list(seg.get("extra_material_refs") or [])
    for rid in refs:
        if rid in remap:
            continue
        info = idx.get(rid)
        if not info:
            continue
        arr_name, mat = info
        nm = copy.deepcopy(mat)
        nm["id"] = _new_id()
        remap[rid] = nm["id"]
        cloned.setdefault(arr_name, []).append(nm)
        if rid == seg.get("material_id"):
            main_new = nm
    new_seg["material_id"] = remap.get(seg.get("material_id"), "")
    new_seg["extra_material_refs"] = [remap[r] for r in
                                      (seg.get("extra_material_refs") or [])
                                      if r in remap]
    new_seg["group_id"] = ""
    new_seg["common_keyframes"] = []
    new_seg["keyframe_refs"] = []
    return new_seg, cloned, main_new


def _merge_materials(dst: Dict, cloned: Dict) -> None:
    for arr_name, items in cloned.items():
        if not isinstance(dst.get(arr_name), list):
            dst[arr_name] = []
        dst[arr_name].extend(items)


def _wrap_korean(text: str, max_chars: int = _MAX_LINE_CHARS) -> str:
    """긴 문장을 줄바꿈해 가로 넘침을 방지(수동 개행 삽입)."""
    text = re.sub(r"\s+", " ", (text or "").strip())
    if not text:
        return ""
    lines: List[str] = []
    cur = ""
    for token in text.split(" "):
        while len(token) > max_chars:
            if cur:
                lines.append(cur)
                cur = ""
            lines.append(token[:max_chars])
            token = token[max_chars:]
        if not cur:
            cur = token
        elif len(cur) + 1 + len(token) <= max_chars:
            cur += " " + token
        else:
            lines.append(cur)
            cur = token
    if cur:
        lines.append(cur)
    return "\n".join(lines)


def _split_two_lines(text: str) -> Tuple[str, str]:
    """제목을 두 줄로 균형 있게 나눈다(인트로 제목 카드용).

    가운데 글자 위치에 가장 가까운 공백에서 끊어, 수동 편집에서 쓰던
    '억지로 유행시킨' / '버터떡' 같은 두 줄 분배와 맞춘다. 공백이 없으면
    글자 중앙에서 나눈다.
    """
    t = re.sub(r"\s+", " ", (text or "").strip())
    if not t:
        return "", ""
    spaces = [i for i, ch in enumerate(t) if ch == " "]
    if spaces:
        mid = len(t) / 2.0
        best = min(spaces, key=lambda i: abs(i - mid))
        return t[:best].strip(), t[best + 1:].strip()
    if len(t) <= 1:
        return t, ""
    half = len(t) // 2
    return t[:half], t[half:]


def _clip(scale: float = 1.0, y: float = 0.0) -> Dict:
    """세그먼트 배치(scale=크기 배율, y=세로 위치. +위/-아래)."""
    return {"scale": {"x": scale, "y": scale}, "rotation": 0.0,
            "transform": {"x": 0.0, "y": y},
            "flip": {"horizontal": False, "vertical": False}, "alpha": 1.0}


def _split_display_units(text: str, unit_len: int = _CAPTION_UNIT_LEN) -> List[str]:
    """긴 나레이션을 소스처럼 '한 번에 한 줄'로 보여줄 짧은 단위들로 분할."""
    text = re.sub(r"\s+", " ", _caption_safe_korean(text))
    if not text:
        return []
    # 문장부호(마침표/물음표/쉼표 등) 뒤를 우선 분할 지점으로 표시
    marked = re.sub(r"([.!?。…,·])", lambda m: m.group(1) + "\x00", text)
    chunks = [c.strip() for c in marked.split("\x00") if c.strip()]
    units: List[str] = []
    cur = ""
    for ch in chunks:
        if not cur:
            cur = ch
        elif len(cur) + 1 + len(ch) <= unit_len:
            cur += " " + ch
        else:
            units.append(cur)
            cur = ch
        # 한 조각이 너무 길면 어절 경계에서 강제 분할
        while len(cur) > unit_len + 6:
            cut = cur.rfind(" ", 0, unit_len)
            if cut <= 0:
                cut = unit_len
            units.append(cur[:cut].strip())
            cur = cur[cut:].strip()
    if cur:
        units.append(cur)
    return [u for u in units if u]


def _generate_frame_bg(path: Path, img_scale: float = _IMG_SCALE,
                       img_y: float = _IMG_TRANSFORM_Y) -> bool:
    """기본 프레임 배경(어두운 배경 + 이미지 자리 표시 테두리)을 생성."""
    try:
        from PIL import Image, ImageDraw
    except ImportError:
        return False
    img = Image.new("RGB", (_CANVAS_W, _CANVAS_H), _BG_COLOR)
    draw = ImageDraw.Draw(img)
    # 이미지가 놓이는 영역(스케일 반영)을 안내선으로 표시
    half = img_scale / 2.0
    shift = int(img_y * (_CANVAS_H / 2))
    x0 = int((0.5 - half) * _CANVAS_W)
    x1 = int((0.5 + half) * _CANVAS_W)
    y0 = int((0.5 - half) * _CANVAS_H) - shift
    y1 = int((0.5 + half) * _CANVAS_H) - shift
    try:
        draw.rounded_rectangle([x0, y0, x1, y1], radius=36,
                               outline=(70, 72, 84), width=6)
    except AttributeError:
        draw.rectangle([x0, y0, x1, y1], outline=(70, 72, 84), width=6)
    try:
        img.save(str(path), "PNG")
        return True
    except OSError:
        return False


def _load_kr_font(size: int):
    from PIL import ImageFont
    for fp in _KR_FONTS:
        if Path(fp).is_file():
            try:
                return ImageFont.truetype(fp, size)
            except OSError:
                continue
    return ImageFont.load_default()


def _generate_cut_image(path: Path, index: int) -> bool:
    """밝은 카드 중앙에 '이미지{n}' 이 새겨진 9:16 PNG(컷 자리표시자)를 만듭니다.

    이 카드는 화면 전체가 아니라 프레임 안쪽에 축소 배치되어, 소스처럼
    테두리(배경 프레임)가 보이도록 합니다(내보내기 시 스케일 적용).
    """
    try:
        from PIL import Image, ImageDraw
    except ImportError:
        return False
    img = Image.new("RGB", (_CANVAS_W, _CANVAS_H), _CARD_FILL)
    draw = ImageDraw.Draw(img)
    draw.rectangle([0, 0, _CANVAS_W - 1, _CANVAS_H - 1],
                   outline=_CARD_BORDER, width=10)
    label = f"이미지{index}"
    font = _load_kr_font(200)
    bbox = draw.textbbox((0, 0), label, font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    draw.text(((_CANVAS_W - tw) / 2 - bbox[0], (_CANVAS_H - th) / 2 - bbox[1]),
              label, fill=(60, 60, 70), font=font)
    try:
        img.save(str(path), "PNG")
        return True
    except OSError:
        return False


def _make_text_content(template_mat: Dict, text: str,
                       max_size: float = _MAX_FONT_SIZE) -> str:
    """템플릿 텍스트 소재의 스타일(폰트/색/외곽선)을 유지한 채 문구만 교체.

    폰트 크기는 넘침 방지를 위해 상한을 두고, range 는 (개행 포함) 전체 길이로 맞춥니다.
    """
    font_ref = ({"path": _CAPTION_FONT_PATH.replace("\\", "/"), "id": ""}
                if _CAPTION_FONT_PATH else None)
    raw = template_mat.get("content", "")
    try:
        obj = json.loads(raw)
        obj["text"] = text
        for st in obj.get("styles", []):
            st["range"] = [0, len(text)]
            st["size"] = max_size
            if font_ref:
                st["font"] = dict(font_ref)
        return json.dumps(obj, ensure_ascii=False)
    except (ValueError, TypeError):
        style: Dict = {
            "fill": {"content": {"render_type": "solid",
                                 "solid": {"color": [1, 1, 1]}}},
            "strokes": [{"content": {"render_type": "solid",
                                     "solid": {"color": [0, 0, 0]}},
                         "width": 0.08, "mode": 0}],
            "size": max_size,
            "range": [0, len(text)],
        }
        if font_ref:
            style["font"] = dict(font_ref)
        return json.dumps({"text": text, "styles": [style]}, ensure_ascii=False)


def _apply_caption_font(material: Dict) -> None:
    """텍스트 소재에 배민 도현체 및 공통 필드를 적용."""
    material["words"] = {"start_time": [], "end_time": [], "text": []}
    material["current_words"] = {"start_time": [], "end_time": [], "text": []}
    material["alignment"] = 1  # 가운데 정렬
    if _CAPTION_FONT_PATH:
        material["font_path"] = _CAPTION_FONT_PATH.replace("\\", "/")
        material["font_name"] = _CAPTION_FONT_NAME
        material["font_title"] = _CAPTION_FONT_NAME
        material["font_id"] = ""
        material["font_resource_id"] = ""
        material["font_url"] = ""


def _build_text_segment(text_tpl: Dict, text: str, start_us: int, dur_us: int,
                        y: float, font_size: float, wrap_chars: int,
                        render_index: int):
    """자막/제목용 텍스트 세그먼트 1개를 생성(세그먼트, 복제소재)."""
    text = _caption_safe_korean(text)
    seg, cloned, main = _clone_seg(text_tpl["seg"], text_tpl["materials"])
    if main is not None:
        main["content"] = _make_text_content(
            main, _wrap_korean(text, wrap_chars), font_size)
        _apply_caption_font(main)
    seg["target_timerange"] = {"start": start_us, "duration": dur_us}
    seg["source_timerange"] = None
    seg["clip"] = _clip(1.0, y)
    seg["render_index"] = render_index
    return seg, cloned


def _build_photo_segment(image_tpl: Dict, img_path: Path, start_us: int,
                         dur_us: int, scale: float, y: float,
                         render_index: int, media_kind: str = "image"):
    """컷/배경 이미지용 photo 세그먼트 1개를 생성(세그먼트, 복제소재)."""
    seg, cloned, main = _clone_seg(image_tpl["seg"], image_tpl["materials"])
    if main is not None:
        source_width, source_height = _asset_dimensions(img_path, media_kind)
        main["type"] = "video" if media_kind == "video" else "photo"
        main["path"] = str(img_path).replace("\\", "/")
        main["width"] = source_width
        main["height"] = source_height
        main["duration"] = dur_us if media_kind == "video" else _PHOTO_DURATION
        main["has_audio"] = False
        main["material_name"] = img_path.name
        for pk in ("media_path", "reverse_path", "intensifies_path",
                   "reverse_intensifies_path", "intensifies_audio_path",
                   "cartoon_path", "live_photo_cover_path"):
            if pk in main:
                main[pk] = ""
        if isinstance(main.get("matting"), dict):
            main["matting"]["path"] = ""
        main["crop"] = {
            "upper_left_x": 0.0, "upper_left_y": 0.0,
            "upper_right_x": 1.0, "upper_right_y": 0.0,
            "lower_left_x": 0.0, "lower_left_y": 1.0,
            "lower_right_x": 1.0, "lower_right_y": 1.0,
        }
        main["crop_ratio"] = "free"
        main["crop_scale"] = 1.0
    seg["target_timerange"] = {"start": start_us, "duration": dur_us}
    seg["source_timerange"] = {"start": 0, "duration": dur_us}
    seg["clip"] = _clip(scale, y)
    seg["render_index"] = render_index
    return seg, cloned


def _safe_folder(name: str) -> str:
    name = re.sub(r'[\\/:*?"<>|\r\n\t]+', " ", name or "").strip()
    name = re.sub(r"\s+", " ", name)
    return name[:60] or "shortly 대본"


def _pick(items: Optional[List], c: int) -> str:
    """리스트에서 c번째(범위 밖이면 마지막) 값을 안전하게 꺼낸다."""
    if not items:
        return ""
    return str(items[c] if c < len(items) else items[-1]).strip()


def _collect_anim_catalog(root: Path, max_projects: int = 40) -> List[Dict]:
    """설치된 CapCut 프로젝트들의 material_animations(애니메이션 소재)를 수집.

    반환: [{"names": set[str], "mat": 소재dict}] — names 에 실제 애니메이션 이름
    (예: "페이드 인", "확대")이 담긴다. 캐시 리소스를 참조하는 소재를 그대로 복제해
    세그먼트에 붙일 수 있다.
    """
    catalog: List[Dict] = []
    try:
        projs = [d for d in root.iterdir()
                 if d.is_dir() and (d / "draft_content.json").is_file()]
    except OSError:
        return catalog
    projs.sort(key=lambda d: d.stat().st_mtime, reverse=True)
    for d in projs[:max_projects]:
        try:
            dc = _read_json(d / "draft_content.json")
        except (OSError, ValueError):
            continue
        anims = (dc.get("materials") or {}).get("material_animations")
        if not isinstance(anims, list):
            continue
        for a in anims:
            if not isinstance(a, dict):
                continue
            sub = a.get("animations")
            if not isinstance(sub, list) or not sub:
                continue
            names = {str(one.get("name", "")).strip()
                     for one in sub if isinstance(one, dict) and one.get("name")}
            names = {n for n in names if n}
            if names:
                catalog.append({"names": names, "mat": a})
    return catalog


def _match_anim(catalog: List[Dict], effect: str) -> Optional[Dict]:
    """추천 효과명(예: '줌 인')에 해당하는 애니메이션 소재를 찾아 복제 반환.

    개념이 다른 효과(줌 vs 페이드)가 '인/아웃' 글자를 공유해 오매칭되는 것을 막기 위해
    유사도(difflib)가 아니라 '핵심 키워드 구절의 부분일치'로만 매칭한다. 없으면 None.
    """
    effect = (effect or "").strip()
    if not effect or effect in ("없음", "none", "None"):
        return None
    # 추천 효과 -> 캡컷 애니메이션 이름에 포함되어야 할 키워드 구절(길이 긴 것 우선)
    keyword_map = {
        "줌 인": ["확대", "줌 인", "줌인", "zoom in"],
        "줌 아웃": ["축소", "줌 아웃", "줌아웃", "zoom out"],
        "페이드 인": ["페이드 인", "페이드인", "fade in"],
        "페이드 아웃": ["페이드 아웃", "페이드아웃", "fade out"],
    }
    keywords = keyword_map.get(effect, [effect])
    keywords = sorted({k.lower() for k in keywords if k}, key=len, reverse=True)

    for entry in catalog:
        for nm in entry["names"]:
            nm_l = nm.lower()
            for kw in keywords:
                # 이름에 키워드 구절이 통째로 포함될 때만 매칭(부분 글자 공유 방지)
                if kw in nm_l:
                    clone = copy.deepcopy(entry["mat"])
                    clone["id"] = _new_id()
                    return clone
    return None


def export_to_capcut_project(guide: Dict,
                             root_override: Optional[str] = None,
                             layout: Optional[Dict] = None,
                             cut_assets: Optional[Dict[str, Dict]] = None) -> Dict:
    """가이드를 이 PC 의 CapCut 프로젝트 폴더로 내보내(초안 생성) 결과를 반환.

    layout: 학습된 채널의 화면 레이아웃(제목/이미지/자막 영역, 정규화 좌표).
            제공되면 제목/자막/이미지 위치·크기를 소스에 맞춰 배치합니다.
    """
    lp = _layout_params(layout)
    cut_assets = cut_assets or {}
    root = find_capcut_draft_root(root_override)
    if root is None:
        return {"ok": False,
                "reason": "이 PC 에서 CapCut 프로젝트 폴더를 찾지 못했습니다. "
                          "CapCut 을 한 번 실행한 뒤 다시 시도하세요."}

    base, text_tpl, image_tpl = _pick_templates(root)
    if base is None or text_tpl is None:
        return {"ok": False,
                "reason": "기존 CapCut 프로젝트(자막 포함)를 찾지 못했습니다. "
                          "CapCut 에서 자막이 있는 프로젝트를 한 번 만든 뒤 다시 시도하세요."}

    segments = [s for s in (guide.get("segments") or [])
                if (s.get("narration") or s.get("caption") or "").strip()]
    if not segments:
        return {"ok": False, "reason": "내보낼 자막(세그먼트)이 없습니다."}

    title = (guide.get("title") or "shortly 대본").strip() or "shortly 대본"
    folder_name = _safe_folder(title)
    proj_dir = root / folder_name
    if proj_dir.exists():
        folder_name = f"{folder_name}_{time.strftime('%m%d_%H%M%S')}"
        proj_dir = root / folder_name
    proj_dir.mkdir(parents=True, exist_ok=True)
    cuts_dir = proj_dir / "shortly_cuts"
    cuts_dir.mkdir(parents=True, exist_ok=True)

    now_s = int(time.time())
    now_us = int(time.time() * _US)
    draft_id = _new_id()
    fold_path = str(proj_dir).replace("\\", "/")
    root_path = str(root).replace("\\", "/")

    # --- draft_content.json (타임라인 본체) ---
    dc = copy.deepcopy(base["content"])
    mats = dc.setdefault("materials", {})
    for k, v in list(mats.items()):
        if isinstance(v, list):
            mats[k] = []

    # 세그먼트 경계(마이크로초) 및 총 길이 계산
    seg_bounds: List[tuple] = []
    for s in segments:
        st = int(round(float(s.get("start_sec", 0)) * _US))
        du = max(400_000, int(round(float(s.get("duration_sec", 2)) * _US)))
        seg_bounds.append((st, du))
    total_us = int(round(float(guide.get("total_duration_sec", 0)) * _US))
    if total_us <= 0:
        total_us = seg_bounds[-1][0] + seg_bounds[-1][1]

    bg_segs: List[Dict] = []
    cut_segs: List[Dict] = []
    reco_segs: List[Dict] = []
    caption_segs: List[Dict] = []
    title_segs: List[Dict] = []
    images_ok = 0
    cut_no = 0
    anim_attached = 0
    # 컷별 추천 효과를 실제 캡컷 애니메이션으로 붙이기 위한 카탈로그(가능 시)
    anim_catalog = _collect_anim_catalog(root)

    # (1) 기본 프레임 배경 — 영상 전체 지속(화면을 이미지로만 채우지 않도록)
    if image_tpl is not None:
        bg_path = cuts_dir / "frame_bg.png"
        if _generate_frame_bg(bg_path, lp["img_scale"], lp["img_y"]):
            images_ok += 1
        bseg, cloned_bg = _build_photo_segment(
            image_tpl, bg_path, 0, total_us, 1.0, 0.0, render_index=0)
        _merge_materials(mats, cloned_bg)
        bg_segs.append(bseg)

    # (2) 상단 고정 제목 — 영상 전체 지속(소스처럼 제목을 항상 노출)
    title_text = (guide.get("hook") or guide.get("title") or "").strip()
    if title_text:
        tiseg, cloned_ti = _build_text_segment(
            text_tpl, title_text, 0, total_us, lp["title_y"],
            _TITLE_FONT_SIZE, _TITLE_MAX_CHARS, render_index=16000)
        _merge_materials(mats, cloned_ti)
        title_segs.append(tiseg)

    # (3) 세그먼트별: 컷 이미지 + 한 줄씩 바뀌는 자막
    cap_idx = 0
    for i, s in enumerate(segments):
        seg_start, seg_dur = seg_bounds[i]
        narration = (s.get("narration") or s.get("caption") or "").strip()

        # 자막: 짧은 단위로 쪼개 소스처럼 한 줄씩 순차 노출
        units = _split_display_units(narration)
        if units:
            total_len = sum(len(u) for u in units) or 1
            acc = 0
            for u in units:
                u_start = seg_start + int(round(seg_dur * acc / total_len))
                acc += len(u)
                u_end = seg_start + int(round(seg_dur * acc / total_len))
                u_dur = max(300_000, u_end - u_start)
                cseg, cloned_c = _build_text_segment(
                    text_tpl, u, u_start, u_dur, lp["caption_y"],
                    _MAX_FONT_SIZE, _CAPTION_MAX_CHARS,
                    render_index=15000 + cap_idx)
                cap_idx += 1
                _merge_materials(mats, cloned_c)
                caption_segs.append(cseg)

        # 컷 이미지: cuts_suggested 만큼 균등 분할, 프레임 안쪽에 축소 배치
        if image_tpl is not None:
            n_cuts = max(1, min(int(s.get("cuts_suggested") or 1), 6))
            img_recos = s.get("image_suggestions") or []
            media_recos = s.get("media_suggestions") or []
            effect_recos = s.get("effect_suggestions") or []
            for c in range(n_cuts):
                cut_no += 1
                c_start = seg_start + int(round(c * seg_dur / n_cuts))
                c_end = seg_start + int(round((c + 1) * seg_dur / n_cuts))
                c_dur = max(200_000, c_end - c_start)
                cut_id = f"s{int(s.get('index', i)):03d}-c{c:02d}"
                selected = cut_assets.get(cut_id) or {}
                selected_path = Path(str(selected.get("path") or ""))
                media_kind = str(selected.get("media_kind") or "image")
                if selected_path.is_file():
                    suffix = selected_path.suffix.lower() or (
                        ".mp4" if media_kind == "video" else ".png"
                    )
                    img_path = cuts_dir / f"cut_{cut_no:02d}{suffix}"
                    shutil.copy2(selected_path, img_path)
                    images_ok += 1
                else:
                    img_path = cuts_dir / f"cut_{cut_no:02d}.png"
                    media_kind = "image"
                    if _generate_cut_image(img_path, cut_no):
                        images_ok += 1
                vseg, cloned_v = _build_photo_segment(
                    image_tpl, img_path, c_start, c_dur, lp["img_scale"],
                    lp["img_y"], render_index=100 + cut_no,
                    media_kind=media_kind)
                # 추천 효과를 실제 캡컷 애니메이션으로 부착(가능 시)
                effect = _pick(effect_recos, c)
                anim = _match_anim(anim_catalog, effect) if anim_catalog else None
                if anim is not None:
                    if not isinstance(mats.get("material_animations"), list):
                        mats["material_animations"] = []
                    mats["material_animations"].append(anim)
                    vseg.setdefault("extra_material_refs", [])
                    vseg["extra_material_refs"].append(anim["id"])
                    anim_attached += 1
                _merge_materials(mats, cloned_v)
                cut_segs.append(vseg)

                # 컷 카드 아래에 편집용 추천 메모(이미지+매체+효과) 표시
                img_txt = _pick(img_recos, c)
                media_txt = _pick(media_recos, c)
                if img_txt or effect:
                    parts = []
                    if img_txt:
                        parts.append(f"추천: {img_txt}"
                                     + (f" ({media_txt})" if media_txt else ""))
                    if effect and effect != "없음":
                        parts.append(f"효과: {effect}")
                    memo = " / ".join(parts)
                    reco_y = max(-0.93, lp["img_y"] - lp["img_scale"] - _RECO_Y_GAP)
                    rseg, cloned_r = _build_text_segment(
                        text_tpl, memo, c_start, c_dur, reco_y,
                        _RECO_FONT_SIZE, _RECO_MAX_CHARS,
                        render_index=14000 + cut_no)
                    _merge_materials(mats, cloned_r)
                    reco_segs.append(rseg)

    # 트랙 순서: 배경 → 컷이미지 → 추천메모 → 자막 → 제목 (뒤 트랙이 위에 렌더됨)
    def _track(ttype: str, segs: List[Dict]) -> Dict:
        return {"id": _new_id(), "type": ttype, "segments": segs,
                "flag": 0, "attribute": 0, "name": "", "is_default_name": True}

    tracks: List[Dict] = []
    if bg_segs:
        tracks.append(_track("video", bg_segs))
    if cut_segs:
        tracks.append(_track("video", cut_segs))
    if reco_segs:
        tracks.append(_track("text", reco_segs))
    if caption_segs:
        tracks.append(_track("text", caption_segs))
    if title_segs:
        tracks.append(_track("text", title_segs))
    dc["tracks"] = tracks

    dc["id"] = draft_id
    dc["name"] = title
    dc["duration"] = total_us
    dc["create_time"] = now_s
    dc["update_time"] = now_s
    dc["canvas_config"] = {"ratio": "9:16", "width": _CANVAS_W,
                           "height": _CANVAS_H, "background": None}
    dc["cover"] = None
    dc["retouch_cover"] = None
    dc["group_container"] = None
    if isinstance(dc.get("relationships"), list):
        dc["relationships"] = []
    if isinstance(dc.get("keyframe_graph_list"), list):
        dc["keyframe_graph_list"] = []
    kf = dc.get("keyframes")
    if isinstance(kf, dict):
        for k, v in kf.items():
            if isinstance(v, list):
                kf[k] = []

    write_json(proj_dir / "draft_content.json", dc)

    # --- draft_meta_info.json ---
    meta = copy.deepcopy(base["meta"]) if isinstance(base["meta"], dict) else {}
    meta.update({
        "draft_id": draft_id,
        "draft_name": title,
        "draft_fold_path": fold_path,
        "draft_root_path": root_path,
        "draft_cover": "",
        "draft_materials": [],
        "draft_materials_copied_info": [],
        "draft_segment_extra_info": [],
        "draft_timeline_materials_size_": 0,
        "draft_need_rename_folder": False,
        "tm_draft_create": now_us,
        "tm_draft_modified": now_us,
        "tm_draft_removed": 0,
        "tm_duration": total_us,
    })
    write_json(proj_dir / "draft_meta_info.json", meta)

    # --- root_meta_info.json 에 프로젝트 등록(목록에 표시되도록) ---
    registered = _register_in_root(root, meta, draft_id, fold_path,
                                   root_path, title, now_us, total_us)

    return {
        "ok": True,
        "project_name": folder_name,
        "path": str(proj_dir),
        "root": str(root),
        "segments": len(caption_segs),
        "cuts": len(cut_segs),
        "images": images_ok,
        "reco_memos": len(reco_segs),
        "anim_attached": anim_attached,
        "has_title": bool(title_segs),
        "registered": registered,
    }


def _register_in_root(root: Path, meta: Dict, draft_id: str, fold_path: str,
                       root_path: str, title: str, now_us: int,
                       total_us: int) -> bool:
    """root_meta_info.json 의 프로젝트 목록에 새 드래프트를 추가."""
    rmi_path = root / "root_meta_info.json"
    try:
        rmi = _read_json(rmi_path)
    except (OSError, ValueError):
        rmi = {"all_draft_store": [], "draft_ids": [], "root_path": root_path}

    store = rmi.get("all_draft_store")
    if not isinstance(store, list):
        store = []
        rmi["all_draft_store"] = store

    entry = copy.deepcopy(store[0]) if store else {}
    entry.update({
        "draft_cover": "",
        "draft_fold_path": fold_path,
        "draft_id": draft_id,
        "draft_name": title,
        "draft_root_path": root_path,
        "tm_draft_create": now_us,
        "tm_draft_modified": now_us,
        "tm_draft_removed": 0,
        "tm_duration": total_us,
    })
    if "draft_json_file" in entry:
        entry["draft_json_file"] = ""
    store.insert(0, entry)

    ids = rmi.get("draft_ids")
    if isinstance(ids, list):
        ids.insert(0, draft_id)

    try:
        write_json(rmi_path, rmi)
        return True
    except OSError:
        return False


# =====================================================================
# 템플릿(기존 프로젝트) 복사 기반 내보내기 + TTS(Typecast) 생성
# =====================================================================

_TTS_GAP_US = 90_000        # 자막(TTS) 사이 간격
_CAP_UNDER_TITLE = 0.14     # 제목 대비 자막을 아래로 내리는 정도(transform y)
_TITLE_PLACEHOLDERS = ("제목이 들어갑니다", "제목을 입력하세요", "제목 입력")
# 사용자가 템플릿에 넣어둔 '자막 예시' 세그먼트를 식별하는 표식(위치/크기 복제용)
_CAPTION_SAMPLE_MARKERS = ("자막", "대사", "caption", "subtitle")
_INTRO_DEFAULT_US = 1_000_000  # blank_10 인트로를 못 찾을 때 기본 인트로 길이


def _tts_cache_path(cfg, cache_dir: Path, text: str,
                    prefix: str = "tts") -> Path:
    """TTS 설정+문구 기반 해시 파일명. 같은 내용이면 같은 경로 → 재사용/캐시."""
    key = "|".join(str(x) for x in (
        getattr(cfg, "voice_id", ""), getattr(cfg, "model", ""),
        getattr(cfg, "language", ""), getattr(cfg, "emotion_preset", ""),
        getattr(cfg, "emotion_intensity", ""), getattr(cfg, "tempo", ""),
        text))
    h = hashlib.sha1(key.encode("utf-8")).hexdigest()[:12]
    return cache_dir / f"{prefix}_{h}.wav"


def _title_for_tts(text: str) -> str:
    """제목 TTS 문구: 끝의 공백/문장부호를 없애고 '?'를 붙여 끝음을 올린다."""
    t = re.sub(r"[\s?!.…·,]+$", "", (text or "").strip())
    return (t + "?") if t else ""


def _blank_intro_end(dc: Dict) -> int:
    """제목 표시 구간(blank_1080 이미지가 깔린 인트로)의 끝 시각(us)."""
    mats = dc.get("materials") or {}
    idx = _index_materials(mats)
    end = 0
    for t in dc.get("tracks", []):
        for seg in t.get("segments", []):
            info = idx.get(seg.get("material_id"))
            if not info:
                continue
            path = str(info[1].get("path") or "").lower()
            if "blank_1080" in path or "blank_10" in path:
                tr = seg.get("target_timerange") or {}
                end = max(end, tr.get("start", 0) + tr.get("duration", 0))
    return end


def _extend_intro_segments(dc: Dict, intro_template_end: int,
                           intro_dur: int, tol: int = 250_000) -> None:
    """인트로(제목 표시) 구간 세그먼트들을 새 인트로 길이에 맞춰 확장."""
    if intro_template_end <= 0 or intro_dur <= 0:
        return
    for t in dc.get("tracks", []):
        for seg in t.get("segments", []):
            tr = seg.get("target_timerange") or {}
            if tr.get("start", 0) <= tol and \
               abs(tr.get("duration", 0) - intro_template_end) <= tol:
                tr["start"] = 0
                tr["duration"] = intro_dur
                src = seg.get("source_timerange")
                if isinstance(src, dict):
                    src["start"] = 0
                    src["duration"] = intro_dur


def _find_caption_sample(dc: Dict):
    """템플릿에 넣어둔 '자막 예시' 텍스트 세그먼트를 찾는다.

    반환: {"seg", "track", "materials"} 또는 None.
    제목 자리표시자는 제외한다.
    """
    mats = dc.get("materials") or {}
    idx = _index_materials(mats)
    for t in dc.get("tracks", []):
        if t.get("type") != "text":
            continue
        for seg in t.get("segments", []):
            info = idx.get(seg.get("material_id"))
            if not info:
                continue
            txt = _text_of(info[1])
            if any(ph in txt for ph in _TITLE_PLACEHOLDERS):
                continue
            low = txt.lower()
            if any(mk in low for mk in _CAPTION_SAMPLE_MARKERS):
                return {"seg": seg, "track": t, "materials": mats}
    return None


def _find_template_project(root: Path, name: str) -> Optional[Path]:
    """폴더명 또는 draft_name 이 name 과 일치하는 프로젝트 폴더를 찾습니다."""
    cand = root / name
    if cand.is_dir() and (cand / "draft_content.json").is_file():
        return cand
    for d in root.iterdir():
        if not d.is_dir() or not (d / "draft_content.json").is_file():
            continue
        if d.name == name:
            return d
        try:
            meta = _read_json(d / "draft_meta_info.json")
            if isinstance(meta, dict) and (meta.get("draft_name") or "") == name:
                return d
        except (OSError, ValueError):
            continue
    return None


def _find_audio_template(root: Path, exclude: Optional[str] = None):
    """다른 프로젝트에서 오디오 세그먼트+소재 템플릿을 찾아 복제용으로 반환."""
    try:
        projs = [d for d in root.iterdir()
                 if d.is_dir() and (d / "draft_content.json").is_file()]
    except OSError:
        return None
    projs.sort(key=lambda d: d.stat().st_mtime, reverse=True)
    for d in projs:
        if exclude and d.name == exclude:
            continue
        try:
            dc = _read_json(d / "draft_content.json")
        except (OSError, ValueError):
            continue
        mats = dc.get("materials") or {}
        idx = _index_materials(mats)
        for t in dc.get("tracks", []):
            if t.get("type") not in ("audio", "music") or not t.get("segments"):
                continue
            audio_types = ("extract_music", "audio", "music", "sound", "record")
            for seg in t["segments"]:
                info = idx.get(seg.get("material_id"))
                if info and info[1].get("type") in audio_types:
                    return {"seg": seg, "materials": mats, "project": d.name}
            return {"seg": t["segments"][0], "materials": mats, "project": d.name}
    return None


def _set_text_keep_style(main_mat: Dict, text: str) -> None:
    """텍스트 소재의 폰트/색/외곽선 스타일은 유지하고 문구만 교체."""
    raw = main_mat.get("content", "")
    try:
        obj = json.loads(raw)
    except (ValueError, TypeError):
        return
    obj["text"] = text
    n = len(text)
    for st in obj.get("styles", []):
        st["range"] = [0, n]
    main_mat["content"] = json.dumps(obj, ensure_ascii=False)
    # 자동 자막 단어 타이밍 잔재 제거(있으면)
    for key in ("words", "current_words"):
        if isinstance(main_mat.get(key), dict):
            main_mat[key] = {"start_time": [], "end_time": [], "text": []}


def _text_of(main_mat: Dict) -> str:
    try:
        return (json.loads(main_mat.get("content", "")).get("text") or "").strip()
    except (ValueError, TypeError):
        return ""


def _find_title_style_seg(dc: Dict):
    """제목 자리표시자 텍스트 세그먼트(스타일 원본)와 세로 위치를 찾습니다."""
    mats = dc.get("materials") or {}
    idx = _index_materials(mats)
    best = None
    best_dur = -1
    for t in dc.get("tracks", []):
        if t.get("type") != "text":
            continue
        for seg in t.get("segments", []):
            info = idx.get(seg.get("material_id"))
            if not info:
                continue
            txt = _text_of(info[1])
            dur = (seg.get("target_timerange") or {}).get("duration", 0)
            is_ph = any(ph in txt for ph in _TITLE_PLACEHOLDERS)
            score = dur + (10 ** 12 if is_ph else 0)
            if score > best_dur:
                best_dur = score
                best = seg
    if best is None:
        for t in dc.get("tracks", []):
            if t.get("type") == "text" and t.get("segments"):
                best = t["segments"][0]
                break
    title_y = 0.71
    if best is not None:
        try:
            title_y = float(((best.get("clip") or {}).get("transform") or {})
                            .get("y", title_y))
        except (TypeError, ValueError):
            pass
    return best, title_y


def _fill_intro_title_card(dc: Dict, title_text: str, intro_end: int,
                           tol: int = 250_000) -> int:
    """영상 시작부 '인트로 제목 카드' 슬롯을 제목으로 채운다.

    업데이트된 템플릿은 시작(약 1초)에 제목이 한 번 더 크게 뜨는 제목 카드가
    있다. 재편집 예시('억지로 유행시킨 버터떡')처럼:
      - 세로 위치가 높은 두 슬롯: 제목을 두 줄로 나눠(위=1줄, 아래=2줄) 배치.
      - 그 아래 슬롯: 전체 제목 한 줄.
    인트로 전용(시작≈0, 짧은 길이) 텍스트 세그먼트만 대상으로 하며, 자막 예시나
    영상 전체 길이 세그먼트(채널바/제목/푸터)는 제외한다.

    반환: 채운 슬롯 수. 이 함수가 채운 슬롯은 자리표시자 문구가 사라지므로
    이후 _fill_titles 가 다시 덮어쓰지 않는다.
    """
    if not title_text:
        return 0
    mats = dc.get("materials") or {}
    idx = _index_materials(mats)
    max_dur = max(intro_end, _INTRO_DEFAULT_US) + 1_500_000
    slots: List[Tuple[float, Dict]] = []
    for t in dc.get("tracks", []):
        if t.get("type") != "text":
            continue
        for seg in t.get("segments", []):
            tr = seg.get("target_timerange") or {}
            start = tr.get("start", 0)
            dur = tr.get("duration", 0)
            if start > tol or dur <= 0 or dur > max_dur:
                continue
            info = idx.get(seg.get("material_id"))
            if not info:
                continue
            main = info[1]
            low = _text_of(main).lower()
            if any(mk in low for mk in _CAPTION_SAMPLE_MARKERS):
                continue  # 자막 예시 제외
            y = 0.0
            try:
                y = float(((seg.get("clip") or {}).get("transform") or {})
                          .get("y", 0.0))
            except (TypeError, ValueError):
                pass
            slots.append((y, main))
    if not slots:
        return 0
    slots.sort(key=lambda s: s[0], reverse=True)  # 화면 위(y 큰) 순
    n = 0
    if len(slots) >= 2:
        line1, line2 = _split_two_lines(title_text)
        _set_text_keep_style(slots[0][1], line1)
        _set_text_keep_style(slots[1][1], line2)
        n += 2
        for _, m in slots[2:]:  # 남은(아래) 슬롯: 전체 제목 한 줄
            _set_text_keep_style(m, title_text)
            n += 1
    else:
        _set_text_keep_style(slots[0][1], title_text)
        n += 1
    return n


def _fill_titles(dc: Dict, title_text: str) -> int:
    """자리표시자('제목이 들어갑니다' 등) 텍스트 소재를 실제 제목으로 채웁니다."""
    n = 0
    wrapped = _wrap_korean(title_text, _TITLE_MAX_CHARS)
    for m in (dc.get("materials") or {}).get("texts", []) or []:
        if any(ph in _text_of(m) for ph in _TITLE_PLACEHOLDERS):
            _set_text_keep_style(m, wrapped)
            n += 1
    return n


def _extend_full_segments(dc: Dict, template_dur: int, total_us: int) -> None:
    """템플릿 전체 길이(≈template_dur)만큼 이어지던 세그먼트를 새 총길이로 확장."""
    if template_dur <= 0:
        return
    tol = 250_000
    for t in dc.get("tracks", []):
        for seg in t.get("segments", []):
            tr = seg.get("target_timerange") or {}
            dur = tr.get("duration", 0)
            start = tr.get("start", 0)
            if start <= tol and abs(dur - template_dur) <= tol:
                tr["start"] = 0
                tr["duration"] = total_us
                src = seg.get("source_timerange")
                if isinstance(src, dict):
                    src["start"] = 0
                    src["duration"] = total_us


_QUOTE_PAIRS = {
    "'": "'",
    '"': '"',
    "‘": "’",
    "“": "”",
}

_HANJA_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")
_HANJA_WORD_TO_HANGUL = {
    "日本": "일본",
    "美國": "미국",
    "中國": "중국",
    "韓國": "한국",
    "英國": "영국",
    "獨逸": "독일",
    "佛蘭西": "프랑스",
}
_HANJA_TO_HANGUL = str.maketrans({
    "日": "일본",
    "月": "월",
    "年": "년",
    "人": "인",
    "大": "대",
    "小": "소",
    "中": "중국",
    "韓": "한국",
    "美": "미국",
    "英": "영국",
    "獨": "독일",
    "佛": "프랑스",
    "北": "북",
    "南": "남",
    "東": "동",
    "西": "서",
    "前": "전",
    "後": "후",
    "上": "상",
    "下": "하",
    "內": "내",
    "外": "외",
    "新": "신",
    "舊": "구",
    "有": "유",
    "無": "무",
    "第": "제",
    "次": "차",
    "社": "사",
    "國": "국",
    "對": "대",
    "亞": "아",
})


def _caption_safe_korean(text: str) -> str:
    """지원하지 않는 한자를 자막과 TTS에 남기지 않는다.

    자주 쓰는 약칭은 한글 독음으로 바꾸고, 알 수 없는 한자는 글리프가 깨지는
    것보다 누락이 안전하므로 제거한다. 새 대본에는 생성 프롬프트에서도 한자
    사용을 금지하지만, 기존 저장 대본을 내보낼 때도 이 경계를 적용한다.
    """

    source = text or ""
    for hanja, hangul in _HANJA_WORD_TO_HANGUL.items():
        source = source.replace(hanja, hangul)
    translated = source.translate(_HANJA_TO_HANGUL)
    unknown_count = len(_HANJA_RE.findall(translated))
    if unknown_count:
        log.warning("자막에서 독음을 알 수 없는 한자 %d자를 제거했습니다.", unknown_count)
        translated = _HANJA_RE.sub("", translated)
    return re.sub(r"[ \t]+", " ", translated).strip()


def _split_sentences(text: str) -> List[str]:
    """나레이션을 TTS 자연 발화 단위(문장/절)로 분할.

    문장 종결부호에서만 끊고 쉼표는 같은 발화로 유지한다. 따옴표 안의 물음표나
    느낌표는 분리하지 않아 인용문 전체를 한 자막 화면 안에서 계획할 수 있게 한다.
    """
    text = re.sub(r"\s+", " ", (text or "").strip())
    if not text:
        return []
    parts: List[str] = []
    start = 0
    quote_end: Optional[str] = None
    for index, char in enumerate(text):
        if quote_end is not None:
            if char == quote_end:
                quote_end = None
            continue
        if char in _QUOTE_PAIRS:
            quote_end = _QUOTE_PAIRS[char]
            continue
        if char in ".!?…。":
            part = text[start:index + 1].strip()
            if part:
                parts.append(part)
            start = index + 1
    tail = text[start:].strip()
    if tail:
        parts.append(tail)
    return parts or [text]


def _split_caption_clauses(text: str) -> List[str]:
    """따옴표 안은 보존하고 쉼표·쌍점 뒤에서 화면용 의미 절을 나눈다."""

    clauses: List[str] = []
    start = 0
    quote_end: Optional[str] = None
    for index, char in enumerate(text):
        if quote_end is not None:
            if char == quote_end:
                quote_end = None
            continue
        if char in _QUOTE_PAIRS:
            quote_end = _QUOTE_PAIRS[char]
            continue
        if char in ",，、;；:":
            clause = text[start:index + 1].strip()
            if clause:
                clauses.append(clause)
            start = index + 1
    tail = text[start:].strip()
    if tail:
        clauses.append(tail)

    # "근데,"처럼 지나치게 짧은 접속 절은 다음 절에 붙여 단독 줄을 막는다.
    merged: List[str] = []
    index = 0
    while index < len(clauses):
        clause = clauses[index]
        if len(clause) <= 6 and index + 1 < len(clauses):
            merged.append(f"{clause} {clauses[index + 1]}".strip())
            index += 2
        else:
            merged.append(clause)
            index += 1
    return merged or [text]


def _balanced_clause_lines(clause: str, hard_limit: int) -> List[str]:
    """절을 최소 줄 수로, 같은 화면에서 읽기 편하게 균형 분할한다."""

    tokens = re.findall(
        r"""(?:"[^"]*"|'[^']*'|“[^”]*”|‘[^’]*’|\S+)""",
        clause,
    )
    if not tokens:
        return []
    if len(clause) <= hard_limit:
        return [clause]

    # (줄 수, 줄 길이 편차, 여백 제곱합, 줄 목록) 기준의 동적 계획.
    # 최소 줄 수를 우선한 뒤 앞줄만 길고 끝에 한두 글자가 남는 분할을 피한다.
    memo: Dict[int, Optional[Tuple[int, int, int, List[str]]]] = {}

    def solve(start: int) -> Optional[Tuple[int, int, int, List[str]]]:
        if start >= len(tokens):
            return (0, 0, 0, [])
        if start in memo:
            return memo[start]
        candidates: List[Tuple[int, int, int, List[str]]] = []
        for end in range(start + 1, len(tokens) + 1):
            line = " ".join(tokens[start:end])
            if len(line) > hard_limit and end > start + 1:
                break
            tail = solve(end)
            if tail is None:
                continue
            lines = [line, *tail[3]]
            lengths = [len(value) for value in lines]
            candidates.append((
                len(lines),
                max(lengths) - min(lengths),
                sum((hard_limit - min(length, hard_limit)) ** 2 for length in lengths),
                lines,
            ))
        memo[start] = min(candidates, key=lambda item: item[:3]) if candidates else None
        return memo[start]

    result = solve(0)
    return result[3] if result else [clause]


def _caption_pages(text: str, line_limit: int = 22) -> List[List[str]]:
    """문장부호와 의미 절을 보존해 한 화면당 최대 두 줄로 묶는다.

    ``line_limit``는 권장 길이이고, 짧은 조사·어미 하나가 다음 줄로 고립되는
    것을 막기 위해 두 글자까지 여유를 허용한다.
    """

    normalized = re.sub(r"\s+", " ", _caption_safe_korean(text))
    if not normalized:
        return []
    lines: List[str] = []
    hard_limit = line_limit + 2
    for clause in _split_caption_clauses(normalized):
        lines.extend(_balanced_clause_lines(clause, hard_limit))
    return [lines[index:index + 2] for index in range(0, len(lines), 2)]


def _plan_sentences(segments: List[Dict], cfg, tts_dir: Path,
                    start_offset: int = 0):
    """문장(절) 단위로 TTS 를 만들고, 각 문장 구간 안에 자막 조각을 분배한다.

    - TTS: 문장/절 단위(자연 발화) → 어색한 끊김/큰 공백 감소.
    - 자막: 의미 단위로 최대 두 줄을 묶고, 첫 줄을 유지한 상태에서 TTS 진행에
      맞춰 두 번째 줄을 추가한다. 따옴표 안 문장은 중간에서 끊지 않는다.
    start_offset: 첫 문장이 시작할 시각(us). 제목(인트로) 이후부터 시작할 때 사용.
    반환: captions(자막 조각), audios(문장 TTS), 총 길이 등.
    """
    sentences: List[Dict] = []
    for i, s in enumerate(segments):
        nar = _caption_safe_korean(
            (s.get("narration") or s.get("caption") or "").strip()
        )
        for sent in _split_sentences(nar):
            sentences.append({"seg": i, "text": sent})

    use_tts = bool(cfg and getattr(cfg, "enabled", False))
    audio_ok = 0
    audio_fail = 0
    audio_reused = 0
    first_err = ""
    cursor = int(start_offset)
    captions: List[Dict] = []
    audios: List[Dict] = []
    for sent in sentences:
        dur = None
        wav = None
        if use_tts:
            try:
                from ..tts import synth_to_file
                wav = _tts_cache_path(cfg, tts_dir, sent["text"])
                existed = wav.is_file() and wav.stat().st_size > 44
                dur = synth_to_file(cfg, sent["text"], wav)  # 있으면 재사용
                audio_ok += 1
                if existed:
                    audio_reused += 1
            except Exception as exc:  # noqa: BLE001 - 개별 실패는 폴백 처리
                wav = None
                dur = None
                audio_fail += 1
                if not first_err:
                    first_err = str(exc)
                log.warning("TTS 실패(%s): %s", sent["text"][:18], exc)
        if dur is None:
            plain = re.sub(r"\s", "", sent["text"])
            dur = max(700_000, int(len(plain) * 135_000))

        pages = _caption_pages(sent["text"]) or [[sent["text"]]]
        total_len = sum(
            sum(len(line) for line in page)
            for page in pages
        ) or 1
        acc = 0
        for page in pages:
            page_len = sum(len(line) for line in page) or 1
            page_start = cursor + int(round(dur * acc / total_len))
            acc += page_len
            page_end = cursor + int(round(dur * acc / total_len))
            page_duration = max(300_000, page_end - page_start)
            if len(page) == 1:
                captions.append(
                    {
                        "seg": sent["seg"],
                        "text": page[0],
                        "start": page_start,
                        "dur": page_duration,
                        "layer": 0,
                    }
                )
                continue
            first, second = page
            captions.append(
                {
                    "seg": sent["seg"],
                    "text": first + "\n",
                    "start": page_start,
                    "dur": page_duration,
                    "layer": 0,
                }
            )
            second_start = page_start + int(
                round(page_duration * len(first) / max(page_len, 1))
            )
            second_start = min(second_start, page_end - 300_000)
            captions.append(
                {
                    "seg": sent["seg"],
                    "text": "\n" + second,
                    "start": second_start,
                    "dur": page_end - second_start,
                    "layer": 1,
                }
            )
        audios.append({"seg": sent["seg"], "text": sent["text"],
                       "start": cursor, "dur": dur, "wav": wav})
        cursor += dur + _TTS_GAP_US

    base = max(int(start_offset), 0)
    total = max(cursor - _TTS_GAP_US, base + 1_000_000) if sentences \
        else max(base, 1_000_000)
    return {"captions": captions, "audios": audios, "total_us": total,
            "audio_ok": audio_ok, "audio_fail": audio_fail,
            "audio_reused": audio_reused, "use_tts": use_tts,
            "first_err": first_err}


def _build_caption_seg(style_seg: Dict, src_mats: Dict, text: str,
                       start_us: int, dur_us: int, y: Optional[float],
                       render_index: int, scale: Optional[float] = None):
    """자막/메모 세그먼트 생성. y 가 None 이면 원본(예시) 세그먼트의 위치를 그대로 사용."""
    seg, cloned, main = _clone_seg(style_seg, src_mats)
    if main is not None:
        _set_text_keep_style(main, text)
    seg["target_timerange"] = {"start": start_us, "duration": dur_us}
    seg["source_timerange"] = None
    if y is not None:
        clip = seg.setdefault("clip", _clip(1.0, y))
        clip.setdefault("transform", {"x": 0.0, "y": y})["y"] = y
        if scale is not None:
            clip["scale"] = {"x": scale, "y": scale}
    elif scale is not None:
        clip = seg.setdefault("clip", _clip(scale, 0.0))
        clip["scale"] = {"x": scale, "y": scale}
    seg["render_index"] = render_index
    seg["visible"] = True
    return seg, cloned


def _build_audio_seg(audio_tpl: Dict, wav_path: Path, start_us: int,
                     dur_us: int):
    seg, cloned, main = _clone_seg(audio_tpl["seg"], audio_tpl["materials"])
    if main is not None:
        main["path"] = str(wav_path).replace("\\", "/")
        main["duration"] = dur_us
        main["name"] = wav_path.stem
        if "material_name" in main:
            main["material_name"] = wav_path.name
        for k in ("intensifies_path", "music_id", "resource_id", "effect_id",
                  "request_id", "tts_task_id"):
            if k in main:
                main[k] = ""
    # 속도 소재가 있으면 1.0 으로(길이는 이미 WAV 에 반영됨)
    for sp in cloned.get("speeds", []) or []:
        sp["speed"] = 1.0
        if "curve_speed" in sp:
            sp["curve_speed"] = None
    seg["target_timerange"] = {"start": start_us, "duration": dur_us}
    seg["source_timerange"] = {"start": 0, "duration": dur_us}
    seg["speed"] = 1.0
    seg["volume"] = 1.0
    if "last_nonzero_volume" in seg:
        seg["last_nonzero_volume"] = 1.0
    seg["is_placeholder"] = False
    seg["visible"] = True
    return seg, cloned


def _write_dc_variants(dir_path: Path, dc: Dict) -> None:
    """폴더 안의 draft_content.json 과 그 백업/임시본(.bak, template-2.tmp)을 갱신."""
    write_json(dir_path / "draft_content.json", dc)
    for name in ("draft_content.json.bak", "template-2.tmp"):
        p = dir_path / name
        if p.is_file():
            try:
                write_json(p, dc)
            except OSError:
                pass


def _reclone_project_ids(proj_dir: Path, dc: Dict, orig_main_id,
                         now_us: int) -> Dict:
    """복사본을 원본과 완전히 독립된 프로젝트로 만들기 위해 내부 id 를 일괄 재생성.

    - dc["id"](=메인 타임라인 id)를 새 값으로 바꾸고 모든 미러/백업에 반영
    - Timelines/<메인id> 폴더를 새 id 로 리네임하고 project.json / timeline_layout
      의 참조를 갱신
    - 오래된 패치 캐시(attachment/patch)를 제거해 옛 내용으로 되돌아가지 않게 함
    반환: {"main_id", "tlset_id", "project_id"}
    """
    new_main = _new_id()
    new_tlset = _new_id()
    new_project = _new_id()
    dc["id"] = new_main

    # (1) 최상위 draft_content 계열 갱신
    _write_dc_variants(proj_dir, dc)

    tl_dir = proj_dir / "Timelines"
    if tl_dir.is_dir():
        # (2) 메인 타임라인 폴더(내용 id == orig_main_id) 갱신 → 리네임 → 캐시 제거
        target = None
        for sub in tl_dir.iterdir():
            dcf = sub / "draft_content.json"
            if sub.is_dir() and dcf.is_file():
                try:
                    if (_read_json(dcf) or {}).get("id") == orig_main_id:
                        target = sub
                        break
                except (OSError, ValueError):
                    continue
        if target is not None:
            _write_dc_variants(target, dc)
            patch = target / "attachment" / "patch"
            if patch.is_dir():
                shutil.rmtree(patch, ignore_errors=True)
            if target.name != new_main:
                try:
                    target.rename(target.with_name(new_main))
                except OSError:
                    pass

        # (3) project.json / project.json.bak 참조 갱신
        for pj in (tl_dir / "project.json", tl_dir / "project.json.bak"):
            if not pj.is_file():
                continue
            try:
                data = _read_json(pj)
            except (OSError, ValueError):
                continue
            if not isinstance(data, dict):
                continue
            data["id"] = new_tlset
            data["main_timeline_id"] = new_main
            for t in data.get("timelines", []) or []:
                if isinstance(t, dict) and t.get("id") == orig_main_id:
                    t["id"] = new_main
                    t["update_time"] = now_us
            data["update_time"] = now_us
            write_json(pj, data)

    # (4) timeline_layout.json 의 타임라인 id 치환
    tll = proj_dir / "timeline_layout.json"
    if tll.is_file() and orig_main_id:
        try:
            txt = tll.read_text(encoding="utf-8").replace(str(orig_main_id),
                                                          new_main)
            tll.write_text(txt, encoding="utf-8")
        except OSError:
            pass

    return {"main_id": new_main, "tlset_id": new_tlset,
            "project_id": new_project}


def export_via_template(guide: Dict,
                        root_override: Optional[str] = None,
                        template_name: str = "챙맛 템플릿",
                        tts_cfg=None,
                        tts_out_dir: Optional[str] = None,
                        cut_assets: Optional[Dict[str, Dict]] = None) -> Dict:
    """기존 '템플릿' 프로젝트를 복사한 뒤, 그 복사본에만 제목/자막/추천 메모/TTS
    오디오를 채워 넣습니다. 원본 템플릿은 절대 수정하지 않습니다.

    - 자막은 제목 바로 아래에 배치, 이미지 추천은 텍스트 메모로 삽입.
    - TTS(Typecast)가 활성화되면 자막 단위별로 음성을 생성하고, 그 길이의 합이
      전체 영상 길이가 됩니다(자막/오디오/배경 모두 그 길이에 맞춰짐).
    """
    root = find_capcut_draft_root(root_override)
    if root is None:
        return {"ok": False,
                "reason": "이 PC 에서 CapCut 프로젝트 폴더를 찾지 못했습니다. "
                          "CapCut 을 한 번 실행한 뒤 다시 시도하세요."}

    tpl_dir = _find_template_project(root, template_name)
    if tpl_dir is None:
        return {"ok": False,
                "reason": f"'{template_name}' 프로젝트를 찾지 못했습니다. "
                          "CapCut 에 해당 이름의 프로젝트가 있는지 확인하세요."}
    cut_assets = cut_assets or {}

    segments = [s for s in (guide.get("segments") or [])
                if (s.get("narration") or s.get("caption") or "").strip()]
    if not segments:
        return {"ok": False, "reason": "내보낼 자막(세그먼트)이 없습니다."}

    title = (guide.get("title") or "shortly 대본").strip() or "shortly 대본"
    title_text = (guide.get("hook") or guide.get("title") or "").strip()

    # 1) 템플릿 폴더 복사(원본 미수정)
    folder_name = _safe_folder(title)
    proj_dir = root / folder_name
    if proj_dir.exists():
        folder_name = f"{folder_name}_{time.strftime('%m%d_%H%M%S')}"
        proj_dir = root / folder_name
    try:
        shutil.copytree(tpl_dir, proj_dir)
    except (OSError, shutil.Error) as exc:
        return {"ok": False, "reason": f"템플릿 복사 실패: {exc}"}

    # 2) 복사본 draft_content 로드 (인트로 길이/자막 예시 파악)
    #    TTS 저장 폴더는 '제목' 기준으로 고정(타임스탬프 없음) → 재내보내기 시 재사용.
    if tts_out_dir:
        tts_dir = Path(tts_out_dir) / _safe_folder(title)
    else:
        tts_dir = proj_dir / "shortly_tts"
    dc = _read_json(proj_dir / "draft_content.json")
    orig_id = dc.get("id")
    template_dur = int(dc.get("duration") or 0)
    mats = dc.setdefault("materials", {})

    style_seg, title_y = _find_title_style_seg(dc)
    caption_y = round(title_y - _CAP_UNDER_TITLE, 4)
    intro_template_end = _blank_intro_end(dc)
    if intro_template_end <= 0:
        intro_template_end = _INTRO_DEFAULT_US

    tts_enabled = bool(tts_cfg and getattr(tts_cfg, "enabled", False))

    # 2-1) 제목 TTS(끝에 '?' 부착) — 인트로(제목 표시) 구간에 재생
    title_wav = None
    title_dur = 0
    title_reused = False
    if tts_enabled and title_text:
        try:
            from ..tts import synth_to_file
            title_tts_text = _title_for_tts(title_text)
            title_wav = _tts_cache_path(tts_cfg, tts_dir, title_tts_text,
                                        prefix="title")
            title_reused = title_wav.is_file() and \
                title_wav.stat().st_size > 44
            title_dur = synth_to_file(tts_cfg, title_tts_text, title_wav)
        except Exception as exc:  # noqa: BLE001 - 실패 시 인트로는 템플릿 길이 유지
            log.warning("제목 TTS 실패: %s", exc)
            title_wav = None
            title_dur = 0
            title_reused = False

    # 인트로 길이 = 제목 표시 구간과 제목 TTS 중 더 긴 쪽(대사는 그 이후 시작)
    intro_dur = max(intro_template_end, title_dur)

    # 2-2) 문장(절) 단위 TTS + 자막 조각 계획 — 인트로 이후부터 배치
    #      TTS 는 문장 단위(자연 발화), 자막은 그 안에서 짧은 조각으로 노출.
    plan = _plan_sentences(segments, tts_cfg, tts_dir, start_offset=intro_dur)
    units = plan["captions"]
    total_us = plan["total_us"]
    if not units:
        shutil.rmtree(proj_dir, ignore_errors=True)
        return {"ok": False, "reason": "자막 단위를 만들지 못했습니다."}

    # 3) 인트로 제목 카드(시작부에 제목이 한 번 더 크게 뜨는 부분) 먼저 채우고,
    #    나머지 제목 자리표시자(메인 제목 등)를 채운다.
    intro_filled = _fill_intro_title_card(dc, title_text, intro_template_end) \
        if title_text else 0
    filled = _fill_titles(dc, title_text) if title_text else 0
    _extend_full_segments(dc, template_dur, total_us)
    _extend_intro_segments(dc, intro_template_end, intro_dur)

    # 3-1) 자막 위치/크기: 템플릿에 넣어둔 '자막 예시'가 있으면 그대로 따른다
    sample = _find_caption_sample(dc)
    if sample is not None:
        cap_style_seg = copy.deepcopy(sample["seg"])  # 위치/크기/스타일 원본 보존
        cap_y = None  # 예시 위치를 그대로 사용
        try:  # 예시 세그먼트는 화면에 안 나오게 트랙에서 제거
            sample["track"]["segments"].remove(sample["seg"])
            if not sample["track"]["segments"]:  # 비면 트랙까지 제거
                dc["tracks"].remove(sample["track"])
        except (ValueError, KeyError):
            pass
    else:
        cap_style_seg = style_seg
        cap_y = caption_y

    # 4) 자막 트랙 + 추천 메모 트랙
    caption_segs: List[Dict] = []
    caption_layers: Dict[int, List[Dict]] = {}
    reco_segs: List[Dict] = []
    seg_span: Dict[int, List[int]] = {}
    for unit in units:
        span = seg_span.setdefault(
            unit["seg"],
            [unit["start"], unit["start"] + unit["dur"]],
        )
        span[0] = min(span[0], unit["start"])
        span[1] = max(span[1], unit["start"] + unit["dur"])
    if cap_style_seg is not None:
        for k, u in enumerate(units):
            # 자막 조각은 이미 짧은 단위이므로 강제 줄바꿈 없이 한 줄로 노출한다.
            # (기존엔 14자에서 불필요하게 개행되어 어색했음. 두 줄 분배/조각 합치기는
            #  가독성 판단이 필요해 수동 편집에 맡긴다.)
            cseg, cloned = _build_caption_seg(
                cap_style_seg, mats, u["text"],
                u["start"], u["dur"], cap_y, render_index=20000 + k)
            _merge_materials(mats, cloned)
            caption_segs.append(cseg)
            caption_layers.setdefault(int(u.get("layer") or 0), []).append(cseg)

        # 세그먼트별 추천(이미지/매체/효과)을 메모 텍스트로 하단에 배치
        # 세그먼트를 추천 컷 수만큼 나눠 '컷별' 메모를 배치(소스 컷 수와 맞춤)
        style_for_reco = style_seg or cap_style_seg
        reco_no = 0
        for i, s in enumerate(segments):
            if i not in seg_span:
                continue
            imgs = s.get("image_suggestions") or []
            media = s.get("media_suggestions") or []
            effs = s.get("effect_suggestions") or []
            n_cuts = max(len(imgs), int(s.get("cuts_suggested") or 0), 1)
            st, en = seg_span[i]
            span = max(en - st, 400_000)
            for c in range(n_cuts):
                c_start = st + int(round(c * span / n_cuts))
                c_end = st + int(round((c + 1) * span / n_cuts))
                c_dur = max(300_000, c_end - c_start)
                img = _pick(imgs, c)
                med = _pick(media, c)
                eff = _pick(effs, c)
                parts = []
                if img:
                    parts.append(f"컷{reco_no + 1}: {img}"
                                 + (f" ({med})" if med else ""))
                if eff and eff != "없음":
                    parts.append(f"효과: {eff}")
                if not parts:
                    continue
                reco_no += 1
                rseg, cloned_r = _build_caption_seg(
                    style_for_reco, mats, " / ".join(parts), c_start, c_dur,
                    -0.82, render_index=19000 + reco_no, scale=0.5)
                _merge_materials(mats, cloned_r)
                reco_segs.append(rseg)

    # 5) 선택된 생성 에셋. 원본 종횡비를 유지하고, 지정 레퍼런스 프로젝트의
    # 수동 조정 분포를 기준으로 하되 두 줄 자막 안전 영역만큼 축소·하향한다.
    cut_segs: List[Dict] = []
    reference_template = _pick_reference_image_template(root)
    if reference_template is None:
        _, _, reference_template = _pick_templates(root)
    reference_project = (
        reference_template.get("project")
        if isinstance(reference_template, dict)
        else None
    )
    if cut_assets and reference_template is not None:
        cuts_dir = proj_dir / "shortly_cuts"
        cuts_dir.mkdir(parents=True, exist_ok=True)
        cut_no = 0
        for i, segment in enumerate(segments):
            if i not in seg_span:
                continue
            segment_index = int(segment.get("index", i))
            n_cuts = max(
                1,
                min(int(segment.get("cuts_suggested") or 1), 6),
            )
            start, end = seg_span[i]
            duration = max(end - start, 400_000)
            for cut_index in range(n_cuts):
                cut_id = f"s{segment_index:03d}-c{cut_index:02d}"
                selected = cut_assets.get(cut_id) or {}
                source = Path(str(selected.get("path") or ""))
                if not source.is_file():
                    continue
                cut_no += 1
                media_kind = str(selected.get("media_kind") or "image")
                suffix = source.suffix.lower() or (
                    ".mp4" if media_kind == "video" else ".png"
                )
                target = cuts_dir / f"cut_{cut_no:02d}{suffix}"
                shutil.copy2(source, target)
                cut_start = start + int(round(cut_index * duration / n_cuts))
                cut_end = start + int(
                    round((cut_index + 1) * duration / n_cuts)
                )
                cut_duration = max(300_000, cut_end - cut_start)
                width, height = _asset_dimensions(target, media_kind)
                scale, transform_y = _reference_asset_layout(
                    width,
                    height,
                    media_kind,
                )
                video_segment, cloned_video = _build_photo_segment(
                    reference_template,
                    target,
                    cut_start,
                    cut_duration,
                    scale,
                    transform_y,
                    render_index=1000 + cut_no,
                    media_kind=media_kind,
                )
                video_segment["extra_material_refs"] = []
                _merge_materials(mats, cloned_video)
                cut_segs.append(video_segment)

    # 6) TTS 오디오 트랙(제목 + 대사)
    audio_segs: List[Dict] = []
    audio_tpl = None
    need_audio = bool(title_wav) or plan["audio_ok"] > 0
    if need_audio:
        audio_tpl = _find_audio_template(root, exclude=proj_dir.name)
        if audio_tpl is not None:
            if title_wav and Path(title_wav).is_file():
                aseg, cloned_a = _build_audio_seg(
                    audio_tpl, title_wav, 0, title_dur)
                _merge_materials(mats, cloned_a)
                audio_segs.append(aseg)
            # TTS 오디오는 문장(절) 단위로 배치(자막 조각 단위가 아님)
            for a in plan["audios"]:
                if not a.get("wav") or not Path(a["wav"]).is_file():
                    continue
                aseg, cloned_a = _build_audio_seg(
                    audio_tpl, a["wav"], a["start"], a["dur"])
                _merge_materials(mats, cloned_a)
                audio_segs.append(aseg)

    # 7) 새 트랙들을 기존 트랙 뒤에 추가(원본 트랙 유지)
    def _track(ttype: str, segs: List[Dict]) -> Dict:
        return {"id": _new_id(), "type": ttype, "segments": segs,
                "flag": 0, "attribute": 0, "name": "", "is_default_name": True}

    tracks = dc.setdefault("tracks", [])
    if cut_segs:
        tracks.append(_track("video", cut_segs))
    if reco_segs:
        tracks.append(_track("text", reco_segs))
    for layer in sorted(caption_layers):
        tracks.append(_track("text", caption_layers[layer]))
    if audio_segs:
        tracks.append(_track("audio", audio_segs))

    # 8) 내부 id 일괄 재생성(원본과 완전히 분리) + 파일 기록
    now_s = int(time.time())
    now_us = int(time.time() * _US)
    fold_path = str(proj_dir).replace("\\", "/")
    root_path = str(root).replace("\\", "/")

    dc["name"] = title
    dc["duration"] = total_us
    dc["update_time"] = now_s
    if "create_time" in dc:
        dc["create_time"] = now_s

    new_ids = _reclone_project_ids(proj_dir, dc, orig_id, now_us)
    draft_id = new_ids["project_id"]

    # draft_meta_info.json (복사본) 갱신
    meta_path = proj_dir / "draft_meta_info.json"
    try:
        meta = _read_json(meta_path)
        if not isinstance(meta, dict):
            meta = {}
    except (OSError, ValueError):
        meta = {}
    meta.update({
        "draft_id": draft_id,
        "draft_name": title,
        "draft_fold_path": fold_path,
        "draft_root_path": root_path,
        "tm_draft_create": now_us,
        "tm_draft_modified": now_us,
        "tm_draft_removed": 0,
        "tm_duration": total_us,
    })
    write_json(meta_path, meta)

    registered = _register_in_root(root, meta, draft_id, fold_path,
                                   root_path, title, now_us, total_us)

    return {
        "ok": True,
        "project_name": folder_name,
        "path": str(proj_dir),
        "root": str(root),
        "template": tpl_dir.name,
        "segments": len(caption_segs),
        "reco_memos": len(reco_segs),
        "inserted_assets": len(cut_segs),
        "asset_layout_reference": reference_project,
        "tts_generated": plan["audio_ok"],
        "tts_reused": plan.get("audio_reused", 0) + (1 if title_reused else 0),
        "tts_failed": plan["audio_fail"],
        "tts_used": plan["use_tts"],
        "tts_title": bool(title_wav),
        "tts_dir": str(tts_dir) if (plan["audio_ok"] or title_wav) else None,
        "intro_sec": round(intro_dur / _US, 2),
        "caption_sample_used": sample is not None,
        "audio_clips": len(audio_segs),
        "audio_template": audio_tpl.get("project") if audio_tpl else None,
        "has_title": bool(filled or intro_filled),
        "intro_title_slots": intro_filled,
        "total_duration_sec": round(total_us / _US, 2),
        "tts_error": plan["first_err"],
        "registered": registered,
    }
