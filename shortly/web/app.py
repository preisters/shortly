"""FastAPI 로컬 웹 앱: 학습/생성 실행, 진행상황(SSE), 결과 조회·다운로드."""

from __future__ import annotations

import json
import queue
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

import ipaddress

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from ..config import get_settings, save_settings
from .. import service as default_service
from ..llm import get_llm
from ..product_profile import ProductKind
from .assets import render_index
from .composition import ServiceProxy

service = ServiceProxy(default_service)

STATIC_DIR = Path(__file__).parent / "static"

app = FastAPI(title="shortly", version="0.1.0")


def _is_lan_ip(host: str) -> bool:
    """사설망/로컬 주소인지 판별. 공인(외부) IP 는 False."""
    if not host:
        return False
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    # 사설망(10/8, 172.16/12, 192.168/16, fc00::/7 등) · 루프백 · 링크로컬만 허용
    return ip.is_private or ip.is_loopback or ip.is_link_local


@app.middleware("http")
async def _lan_only_guard(request: Request, call_next):
    """LAN 전용 모드일 때 외부(공인) IP 접속을 차단합니다."""
    settings = get_settings()
    if settings.lan_only:
        client = request.client.host if request.client else ""
        if not _is_lan_ip(client):
            return JSONResponse(
                status_code=403,
                content={"detail": "외부 접속 차단됨 (같은 네트워크에서만 접속 가능합니다)."},
            )
    response = await call_next(request)
    # 페이지·정적 자산이 예전 캐시로 남아 UI가 갱신되지 않는 문제 방지
    path = request.url.path
    if path == "/" or path.startswith("/static"):
        response.headers["Cache-Control"] = "no-store, max-age=0"
    return response


# --------------------------------------------------------------------------
# 작업(Job) 관리 - 백그라운드 실행 + SSE 진행상황
# --------------------------------------------------------------------------

@dataclass
class Job:
    id: str
    kind: str
    status: str = "running"  # running | done | error
    logs: List[str] = field(default_factory=list)
    result: Optional[dict] = None
    error: Optional[str] = None
    queue: "queue.Queue" = field(default_factory=queue.Queue)


JOBS: Dict[str, Job] = {}


def _make_progress(job: Job):
    def progress(msg: str, cur=None, total=None) -> None:
        line = msg if cur is None or not total else f"({cur}/{total}) {msg}"
        job.logs.append(line)
        job.queue.put({"type": "log", "message": line})
    return progress


def _run_job(job: Job, fn) -> None:
    try:
        result = fn(_make_progress(job))
        job.result = result
        job.status = "done" if (not isinstance(result, dict) or result.get("ok", True)) else "error"
        if isinstance(result, dict) and result.get("ok") is False:
            job.error = result.get("reason")
        job.queue.put({"type": "done", "status": job.status, "result": _safe(result)})
    except Exception as exc:  # noqa: BLE001
        job.status = "error"
        job.error = str(exc)
        job.queue.put({"type": "error", "message": str(exc)})


def _safe(obj):
    """SSE/JSON 전송용으로 큰 객체를 요약."""
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if k in ("guide", "profile", "script"):
                out[k] = v
            elif k == "report" or k == "guide_md":
                out[k] = v
            else:
                out[k] = v
        return out
    return obj


def _start_job(kind: str, fn) -> str:
    job = Job(id=uuid.uuid4().hex, kind=kind)
    JOBS[job.id] = job
    threading.Thread(target=_run_job, args=(job, fn), daemon=True).start()
    return job.id


# --------------------------------------------------------------------------
# 요청 모델
# --------------------------------------------------------------------------

class LearnRequest(BaseModel):
    channel: str
    max_count: Optional[int] = None
    min_views: Optional[int] = None
    do_ocr: bool = True
    separate_vocals: bool = False
    force: bool = False


class SettingsRequest(BaseModel):
    provider: Optional[str] = None
    api_key: Optional[str] = None       # 선택한 provider 의 키
    text_model: Optional[str] = None
    vision_model: Optional[str] = None  # 비전(이미지) 모델. 비우면 텍스트 모델 재사용
    base_url: Optional[str] = None      # 로컬(Ollama/LM Studio) 엔드포인트
    # 가이드/대본 생성 전용 모델(창작 품질용). 비우면 분석 모델과 동일하게 사용.
    gen_provider: Optional[str] = None
    gen_model: Optional[str] = None
    gen_api_key: Optional[str] = None   # 생성 provider 의 키
    typecast_api_key: Optional[str] = None  # CapCut 내보내기 TTS(Typecast)
    max_shorts: Optional[int] = None
    min_views: Optional[int] = None


class GuideRequest(BaseModel):
    channel: str
    text: str
    is_script: bool = False


class GuideRevisionRequest(BaseModel):
    channel: str
    name: str
    instruction: str


class SfxRequest(BaseModel):
    max_items: Optional[int] = None
    probe: bool = True


# --------------------------------------------------------------------------
# 라우트
# --------------------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
def index() -> HTMLResponse:
    html_path = STATIC_DIR / "index.html"
    if not html_path.is_file():
        return HTMLResponse("<h1>shortly</h1><p>static/index.html 이 없습니다.</p>")
    html = render_index(html_path, get_settings().product)

    def _ver(name: str) -> int:
        p = STATIC_DIR / name
        return int(p.stat().st_mtime) if p.is_file() else 0

    # 파일 수정시각을 쿼리로 붙여, 파일이 바뀌면 브라우저가 새로 받도록 강제(캐시 무효화)
    html = html.replace("/static/app.js", f"/static/app.js?v={_ver('app.js')}")
    html = html.replace("/static/style.css", f"/static/style.css?v={_ver('style.css')}")
    return HTMLResponse(html, headers={"Cache-Control": "no-store, max-age=0"})


@app.get("/api/status")
def api_status() -> dict:
    settings = get_settings()
    llm = get_llm(settings)
    gen_llm = get_llm(settings, role="generation")
    out = {
        "llm_provider": settings.llm_provider,
        "llm_available": llm.available,
        "llm_model": llm.text_model,
        "gen_provider": settings.gen_provider or settings.llm_provider,
        "gen_model": gen_llm.text_model,
        "gen_available": gen_llm.available,
        "portable": bool(settings.portable),
        "sfx_enabled": bool(settings.sfx_enabled),
        "channels": service.list_channels(settings),
    }
    if settings.sfx_enabled:
        out["sfx"] = service.sfx_index_status(settings)
    else:
        out["sfx"] = {
            "index_exists": False,
            "catalog_count": 0,
            "sfx_dir_exists": False,
            "disabled": True,
        }
    return out


@app.get("/api/channels")
def api_channels() -> list:
    return service.list_channels()


@app.get("/api/settings")
def api_get_settings() -> dict:
    from ..config import PORTABLE_ANALYSIS_MODEL, PORTABLE_GENERATION_MODEL

    s = get_settings()
    llm = get_llm(s)
    gen_llm = get_llm(s, role="generation")
    response = {
        "provider": s.llm_provider,
        "text_model": s.text_model,
        "vision_model": s.vision_model,
        "llm_available": llm.available,
        # 생성 전용 모델(비어 있으면 분석 모델과 동일)
        "gen_provider": s.gen_provider,
        "gen_model": s.gen_text_model,
        "gen_effective_model": gen_llm.text_model,
        "gen_available": gen_llm.available,
        "max_shorts": s.max_shorts,
        "min_views": s.min_views,
        "portable": bool(s.portable),
        "sfx_enabled": bool(s.sfx_enabled),
        "portable_analysis_model": PORTABLE_ANALYSIS_MODEL,
        "portable_generation_model": PORTABLE_GENERATION_MODEL,
        "tts_enabled": bool(s.typecast_api_key),
        "capcut_template": s.capcut_template,
        # 키 값은 노출하지 않고 설정 여부만 알려줍니다.
        "keys_set": {
            "gemini": bool(s.gemini_api_key),
            "openai": bool(s.openai_api_key),
            "anthropic": bool(s.anthropic_api_key),
            "typecast": bool(s.typecast_api_key),
        },
    }
    if s.features.local_llm:
        response["local_base_url"] = s.local_base_url
    return response


@app.get("/api/local-models")
def api_local_models(base_url: Optional[str] = Query(None)) -> dict:
    """로컬 OpenAI 호환 서버(LM Studio/Ollama)에 설치·로드된 모델 목록."""
    from ..apps.personal.local_models import discover_local_models

    s = get_settings()
    return discover_local_models(
        base_url or s.local_base_url,
        s.local_api_key,
    )


@app.post("/api/settings")
def api_save_settings(req: SettingsRequest) -> dict:
    from ..config import PORTABLE_ANALYSIS_MODEL, PORTABLE_GENERATION_MODEL

    settings = get_settings()
    updates: dict = {}

    # 포터블: Claude + Typecast(TTS) 키만 저장. 모델/제공자는 서버에서 고정.
    if settings.portable:
        if req.api_key and req.api_key.strip():
            updates["ANTHROPIC_API_KEY"] = req.api_key.strip()
        if req.typecast_api_key and req.typecast_api_key.strip():
            updates["TYPECAST_API_KEY"] = req.typecast_api_key.strip()
        updates["SHORTLY_PORTABLE"] = "1"
        updates["SHORTLY_LLM_PROVIDER"] = "anthropic"
        updates["SHORTLY_TEXT_MODEL"] = PORTABLE_ANALYSIS_MODEL
        updates["SHORTLY_VISION_MODEL"] = PORTABLE_ANALYSIS_MODEL
        updates["SHORTLY_GEN_PROVIDER"] = "anthropic"
        updates["SHORTLY_GEN_MODEL"] = PORTABLE_GENERATION_MODEL
        save_settings(updates)
        return api_get_settings()

    if req.provider:
        updates["SHORTLY_LLM_PROVIDER"] = req.provider
    if req.text_model is not None and req.text_model.strip():
        tm = req.text_model.strip()
        updates["SHORTLY_TEXT_MODEL"] = tm
        # 비전 모델을 따로 지정하지 않으면 텍스트 모델과 동일하게(단일 멀티모달 모델)
        if not (req.vision_model and req.vision_model.strip()):
            updates["SHORTLY_VISION_MODEL"] = tm
    if req.vision_model is not None and req.vision_model.strip():
        updates["SHORTLY_VISION_MODEL"] = req.vision_model.strip()
    _KEY_ENV = {"gemini": "GEMINI_API_KEY", "openai": "OPENAI_API_KEY",
                "anthropic": "ANTHROPIC_API_KEY", "local": "SHORTLY_LOCAL_API_KEY"}
    if req.api_key and req.provider:
        key_env = _KEY_ENV.get(req.provider)
        if key_env:
            updates[key_env] = req.api_key.strip()
    if req.base_url is not None and req.base_url.strip():
        updates["SHORTLY_LOCAL_BASE_URL"] = req.base_url.strip()
    # 생성 전용 모델 설정. gen_provider 가 빈 문자열이면 '분석과 동일'로 초기화.
    if req.gen_provider is not None:
        updates["SHORTLY_GEN_PROVIDER"] = req.gen_provider.strip()
    if req.gen_model is not None:
        updates["SHORTLY_GEN_MODEL"] = req.gen_model.strip()
    if req.gen_api_key and req.gen_provider:
        key_env = _KEY_ENV.get(req.gen_provider.strip())
        if key_env:
            updates[key_env] = req.gen_api_key.strip()
    if req.typecast_api_key and req.typecast_api_key.strip():
        updates["TYPECAST_API_KEY"] = req.typecast_api_key.strip()
    if req.max_shorts is not None:
        updates["SHORTLY_MAX_SHORTS"] = req.max_shorts
    if req.min_views is not None:
        updates["SHORTLY_MIN_VIEWS"] = req.min_views

    save_settings(updates)
    return api_get_settings()


@app.post("/api/sfx/build")
def api_sfx_build(req: SfxRequest) -> dict:
    settings = get_settings()
    if not settings.sfx_enabled:
        raise HTTPException(
            status_code=403,
            detail="포터블 버전에서는 효과음 기능을 사용하지 않습니다.",
        )
    build_sfx_index = service.build_sfx_index
    job_id = _start_job("build-sfx", lambda prog: build_sfx_index(
        settings, progress=prog, max_items=req.max_items, probe=req.probe))
    return {"job_id": job_id}


@app.post("/api/learn")
def api_learn(req: LearnRequest) -> dict:
    settings = get_settings()
    learn_channel = service.learn_channel
    job_id = _start_job("learn", lambda prog: learn_channel(
        req.channel, settings, progress=prog, max_count=req.max_count,
        min_views=req.min_views, do_ocr=req.do_ocr,
        separate=req.separate_vocals, force=req.force))
    return {"job_id": job_id}


@app.post("/api/guide")
def api_guide(req: GuideRequest) -> dict:
    settings = get_settings()
    generate_guide = service.generate_guide
    job_id = _start_job("guide", lambda prog: generate_guide(
        req.channel, req.text, is_script=req.is_script, settings=settings, progress=prog))
    return {"job_id": job_id}


@app.post("/api/guide-revise")
def api_guide_revise(req: GuideRevisionRequest) -> dict:
    settings = get_settings()
    revise_guide = service.revise_guide
    job_id = _start_job("guide-revise", lambda prog: revise_guide(
        req.channel, req.name, req.instruction, settings=settings, progress=prog))
    return {"job_id": job_id}


@app.get("/api/jobs/{job_id}")
def api_job(job_id: str) -> dict:
    job = JOBS.get(job_id)
    if job is None:
        raise HTTPException(404, "job 없음")
    return {"id": job.id, "kind": job.kind, "status": job.status,
            "logs": job.logs, "result": _safe(job.result), "error": job.error}


@app.get("/api/jobs/{job_id}/stream")
def api_job_stream(job_id: str) -> StreamingResponse:
    job = JOBS.get(job_id)
    if job is None:
        raise HTTPException(404, "job 없음")

    def gen():
        # 이미 쌓인 로그 먼저 전송
        for line in list(job.logs):
            yield f"data: {json.dumps({'type': 'log', 'message': line}, ensure_ascii=False)}\n\n"
        while True:
            try:
                evt = job.queue.get(timeout=1.0)
                yield f"data: {json.dumps(evt, ensure_ascii=False)}\n\n"
                if evt.get("type") in ("done", "error"):
                    break
            except queue.Empty:
                if job.status in ("done", "error"):
                    yield f"data: {json.dumps({'type': job.status}, ensure_ascii=False)}\n\n"
                    break
                yield ": keep-alive\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream")


@app.get("/api/profile")
def api_profile(channel: str = Query(...)) -> dict:
    profile = service.get_profile(channel)
    if profile is None:
        raise HTTPException(404, "학습된 프로파일 없음")
    return profile


@app.get("/api/report")
def api_report(channel: str = Query(...)) -> dict:
    from ..ingest.youtube import channel_paths
    paths = channel_paths(channel)
    if not paths.report_md.is_file():
        raise HTTPException(404, "리포트 없음")
    return {"report": paths.report_md.read_text(encoding="utf-8")}


@app.get("/api/guides")
def api_guides(channel: str = Query(...)) -> dict:
    return {"guides": service.list_guides(channel)}


@app.get("/api/guide-detail")
def api_guide_detail(channel: str = Query(...), name: str = Query(...)) -> dict:
    g = service.get_guide(channel, name)
    if g is None:
        raise HTTPException(404, "가이드를 찾을 수 없습니다.")
    return g


@app.delete("/api/guide-detail")
def api_guide_delete(channel: str = Query(...), name: str = Query(...)) -> dict:
    result = service.delete_guide(channel, name)
    if not result.get("ok"):
        raise HTTPException(404, result.get("reason", "삭제 실패"))
    return result


@app.post("/api/guide-export-capcut")
def api_guide_export_capcut(channel: str = Query(...), name: str = Query(...)) -> dict:
    result = service.export_guide_to_capcut(channel, name)
    if not result.get("ok"):
        raise HTTPException(400, result.get("reason", "CapCut 내보내기 실패"))
    return result


@app.get("/api/download")
def api_download(path: str = Query(...)):
    settings = get_settings()
    p = Path(path).resolve()
    data_root = settings.data_dir.resolve()
    if data_root not in p.parents and p != data_root:
        raise HTTPException(403, "허용되지 않은 경로")
    if not p.is_file():
        raise HTTPException(404, "파일 없음")
    return FileResponse(str(p), filename=p.name)


if STATIC_DIR.is_dir():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


def _lan_ips() -> list:
    """이 PC 의 LAN IP 주소 목록(다른 PC 에서 접속할 주소)."""
    import socket

    ips = set()
    try:
        # 외부로 나가는 소켓을 열어 기본 라우트에 쓰이는 로컬 IP 를 알아냅니다(실제 전송 없음).
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("8.8.8.8", 80))
            ips.add(s.getsockname()[0])
        finally:
            s.close()
    except OSError:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if not ip.startswith("127."):
                ips.add(ip)
    except OSError:
        pass
    return sorted(ips)


def _create_runtime_app(settings):
    """Select the concrete product app for the legacy-compatible web command."""

    if settings.portable or settings.product is ProductKind.TEAM:
        from ..apps.team.api import create_app
    else:
        from ..apps.personal.api import create_app
    return create_app()


def run(host: str = "0.0.0.0", port: int = 8756) -> None:
    import uvicorn

    settings = get_settings()
    runtime_app = _create_runtime_app(settings)
    print("=" * 56)
    print(" shortly 웹 UI 시작")
    print(f"  - 이 PC:        http://localhost:{port}")
    if host in ("0.0.0.0", "::"):
        for ip in _lan_ips():
            print(f"  - 같은 공유기의 다른 PC: http://{ip}:{port}")
        print("  * 다른 PC에서 접속해도 모든 처리·다운로드는 이 PC에서 수행됩니다.")
        if settings.lan_only:
            print("  * 보안: 사설망(LAN)에서만 접속 허용, 외부(공인) IP 는 차단됩니다.")
        print("  * 접속이 안 되면 Windows 방화벽에서 이 포트를 허용하세요:")
        print(f"      New-NetFirewallRule -DisplayName \"shortly\" -Direction Inbound "
              f"-LocalPort {port} -Protocol TCP -Action Allow")
    print(f"  * 웹 조사(대본): "
          f"{'ON' if settings.research_enabled else 'OFF'} "
          f"(results<={settings.research_max_results}, "
          f"queries<={settings.research_max_queries})")
    print(f"  * 팩트체크(시점·주체): "
          f"{'ON' if settings.factcheck_enabled else 'OFF'}")
    if settings.portable:
        print("  * 모드: PORTABLE (학습=Sonnet 5, 생성=Opus 4.8, 효과음 OFF)")
    print("=" * 56, flush=True)
    uvicorn.run(runtime_app, host=host, port=port, log_level="info")
