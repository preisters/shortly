"""팀용 번들 앱 진입점 — 서버 기동 후 브라우저를 엽니다.

bundled pythonw 로 실행됩니다(콘솔 없음).
pythonw 에서는 sys.stdout/stderr 가 None 이라 uvicorn 로깅이 깨지므로
로그 파일로 연결한 뒤 서버를 띄웁니다.
"""

from __future__ import annotations

import os
import sys
import threading
import time
import traceback
import webbrowser
from pathlib import Path

from ..path_policy import PathPolicy
from ..product_profile import ProductKind


def _app_root() -> Path:
    """shortly 패키지가 들어 있는 앱 루트 폴더."""
    # .../app/shortly/portable/launcher.py → parents[2] == app
    return Path(__file__).resolve().parents[2]


def _msg_box(title: str, text: str) -> None:
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(0, text, title, 0x10)  # MB_ICONERROR
    except Exception:  # noqa: BLE001
        try:
            print(f"{title}: {text}", file=sys.stderr)
        except Exception:  # noqa: BLE001
            pass


def _ensure_stdio(log_dir: Path) -> Path:
    """pythonw(GUI) 실행 시 None 인 stdout/stderr 를 로그 파일로 연결.

    uvicorn DefaultFormatter 가 sys.stdout.isatty() 를 호출하므로 필수.
    """
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / "shortly.log"
    # append 모드, 라인 버퍼에 가깝게
    log_f = open(log_path, "a", encoding="utf-8", errors="replace", buffering=1)  # noqa: SIM115
    if sys.stdout is None:
        sys.stdout = log_f
    if sys.stderr is None:
        sys.stderr = log_f
    # stdout 만 있고 isatty 없는 래퍼인 경우도 대비
    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        if stream is not None and not hasattr(stream, "isatty"):
            setattr(sys, stream_name, log_f)
    return log_path


def _uvicorn_log_config() -> dict:
    """콘솔/색상(isatty) 의존 없는 uvicorn 로그 설정.

    pythonw 환경에서 uvicorn.DefaultFormatter 가 stdout.isatty() 로 깨지므로
    표준 logging.Formatter 만 사용한다.
    """
    return {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {
            "default": {
                "format": "%(asctime)s %(levelname)s %(name)s: %(message)s",
                "datefmt": "%H:%M:%S",
            },
            "access": {
                "format": "%(asctime)s %(levelname)s %(message)s",
                "datefmt": "%H:%M:%S",
            },
        },
        "handlers": {
            "default": {
                "formatter": "default",
                "class": "logging.StreamHandler",
                "stream": "ext://sys.stderr",
            },
            "access": {
                "formatter": "access",
                "class": "logging.StreamHandler",
                "stream": "ext://sys.stderr",
            },
        },
        "loggers": {
            "uvicorn": {"handlers": ["default"], "level": "WARNING", "propagate": False},
            "uvicorn.error": {
                "handlers": ["default"], "level": "WARNING", "propagate": False,
            },
            "uvicorn.access": {
                "handlers": ["access"], "level": "WARNING", "propagate": False,
            },
        },
    }


def _prepare_env(app_root: Path) -> Path:
    """번들 환경변수·사용자 데이터 폴더를 준비한다. user_data 경로를 반환."""
    os.environ["SHORTLY_PRODUCT"] = ProductKind.TEAM.value
    os.environ["SHORTLY_PORTABLE"] = "1"
    os.environ["SHORTLY_HOST"] = os.environ.get("SHORTLY_HOST") or "127.0.0.1"
    os.environ["SHORTLY_PORT"] = os.environ.get("SHORTLY_PORT") or "8756"
    os.environ["SHORTLY_LAN_ONLY"] = os.environ.get("SHORTLY_LAN_ONLY") or "1"

    ffmpeg_bin = app_root / "runtime" / "ffmpeg" / "bin"
    if ffmpeg_bin.is_dir():
        os.environ["PATH"] = str(ffmpeg_bin) + os.pathsep + os.environ.get("PATH", "")

    paths = PathPolicy.for_product(ProductKind.TEAM, project_root=app_root)
    paths.ensure_dirs()
    user_data = paths.state_root

    os.environ["SHORTLY_USER_DATA"] = str(user_data)
    os.environ.setdefault("SHORTLY_DATA_DIR", str(paths.data_root))
    os.environ.setdefault("SHORTLY_ARTIFACT_DIR", str(paths.artifact_root))
    os.environ.setdefault("SHORTLY_LOG_DIR", str(paths.log_root))
    os.environ.setdefault("SHORTLY_TTS_OUTPUT_DIR", str(paths.tts_root))

    # 앱에 포함된 기본 env → 사용자 .env (없을 때만 복사)
    defaults = app_root / "env.defaults"
    user_env = user_data / ".env"
    if defaults.is_file() and not user_env.is_file():
        user_env.write_text(defaults.read_text(encoding="utf-8"), encoding="utf-8")

    # 작업 디렉터리를 앱 루트로 (상대 경로·패키지 import)
    os.chdir(app_root)
    if str(app_root) not in sys.path:
        sys.path.insert(0, str(app_root))
    return user_data


def _wait_and_open(url: str, timeout: float = 60.0) -> None:
    import urllib.request

    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=1.5) as resp:
                if resp.status < 500:
                    webbrowser.open(url)
                    return
        except Exception:  # noqa: BLE001
            time.sleep(0.4)
    webbrowser.open(url)


def main() -> int:
    app_root = _app_root()
    try:
        user_data = _prepare_env(app_root)
        # pythonw: stdout/stderr=None → uvicorn isatty 오류 방지
        log_dir = Path(os.environ.get("SHORTLY_LOG_DIR") or user_data)
        _ensure_stdio(log_dir)

        # config 가 env 를 읽도록 런처에서 환경 준비 후 import
        from shortly.apps.team.api import create_app
        from shortly.config import get_settings

        settings = get_settings(reload=True)
        fastapi_app = create_app()
        host = settings.host or "127.0.0.1"
        # 0.0.0.0 바인딩이어도 브라우저는 localhost 로
        open_host = "127.0.0.1" if host in ("0.0.0.0", "::") else host
        port = int(settings.port or 8756)
        url = f"http://{open_host}:{port}"

        opener = threading.Thread(target=_wait_and_open, args=(url,), daemon=True)
        opener.start()

        import uvicorn
        uvicorn.run(
            fastapi_app,
            host=host,
            port=port,
            log_level="warning",
            log_config=_uvicorn_log_config(),
        )
        return 0
    except Exception:  # noqa: BLE001
        _msg_box("shortly 실행 오류", traceback.format_exc()[-1500:])
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
