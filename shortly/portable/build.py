"""팀용 Windows 포터블 폴더를 생성합니다.

로컬 개발 버전과 달리:
- SHORTLY_PORTABLE=1 (학습=Sonnet 5, 생성=Opus 5 고정)
- AI 선택 UI 없음 / Claude API 키만
- 효과음 기능 비활성
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Callable, Optional

from ..config import (
    PORTABLE_ANALYSIS_MODEL,
    PORTABLE_GENERATION_MODEL,
    PROJECT_ROOT,
)
from ..product_profile import ProductKind
from ..web.assets import render_index, render_script

Progress = Callable[[str, Optional[int], Optional[int]], None]

_SKIP_DIR_NAMES = {
    ".git", ".venv", "venv", "__pycache__", ".pytest_cache", ".mypy_cache",
    ".cursor", "dist", "node_modules", "agent-transcripts", "terminals",
    ".ruff_cache",
}
_SKIP_FILE_SUFFIXES = {".pyc", ".pyo", ".pyd"}
_SKIP_FILE_NAMES = {
    ".env",  # 개발자 키 유출 방지 — 포터블용 .env 를 새로 씀
}
_TEAM_SOURCE_EXCLUDES = (
    Path("shortly/apps/personal"),
    Path("shortly/llm/gemini.py"),
    Path("shortly/llm/openai_client.py"),
    Path("shortly/llm/openai_transport.py"),
)


def _noop(msg: str, cur: Optional[int] = None, total: Optional[int] = None) -> None:
    pass


def _should_skip(path: Path, root: Path) -> bool:
    rel = path.relative_to(root)
    parts = set(rel.parts)
    if parts & _SKIP_DIR_NAMES:
        return True
    if path.name in _SKIP_FILE_NAMES:
        return True
    if path.suffix.lower() in _SKIP_FILE_SUFFIXES:
        return True
    # 개발 산출물·임시 파일
    if path.name.startswith("_tmp") or path.name.startswith("_plan"):
        return True
    return False


def _is_explicitly_excluded(
    path: Path,
    root: Path,
    excludes: tuple[Path, ...],
) -> bool:
    relative = path.relative_to(root)
    return any(
        relative == excluded or excluded in relative.parents
        for excluded in excludes
    )


def _copy_tree(
    src: Path,
    dst: Path,
    root: Path,
    *,
    excludes: tuple[Path, ...] = (),
) -> int:
    """src 트리를 dst 로 복사. 스킵 규칙 적용. 복사한 파일 수 반환."""
    count = 0
    if _is_explicitly_excluded(src, root, excludes):
        return 0
    if src.is_file():
        if _should_skip(src, root):
            return 0
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        return 1
    for child in src.iterdir():
        if child.name in _SKIP_DIR_NAMES:
            continue
        if _should_skip(child, root):
            continue
        if _is_explicitly_excluded(child, root, excludes):
            continue
        target = dst / child.name
        if child.is_dir():
            count += _copy_tree(
                child,
                target,
                root,
                excludes=excludes,
            )
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(child, target)
            count += 1
    return count


def _copy_team_package(out_dir: Path) -> int:
    """Copy the team source allowlist and materialize its sanitized HTML."""

    package_root = out_dir / "shortly"
    count = _copy_tree(
        PROJECT_ROOT / "shortly",
        package_root,
        PROJECT_ROOT,
        excludes=_TEAM_SOURCE_EXCLUDES,
    )
    source_index = PROJECT_ROOT / "shortly" / "web" / "static" / "index.html"
    target_index = package_root / "web" / "static" / "index.html"
    target_index.write_text(
        render_index(source_index, ProductKind.TEAM),
        encoding="utf-8",
    )
    source_script = PROJECT_ROOT / "shortly" / "web" / "static" / "app.js"
    target_script = package_root / "web" / "static" / "app.js"
    target_script.write_text(
        render_script(source_script, ProductKind.TEAM),
        encoding="utf-8",
    )
    return count


def _write_env(out_dir: Path) -> None:
    text = f"""# shortly 팀용 포터블 설정
# API 키는 웹 UI 설정 화면에서 입력하거나 아래에 직접 넣으세요.

SHORTLY_PORTABLE=1
SHORTLY_LLM_PROVIDER=anthropic
SHORTLY_TEXT_MODEL={PORTABLE_ANALYSIS_MODEL}
SHORTLY_VISION_MODEL={PORTABLE_ANALYSIS_MODEL}
SHORTLY_GEN_PROVIDER=anthropic
SHORTLY_GEN_MODEL={PORTABLE_GENERATION_MODEL}

# Claude API 키 (https://console.anthropic.com/)
ANTHROPIC_API_KEY=

SHORTLY_DATA_DIR=data
SHORTLY_TTS_OUTPUT_DIR=tts_output
SHORTLY_HOST=127.0.0.1
SHORTLY_PORT=8756
SHORTLY_LAN_ONLY=1
SHORTLY_RESEARCH=1
SHORTLY_FACTCHECK=1
SHORTLY_MAX_SHORTS=30
# CapCut 내보내기 나레이션 TTS
TYPECAST_API_KEY=
"""
    (out_dir / ".env").write_text(text, encoding="utf-8")
    (out_dir / ".env.example").write_text(text, encoding="utf-8")


def _write_run_bat(out_dir: Path) -> None:
    bat = r"""@echo off
chcp 65001 >nul
cd /d "%~dp0"

where py >nul 2>&1
if %ERRORLEVEL%==0 (
  set "PY=py -3"
) else (
  where python >nul 2>&1
  if %ERRORLEVEL%==0 (
    set "PY=python"
  ) else (
    echo [오류] Python 이 설치되어 있지 않습니다.
    echo https://www.python.org/downloads/ 에서 Python 3.10 이상을 설치한 뒤
    echo 설치 화면에서 "Add python.exe to PATH" 를 체크하세요.
    pause
    exit /b 1
  )
)

if not exist ".venv\Scripts\python.exe" (
  echo [1/2] 가상환경 생성 중...
  %PY% -m venv .venv
  if errorlevel 1 (
    echo 가상환경 생성 실패
    pause
    exit /b 1
  )
  echo [2/2] 필수 패키지 설치 중 (처음 한 번, 몇 분 걸릴 수 있어요)...
  ".venv\Scripts\python.exe" -m pip install --upgrade pip
  ".venv\Scripts\python.exe" -m pip install -r requirements.txt
  if errorlevel 1 (
    echo 패키지 설치 실패
    pause
    exit /b 1
  )
)

echo shortly 포터블 실행 중...
echo 브라우저에서 http://127.0.0.1:8756 을 열어주세요.
echo 종료하려면 이 창에서 Ctrl+C 를 누르세요.
echo.
".venv\Scripts\python.exe" -m shortly.apps.team.launcher
pause
"""
    (out_dir / "실행.bat").write_text(bat, encoding="utf-8")
    # ASCII fallback name for some environments
    (out_dir / "run.bat").write_text(bat, encoding="utf-8")


def _write_readme(out_dir: Path) -> None:
    text = f"""shortly 팀용 포터블 버전
========================

이 폴더는 개발용이 아닌 팀원용 포터블 패키지입니다.

고정된 AI
---------
- 채널 학습: {PORTABLE_ANALYSIS_MODEL} (Claude Sonnet 5)
- 대본/가이드 생성: {PORTABLE_GENERATION_MODEL} (Claude Opus 5)
- 효과음 기능: 없음

시작 방법
---------
1) Python 3.10 이상이 설치되어 있어야 합니다.
2) "실행.bat" (또는 run.bat) 을 더블클릭합니다.
3) 처음 실행 시 패키지 설치가 진행됩니다 (몇 분).
4) 브라우저에서 http://127.0.0.1:8756 을 엽니다.
5) 설정에서 Anthropic(Claude) API 키를 넣고 저장합니다.

업데이트
---------
새 포터블 zip/폴더를 받으면:
- data/ 폴더와 .env 파일은 그대로 두세요 (학습 결과·API 키 유지).
- 그 외 파일(shortly/, requirements.txt, 실행.bat 등)만 덮어쓰세요.

필요 프로그램
-------------
- Python 3.10+
- ffmpeg (영상 처리용, PATH 에 있어야 함)
  https://ffmpeg.org/download.html

폴더 안내
---------
- data/        학습·생성 결과 (업데이트 시 유지)
- tts_output/  TTS 출력 (있으면)
- .env         API 키·설정 (업데이트 시 유지)
"""
    (out_dir / "사용법.txt").write_text(text, encoding="utf-8")


def _write_requirements(out_dir: Path) -> None:
    """포터블용 requirements — Claude + 핵심 분석 의존성 (효과음/로컬 LLM 제외)."""
    text = """# shortly 팀용 포터블
fastapi>=0.110
uvicorn>=0.29
numpy>=1.24
scipy>=1.10
Pillow>=10.0
yt-dlp>=2024.1.0
opencv-python>=4.8
scenedetect>=0.6.3
faster-whisper>=1.0.0
easyocr>=1.7.0
anthropic>=0.25.0
ddgs>=9.0.0
"""
    (out_dir / "requirements.txt").write_text(text, encoding="utf-8")


def build_portable(
    out_dir: Optional[Path] = None,
    progress: Progress = _noop,
    clean: bool = True,
) -> dict:
    """팀용 포터블 폴더를 생성합니다.

    기본 출력: <프로젝트>/dist/shortly-portable/
    """
    out = Path(out_dir) if out_dir else PROJECT_ROOT / "dist" / "shortly-portable"
    out = out.resolve()

    progress(f"포터블 출력 폴더: {out}", None, None)
    if out.exists() and clean:
        # data/.env 는 사용자가 쓸 수 있으므로 빌드 산출물은 통째로 재생성
        progress("기존 포터블 폴더 삭제 중...", None, None)
        shutil.rmtree(out)
    out.mkdir(parents=True, exist_ok=True)

    progress("소스 복사 중...", None, None)
    # 패키지
    n = _copy_team_package(out)
    # 메타
    for name in ("pyproject.toml",):
        src = PROJECT_ROOT / name
        if src.is_file():
            shutil.copy2(src, out / name)
            n += 1

    progress("포터블 설정·실행 파일 작성 중...", None, None)
    _write_env(out)
    _write_run_bat(out)
    _write_readme(out)
    _write_requirements(out)

    (out / "data").mkdir(exist_ok=True)
    (out / "data" / "channels").mkdir(exist_ok=True)
    (out / "tts_output").mkdir(exist_ok=True)
    (out / "data" / ".gitkeep").write_text("", encoding="utf-8")

    progress("포터블 빌드 완료", None, None)
    return {
        "ok": True,
        "output_dir": str(out),
        "files_copied": n,
        "analysis_model": PORTABLE_ANALYSIS_MODEL,
        "generation_model": PORTABLE_GENERATION_MODEL,
        "hint": "실행.bat 을 더블클릭하세요. data/ 와 .env 는 업데이트 시 유지하세요.",
    }
