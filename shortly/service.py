"""학습/생성 파이프라인을 함수로 노출하는 공용 서비스 계층(웹·CLI 공유)."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Callable, Dict, List, Optional

from .analyze.audio_sfx import analyze_audio_sfx
from .analyze.image_content import analyze_image_usage
from .analyze.style_profile import (aggregate_profile, build_image_editing_styles,
                                     render_report)
from .analyze.transcript import extract_transcript
from .analyze.video_cv import analyze_video
from .analyze.vision import analyze_short, describe_keyframes
from .config import Settings, get_settings
from .generate.capcut_export import (export_capcut_draft,
                                      export_to_capcut_project,
                                      export_via_template)
from .generate.guide import build_guide, render_guide_markdown
from .generate.image_reco import recommend_cuts
from .generate.research import research_topic
from .generate.factcheck import apply_factcheck_repairs, factcheck_script
from .generate.script_gen import (
    analyze_story_flow,
    extract_subject_anchors,
    generate_script,
    polish_story_flow,
    revise_script,
    script_preserves_subject,
)
from .ingest.youtube import channel_paths, ingest_channel, load_manifest
from .llm import get_llm
from .sfx.catalog import build_catalog, load_catalog
from .sfx.fingerprint import FingerprintIndex
from .utils import log, read_json, slugify, write_json

Progress = Callable[[str, Optional[int], Optional[int]], None]


def _noop(msg: str, cur: Optional[int] = None, total: Optional[int] = None) -> None:
    log.info(msg)


def _script_changed(left: Dict, right: Dict) -> bool:
    fields = ("title", "hook", "segments")
    return any(left.get(field) != right.get(field) for field in fields)


def _finalize_story_quality(
    llm,
    profile: Dict,
    script: Dict,
    research: Optional[Dict],
    *,
    instruction: str = "",
) -> tuple[Dict, Dict]:
    """Run a final flow gate after factcheck and record evidence quality."""

    before = analyze_story_flow(script)
    final_script = script
    if before:
        final_script = polish_story_flow(
            llm,
            profile,
            script,
            instruction=instruction,
            original_script=script if instruction else None,
        )
    after = analyze_story_flow(final_script)
    tiers: Dict[str, int] = {}
    for result in (research or {}).get("results") or []:
        if not isinstance(result, dict):
            continue
        tier = str(result.get("source_tier") or "general")
        tiers[tier] = tiers.get(tier, 0) + 1
    hard_fact_count = len((research or {}).get("hard_facts") or [])
    credible_source_count = (
        tiers.get("authoritative", 0) + tiers.get("editorial", 0)
    )
    if hard_fact_count and credible_source_count >= 2:
        evidence_status = "strong"
    elif hard_fact_count:
        evidence_status = "usable"
    else:
        evidence_status = "thin"
    metadata = {
        "story_flow": {
            "issues_before_final_pass": before,
            "issues_after_final_pass": after,
            "changed": _script_changed(script, final_script),
            "rejected": final_script.get("_flow_rejected"),
        },
        "research_evidence": {
            "result_count": len((research or {}).get("results") or []),
            "source_tiers": tiers,
            "credible_source_count": credible_source_count,
            "quantified_fact_count": len(
                (research or {}).get("quantified_facts") or []
            ),
            "hard_fact_count": hard_fact_count,
            "thin": bool((research or {}).get("thin", not research)),
            "status": evidence_status,
        },
    }
    return final_script, metadata


# --------------------------------------------------------------------------
# 효과음 지문 인덱스
# --------------------------------------------------------------------------

def build_sfx_index(settings: Optional[Settings] = None, progress: Progress = _noop,
                    max_items: Optional[int] = None, probe: bool = True) -> Dict:
    settings = settings or get_settings()
    settings.ensure_dirs()
    progress("효과음 카탈로그 스캔 중...", None, None)
    items = build_catalog(settings, probe=probe)
    if not items:
        return {"ok": False, "reason": "효과음 파일을 찾지 못했습니다.", "sfx_dir": str(settings.sfx_dir)}

    progress(f"지문 인덱스 구축 중... (효과음 {len(items)}개 카탈로그)", None, None)
    index = FingerprintIndex(sr=settings.audio_sr)
    index.build_from_catalog(items, kinds=("sfx",), max_items=max_items)
    index.save(FingerprintIndex.default_path(settings))
    return {
        "ok": True,
        "catalog_count": len(items),
        "indexed_tracks": len(index.tracks),
        "hash_count": len(index.db),
        "index_path": str(FingerprintIndex.default_path(settings)),
    }


def _load_index(settings: Settings) -> Optional[FingerprintIndex]:
    return FingerprintIndex.load(FingerprintIndex.default_path(settings))


# --------------------------------------------------------------------------
# 채널 학습
# --------------------------------------------------------------------------

def _augment_image_usage(cached: Dict, video_info: Dict, llm) -> bool:
    """캐시된 분석 결과에 컷별 이미지 내용(image_usage)을 보강.

    상위 영상인데 이전 학습 때 이미지 분석을 안 했던 경우를 위해, 재다운로드 없이
    캐시 영상으로 image_usage(및 CV 샷 상세)를 채운다. 갱신 여부를 반환.
    """
    vp = video_info.get("video_path")
    if not vp or not Path(vp).is_file():
        return False
    vpath = Path(vp)
    cv = cached.get("cv") or {}
    # 샷 상세가 없으면 detailed 로 재분석(키프레임은 기존 것 보존)
    if not cv.get("shots"):
        old_kf = cv.get("keyframes")
        cv = analyze_video(vpath, detailed=True)
        if old_kf and not cv.get("keyframes"):
            cv["keyframes"] = old_kf
        cached["cv"] = cv
    layout = cached.get("layout") or (cached.get("transcript", {}) or {}).get("layout")
    narration = (cached.get("transcript", {}) or {}).get("full_text", "")
    cached["image_usage"] = analyze_image_usage(
        llm, vpath, cv, layout=layout, narration_text=narration)
    return True


def analyze_one_video(video_info: Dict, video_dir: Path,
                      llm, settings: Settings, do_ocr: bool,
                      force: bool = False, analyze_images: bool = False) -> Optional[Dict]:
    """단일 영상 분석(캐시 지원).

    analyze_images=True(상위 영상)면 컷별 이미지 내용(image_usage)과 CV 샷 상세를
    추가로 산출한다. 캐시에 image_usage 가 이미 있으면 재실행하지 않는다.
    """
    analysis_path = video_dir / "analysis.json"
    if analysis_path.is_file() and not force:
        cached = read_json(analysis_path, default=None)
        if cached:
            # 상위 영상인데 이미지 분석이 빠져 있으면 보강(비전 느림 → 필요 시에만)
            if analyze_images and not cached.get("image_usage"):
                if _augment_image_usage(cached, video_info, llm):
                    write_json(analysis_path, cached)
            return cached

    video_path = video_info.get("video_path")
    if not video_path or not Path(video_path).is_file():
        return None
    vpath = Path(video_path)

    transcript = extract_transcript(video_info, do_ocr=do_ocr)
    cv = analyze_video(vpath, frames_out=video_dir / "frames", detailed=analyze_images)
    if getattr(settings, "sfx_enabled", True):
        sfx = analyze_audio_sfx(vpath, transcript, cv, settings=settings)
    else:
        sfx = {
            "available": False,
            "reason": "portable(효과음 비활성)",
            "sfx_usages": [],
            "bgm_usages": [],
        }
    frame_descs = describe_keyframes(llm, cv.get("keyframes", []))
    analysis = analyze_short(llm, transcript, cv, sfx, frame_descs)

    result = {
        "id": video_info.get("id"),
        "title": video_info.get("title"),
        "transcript": transcript,
        "cv": cv,
        "sfx": sfx,
        "frame_descriptions": frame_descs,
        "analysis": analysis,
        # 화면 레이아웃(제목/이미지/자막 영역) — 집계 시 최상위에서 읽음
        "layout": transcript.get("layout"),
    }
    if analyze_images:
        # 컷별 이미지 '내용'(피사체/타입/자막 연관성). 영상·움짤·전환은 CV(shots) 담당.
        result["image_usage"] = analyze_image_usage(
            llm, vpath, cv, layout=transcript.get("layout"),
            narration_text=transcript.get("full_text", ""))
    write_json(analysis_path, result)
    return result


def learn_channel(channel_input: str, settings: Optional[Settings] = None,
                  progress: Progress = _noop, max_count: Optional[int] = None,
                  min_views: Optional[int] = None, sort_by_views: bool = True,
                  do_ocr: bool = True, separate: bool = False,
                  force: bool = False) -> Dict:
    """채널 쇼츠를 수집·분석하고 스타일 프로파일을 만듭니다."""
    settings = settings or get_settings()
    settings.ensure_dirs()
    llm = get_llm(settings)
    if not llm.available:
        progress("주의: LLM 미설정 → 분석/요약은 휴리스틱으로 대체됩니다.", None, None)

    if getattr(settings, "sfx_enabled", True) and not load_catalog(settings):
        progress("효과음 카탈로그 구축 중...", None, None)
        build_catalog(settings, probe=False)

    progress("1/3 채널 쇼츠 수집 중...", None, None)
    manifest = ingest_channel(channel_input, settings, max_count=max_count,
                              min_views=min_views, sort_by_views=sort_by_views,
                              progress=progress)
    videos = manifest.get("videos", [])

    progress("2/3 쇼츠 분석 중...", 0, len(videos))
    paths = channel_paths(channel_input, settings)
    per_short: List[Dict] = []
    for i, v in enumerate(videos, start=1):
        progress(f"[{i}/{len(videos)}] 분석: {v.get('title')}", i, len(videos))
        try:
            # 조회수 상위 5개만 컷별 이미지 내용 분석(비전 느림 → 상위에 집중)
            res = analyze_one_video(v, paths.video_dir(v["id"]), llm, settings,
                                    do_ocr=do_ocr, force=force,
                                    analyze_images=(i <= 5))
            if res:
                per_short.append(res)
        except Exception as exc:  # noqa: BLE001
            log.warning("영상 분석 실패 (%s): %s", v.get("id"), exc)

    progress("3/3 스타일 프로파일 종합 중...", None, None)
    profile = aggregate_profile(llm, manifest, per_short)
    write_json(paths.profile_json, profile)
    report = render_report(profile)
    paths.report_md.write_text(report, encoding="utf-8")

    progress("학습 완료", None, None)
    return {"ok": True, "profile": profile, "report": report,
            "analyzed": len(per_short), "slug": paths.slug}


# --------------------------------------------------------------------------
# 가이드 생성
# --------------------------------------------------------------------------

def get_profile(channel_input: str, settings: Optional[Settings] = None) -> Optional[Dict]:
    settings = settings or get_settings()
    paths = channel_paths(channel_input, settings)
    return read_json(paths.profile_json, default=None)


def list_guides(channel_input: str, settings: Optional[Settings] = None) -> List[Dict]:
    """채널별로 이전에 생성한 가이드 목록(최신순)."""
    settings = settings or get_settings()
    paths = channel_paths(channel_input, settings)
    out_root = paths.root / "outputs"
    guides: List[Dict] = []
    if not out_root.is_dir():
        return guides
    for d in out_root.iterdir():
        if not d.is_dir():
            continue
        g = read_json(d / "guide.json", default={}) or {}
        req = read_json(d / "request.json", default={}) or {}
        md = d / "guide.md"
        mtime = md.stat().st_mtime if md.is_file() else d.stat().st_mtime
        guides.append({
            "name": d.name,
            "title": g.get("title") or d.name,
            "hook": g.get("hook", ""),
            "duration_sec": g.get("total_duration_sec"),
            "created": int(mtime),
            "has_capcut": (d / "capcut_draft.json").is_file(),
            # 입력 원문·모드(주제/대본). 이전 버전 출력물은 request.json 이 없어 None
            "input_text": req.get("input_text"),
            "input_type": req.get("input_type", "unknown"),
        })
    guides.sort(key=lambda x: x["created"], reverse=True)
    return guides


def _resolve_guide_dir(channel_input: str, name: str,
                       settings: Settings):
    """outputs 하위의 안전한 가이드 폴더 경로를 반환(밖으로 벗어나면 None)."""
    paths = channel_paths(channel_input, settings)
    out_root = (paths.root / "outputs").resolve()
    target = (out_root / name).resolve()
    if out_root != target and out_root not in target.parents:
        return None
    if not target.is_dir():
        return None
    return target


def delete_guide(channel_input: str, name: str,
                 settings: Optional[Settings] = None) -> Dict:
    """저장된 가이드 폴더를 삭제합니다."""
    settings = settings or get_settings()
    target = _resolve_guide_dir(channel_input, name, settings)
    if target is None:
        return {"ok": False, "reason": "가이드를 찾을 수 없습니다."}
    import shutil
    try:
        shutil.rmtree(target)
    except OSError as exc:
        return {"ok": False, "reason": f"삭제 실패: {exc}"}
    return {"ok": True, "name": name}


def export_guide_to_capcut(channel_input: str, name: str,
                           settings: Optional[Settings] = None) -> Dict:
    """저장된 가이드를 이 PC 의 CapCut 프로젝트로 내보냅니다."""
    settings = settings or get_settings()
    target = _resolve_guide_dir(channel_input, name, settings)
    if target is None:
        return {"ok": False, "reason": "가이드를 찾을 수 없습니다."}
    guide = read_json(target / "guide.json", default=None)
    if not guide:
        return {"ok": False, "reason": "guide.json 을 읽을 수 없습니다."}
    # '챙맛 템플릿' 프로젝트를 복사해 제목/자막/추천 메모/TTS 를 채워 넣습니다.
    # (원본 템플릿은 수정하지 않으며, TTS 길이의 합이 영상 길이가 됩니다.)
    return export_via_template(
        guide,
        template_name=settings.capcut_template,
        tts_cfg=settings.typecast_config(),
        tts_out_dir=str(settings.tts_output_dir),
    )


def _ocr_layout_for_dir(video_dir: Path) -> Optional[Dict]:
    """캐시된 video.mp4 로 easyocr 를 돌려 레이아웃(영역)을 추정."""
    from .analyze.layout import derive_layout_from_ocr
    from .analyze.transcript import _ocr_on_screen

    vids = sorted(video_dir.glob("*.mp4"))
    if not vids:
        return None
    _, frames_items = _ocr_on_screen(vids[0])
    if not frames_items:
        return None
    return derive_layout_from_ocr(frames_items)


def backfill_layout(channel_input: str, count: int = 3,
                    settings: Optional[Settings] = None,
                    progress: Progress = _noop, method: str = "qwen") -> Dict:
    """이미 학습된 채널의 캐시 영상으로 레이아웃 좌표를 backfill.

    추가 학습(재다운로드) 없이, 캐싱된 키프레임/영상에서 제목/이미지/자막 영역
    좌표를 채워 넣고 프로파일을 갱신합니다.

    method="qwen": 캐시 키프레임을 비전 모델(Qwen)에 보내 좌표 추출(정확하나 느림).
    method="ocr":  캐시 video.mp4 에 easyocr 박스로 영역 추정(빠름).
    """
    from .analyze.layout import aggregate_layout, extract_layout_qwen

    settings = settings or get_settings()
    paths = channel_paths(channel_input, settings)
    llm = None
    if method == "qwen":
        llm = get_llm(settings, role="analysis")
        if not llm.available:
            return {"ok": False,
                    "reason": "분석 LLM(Qwen)이 설정되지 않았습니다. LM Studio 서버를 확인하세요."}
    videos_root = paths.videos_dir
    if not videos_root.is_dir():
        return {"ok": False, "reason": "학습된 영상 캐시가 없습니다."}

    dirs = sorted([d for d in videos_root.iterdir() if d.is_dir()])
    updated: List[Dict] = []
    processed = 0
    for d in dirs:
        if processed >= count:
            break
        ap = d / "analysis.json"
        if not ap.is_file():
            continue
        data = read_json(ap, default=None)
        if not data:
            continue
        progress(f"[{processed + 1}/{count}] 좌표 추출({method}): "
                 f"{data.get('title') or d.name}", processed + 1, count)
        if method == "qwen":
            keyframes = [k for k in ((data.get("cv") or {}).get("keyframes") or [])
                         if Path(k).is_file()]
            layout = extract_layout_qwen(keyframes, llm) if keyframes else None
        else:
            layout = _ocr_layout_for_dir(d)
        if not layout:
            log.warning("레이아웃 추출 실패(건너뜀): %s", d.name)
            continue
        data["layout"] = layout
        if isinstance(data.get("transcript"), dict):
            data["transcript"]["layout"] = layout
        write_json(ap, data)
        updated.append({"id": data.get("id") or d.name, "layout": layout})
        processed += 1

    # 전체 재집계: layout 이 있는 모든 영상으로 프로파일 레이아웃 갱신(재합성 없음)
    per_short: List[Dict] = []
    for d in dirs:
        ap = d / "analysis.json"
        if not ap.is_file():
            continue
        data = read_json(ap, default=None)
        if data and isinstance(data.get("layout"), dict):
            per_short.append(data)
    agg = aggregate_layout(per_short)
    profile = read_json(paths.profile_json, default=None)
    if profile is not None and agg:
        profile["layout"] = agg
        write_json(paths.profile_json, profile)
        paths.report_md.write_text(render_report(profile), encoding="utf-8")
        progress("프로파일 레이아웃 갱신 완료", None, None)

    return {"ok": True, "updated_count": len(updated),
            "updated": updated, "profile_layout": agg}


def backfill_image_usage(channel_input: str, count: int = 5,
                         settings: Optional[Settings] = None,
                         progress: Progress = _noop,
                         sample_fps: Optional[float] = None) -> Dict:
    """이미 학습된 채널의 캐시 영상으로 이미지 내용(image_usage)을 backfill.

    재다운로드 없이 캐시된 video.mp4 로 상위 count 개(조회수순) 영상의 image_usage 와
    CV 샷 상세를 채우고, 프로파일의 image_style/editing_style 을 갱신합니다.
    sample_fps 를 주면 초당 그만큼 촘촘히 샘플링(내용 누락 방지). 비전이 느리면
    별도/백그라운드 실행을 권장합니다.
    """
    settings = settings or get_settings()
    paths = channel_paths(channel_input, settings)
    llm = get_llm(settings, role="analysis")
    if not llm.available:
        return {"ok": False,
                "reason": "분석 LLM(Qwen)이 설정되지 않았습니다. LM Studio 서버를 확인하세요."}
    videos_root = paths.videos_dir
    if not videos_root.is_dir():
        return {"ok": False, "reason": "학습된 영상 캐시가 없습니다."}

    manifest = load_manifest(channel_input, settings)
    if not manifest or not manifest.get("videos"):
        return {"ok": False, "reason": "채널 매니페스트가 없습니다. 먼저 채널을 학습하세요."}
    # 조회수 내림차순으로 상위부터 처리(다운로드 실패/분석 없음은 건너뛰고 다음 순위로)
    videos = sorted(manifest["videos"],
                    key=lambda v: v.get("view_count") or 0, reverse=True)

    def _resolve_vpath(v: Dict, vdir: Path) -> Optional[Path]:
        vp = v.get("video_path")
        if vp and Path(vp).is_file():
            return Path(vp)
        for p in sorted(vdir.glob("video.*")):
            if p.suffix.lower() in {".mp4", ".mkv", ".webm"}:
                return p
        return None

    updated: List[Dict] = []
    processed = 0
    for v in videos:
        if processed >= count:
            break
        vid = v.get("id")
        if not vid:
            continue
        vdir = paths.video_dir(vid)
        ap = vdir / "analysis.json"
        if not ap.is_file():
            continue
        vpath = _resolve_vpath(v, vdir)
        if vpath is None:
            continue
        data = read_json(ap, default=None)
        if not data:
            continue
        processed += 1
        views = v.get("view_count")
        if data.get("image_usage"):
            progress(f"[{processed}/{count}] 이미 분석됨(스킵): {v.get('title')}",
                     processed, count)
            updated.append({"id": vid, "status": "cached",
                            "records": len(data["image_usage"])})
            continue
        progress(f"[{processed}/{count}] 이미지 내용 분석: {v.get('title')} "
                 f"(조회수 {views})", processed, count)
        cv = data.get("cv") or {}
        if not cv.get("shots"):
            old_kf = cv.get("keyframes")
            cv = analyze_video(vpath, detailed=True)
            if old_kf and not cv.get("keyframes"):
                cv["keyframes"] = old_kf
            data["cv"] = cv
        layout = data.get("layout") or (data.get("transcript", {}) or {}).get("layout")
        narration = (data.get("transcript", {}) or {}).get("full_text", "")
        data["image_usage"] = analyze_image_usage(
            llm, vpath, cv, layout=layout, narration_text=narration,
            sample_fps=sample_fps)
        write_json(ap, data)
        updated.append({"id": vid, "records": len(data["image_usage"])})

    # 프로파일의 이미지/편집 경향 재집계(image_usage 있는 모든 영상으로)
    per_short: List[Dict] = []
    for d in sorted([x for x in videos_root.iterdir() if x.is_dir()]):
        ap = d / "analysis.json"
        if not ap.is_file():
            continue
        data = read_json(ap, default=None)
        if data:
            per_short.append(data)
    profile = read_json(paths.profile_json, default=None)
    if profile is not None and per_short:
        img_edit = build_image_editing_styles(llm, per_short)
        profile["image_style"] = img_edit.get("image_style", {})
        profile["editing_style"] = img_edit.get("editing_style", {})
        write_json(paths.profile_json, profile)
        paths.report_md.write_text(render_report(profile), encoding="utf-8")
        progress("프로파일 이미지/편집 경향 갱신 완료", None, None)

    return {"ok": True, "updated_count": len(updated), "updated": updated}


def get_guide(channel_input: str, name: str,
              settings: Optional[Settings] = None) -> Optional[Dict]:
    """저장된 특정 가이드의 내용(마크다운 + 다운로드 경로)을 반환합니다."""
    settings = settings or get_settings()
    target = _resolve_guide_dir(channel_input, name, settings)
    if target is None:
        return None
    md = target / "guide.md"
    capcut = target / "capcut_draft.json"
    return {
        "name": name,
        "request": read_json(target / "request.json", default=None),
        "guide": read_json(target / "guide.json", default={}),
        "quality": read_json(target / "quality.json", default={}),
        "guide_md": md.read_text(encoding="utf-8") if md.is_file() else "",
        "output_dir": str(target),
        "capcut_path": str(capcut) if capcut.is_file() else None,
    }


def generate_guide(channel_input: str, text: str, is_script: bool = False,
                   settings: Optional[Settings] = None, progress: Progress = _noop) -> Dict:
    """학습된 채널 스타일로 대본→가이드→CapCut 초안을 생성합니다."""
    settings = settings or get_settings()
    profile = get_profile(channel_input, settings)
    if profile is None:
        return {"ok": False, "reason": "학습된 프로파일이 없습니다. 먼저 채널을 학습하세요."}

    llm = get_llm(settings, role="generation")
    research = None
    initial_subject_anchors = extract_subject_anchors({}, text)
    # 주제 모드: 웹 조사로 팩트/각도 보강(대본 모드·비활성 설정이면 스킵)
    if (not is_script) and getattr(settings, "research_enabled", True):
        progress("웹 조사 중...", None, None)
        log.info("generate_guide: 웹 조사 시작 topic=%r", text[:80])
        research = research_topic(
            text, llm=llm,
            max_results=getattr(settings, "research_max_results", 8),
            max_queries=getattr(settings, "research_max_queries", 4),
            subject_anchors=initial_subject_anchors,
        )
        n = len((research or {}).get("results") or [])
        hf = len((research or {}).get("hard_facts") or [])
        log.info("generate_guide: 웹 조사 완료 results=%d hard_facts=%d queries=%s",
                 n, hf, (research or {}).get("queries"))
        progress(f"웹 조사 완료 (결과 {n}건, 하드팩트 {hf})", None, None)
    elif not is_script:
        # 조사 비활성 시에도 창작 억제 메모를 남긴다
        research = {
            "queries": [], "results": [], "hard_facts": [],
            "soft_angles": [], "curiosity_hooks": [],
            "avoid_claims": ["조사 비활성 — 입력에 없는 경위·조건·인용 창작 금지"],
            "brief_text": "(웹 조사 비활성 — 사용자 입력에 없는 사건 경위를 창작하지 말 것)",
            "thin": True,
        }

    progress(f"대본 생성 중... (모델: {llm.text_model})", None, None)
    script = generate_script(
        llm, profile, text, is_script=is_script, research=research)

    factcheck_meta = None
    if (not is_script) and getattr(settings, "factcheck_enabled", True):
        progress("팩트체크 중 (시점·주체·수치 범위)...", None, None)
        fc = factcheck_script(
            llm, script, research=research, topic=text, force=True)
        factcheck_meta = {
            "changed": fc.get("changed"),
            "issues": fc.get("issues") or [],
            "fixes": fc.get("fixes") or [],
            "skipped": fc.get("skipped"),
            "extra_results": fc.get("extra_results") or [],
        }
        if fc.get("changed") and fc.get("script"):
            repaired_script = apply_factcheck_repairs(
                script,
                fc["script"],
                issues=factcheck_meta["issues"],
                fixes=factcheck_meta["fixes"],
            )
            if repaired_script.get("_factcheck_rejected"):
                factcheck_meta["changed"] = False
                factcheck_meta["application_rejected"] = (
                    repaired_script["_factcheck_rejected"]
                )
                progress("팩트체크 구조 변경 거부 (기존 대본 유지)", None, None)
            else:
                script = repaired_script
                progress(
                    f"팩트체크 수정 반영 ({len(factcheck_meta['fixes'])}건)",
                    None, None)
        else:
            progress("팩트체크 완료 (수정 없음)", None, None)

    quality_meta: Dict = {}
    if not is_script:
        flow_issues = analyze_story_flow(script)
        if flow_issues:
            progress("최종 이야기 흐름 정리 중...", None, None)
        script, quality_meta = _finalize_story_quality(
            llm,
            profile,
            script,
            research,
        )
        if flow_issues:
            remaining = quality_meta["story_flow"]["issues_after_final_pass"]
            progress(
                (
                    "최종 이야기 흐름 정리 완료"
                    if not remaining
                    else f"흐름 점검 완료 (남은 주의 {len(remaining)}건)"
                ),
                None,
                None,
            )

    progress("제작 가이드 구성 중...", None, None)
    guide = build_guide(profile, script)
    # 컷별 이미지/매체/효과 추천(학습된 image_style·editing_style 기반)
    guide = recommend_cuts(llm, profile, guide)
    guide_md = render_guide_markdown(guide)

    paths = channel_paths(channel_input, settings)
    out_dir = paths.root / "outputs" / slugify(guide.get("title") or text[:30])
    out_dir.mkdir(parents=True, exist_ok=True)
    # 입력 원문·모드·시각을 그대로 보존(주제/대본 이력 조회용)
    request_meta = {
        "channel_input": channel_input,
        "input_text": text,
        "input_type": "script" if is_script else "topic",
        "is_script": is_script,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "title": guide.get("title"),
        "subject_anchors": extract_subject_anchors(script, text),
        "research_used": bool(research and (research.get("results")
                                            or research.get("hard_facts"))),
        "factcheck_used": bool(factcheck_meta and not factcheck_meta.get("skipped")),
        "factcheck_changed": bool(factcheck_meta and factcheck_meta.get("changed")),
        "quality_gate_used": bool(quality_meta),
        "quality_gate_passed": not bool(
            (quality_meta.get("story_flow") or {}).get(
                "issues_after_final_pass"
            )
        ),
    }
    write_json(out_dir / "request.json", request_meta)
    write_json(out_dir / "script.json", script)
    write_json(out_dir / "guide.json", guide)
    # 조사 사용 여부·빈 결과도 남겨 환각 원인 추적에 쓴다
    if research is not None:
        write_json(out_dir / "research.json", research)
    if factcheck_meta is not None:
        write_json(out_dir / "factcheck.json", factcheck_meta)
    if quality_meta:
        write_json(out_dir / "quality.json", quality_meta)
    (out_dir / "guide.md").write_text(guide_md, encoding="utf-8")

    progress("CapCut 초안 생성 중...", None, None)
    capcut_path = out_dir / "capcut_draft.json"
    export_capcut_draft(guide, capcut_path)

    progress("가이드 생성 완료", None, None)
    return {
        "ok": True,
        "request": request_meta,
        "script": script,
        "guide": guide,
        "guide_md": guide_md,
        "research": research,
        "quality": quality_meta,
        "output_dir": str(out_dir),
        "capcut_path": str(capcut_path),
    }


def _unique_output_dir(out_root: Path, base_slug: str) -> Path:
    """outputs 하위에 겹치지 않는 새 폴더 경로를 만듭니다(원본 미덮어씀)."""
    base_slug = base_slug or "guide"
    candidate = out_root / base_slug
    if not candidate.exists():
        return candidate
    stamp = datetime.now().strftime("%m%d_%H%M%S")
    candidate = out_root / f"{base_slug}_{stamp}"
    n = 2
    while candidate.exists():
        candidate = out_root / f"{base_slug}_{stamp}_{n}"
        n += 1
    return candidate


def _revision_lineage_context(
    target: Path,
    *,
    max_depth: int = 8,
) -> Dict:
    """Collect root request metadata and prior evidence across revisions."""

    out_root = target.parent.resolve()
    current = target.resolve()
    visited = set()
    root_input_text = ""
    root_input_type = ""
    subject_anchors: List[str] = []
    research_results: List[Dict] = []
    seen_results = set()

    for _ in range(max_depth):
        current_key = str(current).casefold()
        if current_key in visited or current.parent != out_root:
            break
        visited.add(current_key)
        request = read_json(current / "request.json", default={}) or {}
        research = read_json(current / "research.json", default={}) or {}
        factcheck = read_json(current / "factcheck.json", default={}) or {}

        stored_anchors = request.get("subject_anchors")
        if not subject_anchors and isinstance(stored_anchors, list):
            subject_anchors = [
                str(value).strip()
                for value in stored_anchors
                if str(value).strip()
            ]

        input_type = str(request.get("input_type") or "").strip()
        if input_type in ("topic", "script"):
            root_input_text = str(request.get("input_text") or "").strip()
            root_input_type = input_type
        original_type = str(
            request.get("original_input_type") or ""
        ).strip()
        original_text = str(
            request.get("original_input_text") or ""
        ).strip()
        if original_type in ("topic", "script") and original_text:
            root_input_text = original_text
            root_input_type = original_type

        for row in [
            *(research.get("results") or []),
            *(factcheck.get("extra_results") or []),
        ]:
            if not isinstance(row, dict):
                continue
            key = (
                str(row.get("url") or "").split("?", 1)[0]
                or str(row.get("title") or "").strip().casefold()
            )
            if not key or key in seen_results:
                continue
            seen_results.add(key)
            research_results.append(row)

        source_name = str(
            request.get("source_guide_name") or ""
        ).strip()
        if not source_name:
            break
        next_target = (out_root / source_name).resolve()
        if next_target.parent != out_root or not next_target.is_dir():
            break
        current = next_target

    return {
        "root_input_text": root_input_text,
        "root_input_type": root_input_type,
        "subject_anchors": subject_anchors,
        "research_results": research_results,
    }


def revise_guide(channel_input: str, name: str, instruction: str,
                 settings: Optional[Settings] = None,
                 progress: Progress = _noop) -> Dict:
    """저장된 가이드의 완성 대본을 수정 요청 방향대로 다시 다듬어 새 가이드로 저장합니다.

    원본 가이드 폴더는 그대로 두고, 새 outputs 폴더에 수정본을 저장합니다.
    """
    settings = settings or get_settings()
    instruction = (instruction or "").strip()
    if not instruction:
        return {"ok": False, "reason": "수정 요청 내용을 입력하세요."}

    target = _resolve_guide_dir(channel_input, name, settings)
    if target is None:
        return {"ok": False, "reason": "원본 가이드를 찾을 수 없습니다."}

    original_script = read_json(target / "script.json", default=None)
    if not original_script:
        return {"ok": False,
                "reason": "원본 대본(script.json)을 읽을 수 없어 수정할 수 없습니다."}
    original_request = read_json(target / "request.json", default={}) or {}
    lineage = _revision_lineage_context(target)
    root_input_text = str(
        lineage.get("root_input_text")
        or original_request.get("original_input_text")
        or original_request.get("input_text")
        or ""
    ).strip()
    root_input_type = str(
        lineage.get("root_input_type")
        or original_request.get("original_input_type")
        or original_request.get("input_type")
        or ""
    ).strip()
    stored_subject_anchors = (
        lineage.get("subject_anchors")
        or original_request.get("subject_anchors")
    )
    subject_anchors = (
        [
            str(value).strip()
            for value in stored_subject_anchors
            if str(value).strip()
        ]
        if isinstance(stored_subject_anchors, list)
        else []
    )
    if not subject_anchors:
        subject_anchors = extract_subject_anchors(
            original_script,
            root_input_text,
        )

    profile = get_profile(channel_input, settings)
    if profile is None:
        return {"ok": False, "reason": "학습된 프로파일이 없습니다. 먼저 채널을 학습하세요."}

    llm = get_llm(settings, role="generation")
    if not llm.available:
        return {"ok": False,
                "reason": "대본 수정에는 생성 AI 연결이 필요합니다. 설정에서 AI를 연결하세요."}

    research = None
    if getattr(settings, "research_enabled", True):
        progress("웹 조사 중...", None, None)
        subject_prefix = " ".join(subject_anchors)
        seed = "\n".join(x for x in [
            subject_prefix,
            root_input_text[:1200],
            str(original_script.get("title") or ""),
            str(original_script.get("hook") or ""),
            " ".join(
                str(segment.get("text") or "")
                for segment in original_script.get("segments") or []
                if isinstance(segment, dict)
            )[:1600],
            instruction,
        ] if x).strip()
        research = research_topic(
            seed, llm=llm,
            max_results=getattr(settings, "research_max_results", 8),
            max_queries=getattr(settings, "research_max_queries", 4),
            subject_anchors=subject_anchors,
            seed_results=lineage.get("research_results") or [],
        )
        n = len((research or {}).get("results") or [])
        progress(f"웹 조사 완료 ({n}건)", None, None)

    progress(f"대본 수정 중... (모델: {llm.text_model})", None, None)
    script = revise_script(
        llm,
        profile,
        original_script,
        instruction,
        research=research,
        subject_anchors=subject_anchors,
    )
    if script.get("_source") in ("revise_failed", "unavailable"):
        return {"ok": False,
                "reason": "대본 수정에 실패했습니다. 잠시 후 다시 시도하거나 요청을 바꿔보세요."}
    if not script_preserves_subject(script, subject_anchors):
        return {
            "ok": False,
            "reason": (
                "수정 결과가 원본 회사·제품을 다른 주체로 바꾸려 해 저장하지 "
                "않았습니다. 원본 가이드와 수정 요청을 확인한 뒤 다시 시도하세요."
            ),
            "subject_guard": {
                "anchors": subject_anchors,
                "stage": "revision",
            },
        }

    factcheck_meta = None
    if getattr(settings, "factcheck_enabled", True):
        progress("팩트체크 중 (시점·주체·수치 범위)...", None, None)
        seed = "\n".join(x for x in [
            " ".join(subject_anchors),
            root_input_text,
            str(script.get("title") or ""),
            instruction,
        ] if x).strip()
        fc = factcheck_script(
            llm,
            script,
            research=research,
            topic=seed,
            force=True,
            revision_instruction=instruction,
        )
        factcheck_meta = {
            "changed": fc.get("changed"),
            "issues": fc.get("issues") or [],
            "fixes": fc.get("fixes") or [],
            "skipped": fc.get("skipped"),
            "extra_results": fc.get("extra_results") or [],
        }
        if fc.get("changed") and fc.get("script"):
            scoped_factcheck_script = apply_factcheck_repairs(
                script,
                fc["script"],
                issues=factcheck_meta["issues"],
                fixes=factcheck_meta["fixes"],
            )
            if scoped_factcheck_script.get("_factcheck_rejected"):
                factcheck_meta["changed"] = False
                factcheck_meta["application_rejected"] = (
                    scoped_factcheck_script["_factcheck_rejected"]
                )
                progress(
                    "팩트체크 구조 변경 거부 (수정 전 대본 유지)",
                    None,
                    None,
                )
            elif script_preserves_subject(
                scoped_factcheck_script,
                subject_anchors,
            ):
                script = scoped_factcheck_script
                progress(
                    f"팩트체크 수정 반영 ({len(factcheck_meta['fixes'])}건)",
                    None, None)
            else:
                factcheck_meta["changed"] = False
                factcheck_meta["subject_change_rejected"] = True
                factcheck_meta["issues"].append(
                    "팩트체크 결과가 원본 주체를 제거해 수정 반영을 거부했습니다."
                )
                progress("팩트체크 주체 변경 거부 (수정 전 대본 유지)", None, None)
        else:
            progress("팩트체크 완료 (수정 없음)", None, None)

    flow_issues = analyze_story_flow(script)
    if flow_issues:
        progress("최종 이야기 흐름 정리 중...", None, None)
    script, quality_meta = _finalize_story_quality(
        llm,
        profile,
        script,
        research,
        instruction=instruction,
    )
    if flow_issues:
        remaining = quality_meta["story_flow"]["issues_after_final_pass"]
        progress(
            (
                "최종 이야기 흐름 정리 완료"
                if not remaining
                else f"흐름 점검 완료 (남은 주의 {len(remaining)}건)"
            ),
            None,
            None,
        )

    progress("제작 가이드 재구성 중...", None, None)
    guide = build_guide(profile, script)
    guide = recommend_cuts(llm, profile, guide)
    guide_md = render_guide_markdown(guide)

    paths = channel_paths(channel_input, settings)
    out_root = paths.root / "outputs"
    out_root.mkdir(parents=True, exist_ok=True)
    out_dir = _unique_output_dir(out_root, slugify(guide.get("title") or name))
    out_dir.mkdir(parents=True, exist_ok=True)

    request_meta = {
        "channel_input": channel_input,
        "input_text": instruction,
        "input_type": "revision",
        "is_script": False,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "title": guide.get("title"),
        "source_guide_name": name,
        "revision_instruction": instruction,
        "original_input_text": root_input_text,
        "original_input_type": root_input_type,
        "subject_anchors": subject_anchors,
        "research_used": bool(research and (research.get("results")
                                            or research.get("hard_facts"))),
        "factcheck_used": bool(factcheck_meta and not factcheck_meta.get("skipped")),
        "factcheck_changed": bool(factcheck_meta and factcheck_meta.get("changed")),
        "quality_gate_used": True,
        "quality_gate_passed": not bool(
            quality_meta["story_flow"]["issues_after_final_pass"]
        ),
    }
    write_json(out_dir / "request.json", request_meta)
    write_json(out_dir / "script.json", script)
    write_json(out_dir / "guide.json", guide)
    if research:
        write_json(out_dir / "research.json", research)
    if factcheck_meta is not None:
        write_json(out_dir / "factcheck.json", factcheck_meta)
    write_json(out_dir / "quality.json", quality_meta)
    (out_dir / "guide.md").write_text(guide_md, encoding="utf-8")

    progress("CapCut 초안 생성 중...", None, None)
    capcut_path = out_dir / "capcut_draft.json"
    export_capcut_draft(guide, capcut_path)

    progress("대본 수정 완료", None, None)
    return {
        "ok": True,
        "request": request_meta,
        "script": script,
        "guide": guide,
        "guide_md": guide_md,
        "quality": quality_meta,
        "output_dir": str(out_dir),
        "capcut_path": str(capcut_path),
        "source_guide_name": name,
    }


# --------------------------------------------------------------------------
# 조회
# --------------------------------------------------------------------------

def list_channels(settings: Optional[Settings] = None) -> List[Dict]:
    settings = settings or get_settings()
    settings.ensure_dirs()
    out: List[Dict] = []
    for d in sorted(settings.channels_dir.glob("*")):
        if not d.is_dir():
            continue
        manifest = read_json(d / "channel.json", default={})
        has_profile = (d / "profile.json").is_file()
        out.append({
            "slug": d.name,
            "input": manifest.get("input", d.name),
            "count": manifest.get("count", 0),
            "learned": has_profile,
        })
    return out


def sfx_index_status(settings: Optional[Settings] = None) -> Dict:
    settings = settings or get_settings()
    fp_path = FingerprintIndex.default_path(settings)
    catalog = load_catalog(settings)
    from .sfx.clap_match import _clap_cache_paths
    clap_npz, clap_meta = _clap_cache_paths(settings)
    return {
        "index_exists": fp_path.is_file(),
        "index_path": str(fp_path),
        "clap_cache_exists": clap_npz.is_file() and clap_meta.is_file(),
        "clap_cache_path": str(clap_npz),
        "catalog_count": len(catalog),
        "sfx_dir": str(settings.sfx_dir),
        "sfx_dir_exists": settings.sfx_dir.is_dir(),
        "sfx_method": "drums_clap",
    }
