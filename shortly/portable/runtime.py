"""임베디드 Python + ffmpeg 다운로드 및 구성."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path
from typing import Callable, Optional
from urllib.request import urlretrieve

Progress = Callable[[str, Optional[int], Optional[int]], None]

PYTHON_VERSION = "3.12.10"
PYTHON_EMBED_URL = (
    f"https://www.python.org/ftp/python/{PYTHON_VERSION}/"
    f"python-{PYTHON_VERSION}-embed-amd64.zip"
)
GET_PIP_URL = "https://bootstrap.pypa.io/get-pip.py"
# gyan.dev 공식 essentials 빌드(윈도우)
FFMPEG_URL = "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip"


def _noop(msg: str, cur: Optional[int] = None, total: Optional[int] = None) -> None:
    pass


def _download(url: str, dest: Path, progress: Progress) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.is_file() and dest.stat().st_size > 1_000_000:
        progress(f"캐시 사용: {dest.name}", None, None)
        return
    progress(f"다운로드 중: {url}", None, None)
    tmp = dest.with_suffix(dest.suffix + ".part")
    try:
        urlretrieve(url, tmp)  # noqa: S310 — 고정 공식 URL
        tmp.replace(dest)
    finally:
        if tmp.is_file():
            try:
                tmp.unlink()
            except OSError:
                pass


def _extract_zip(zip_path: Path, dest: Path, progress: Progress) -> None:
    progress(f"압축 해제: {zip_path.name} → {dest.name}", None, None)
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path, "r") as zf:
        zf.extractall(dest)


def _enable_site_on_embed(python_dir: Path) -> None:
    """임베디드 Python 에서 pip/site-packages·앱 루트를 쓰도록 ._pth 수정.

    ._pth 가 있으면 cwd/PYTHONPATH 기본 탐색이 막힌다.
    python.exe 가 runtime/python/ 에 있으므로 ../.. 가 앱 루트(shortly 패키지 위치)다.
    """
    pth_files = list(python_dir.glob("python*._pth"))
    if not pth_files:
        raise RuntimeError(f"python*._pth 없음: {python_dir}")
    pth = pth_files[0]
    lines = pth.read_text(encoding="utf-8").splitlines()
    out: list[str] = []
    seen_site = False
    seen_lib = False
    seen_dot = False
    seen_app = False
    for line in lines:
        s = line.strip()
        if s.startswith("#") and "import site" in s:
            out.append("import site")
            seen_site = True
            continue
        if s == "import site":
            seen_site = True
        if s == "Lib\\site-packages" or s == "Lib/site-packages":
            seen_lib = True
        if s == ".":
            seen_dot = True
        if s in ("../..", "..\\.."):
            seen_app = True
        out.append(line)
    if not seen_dot:
        out.insert(0, ".")
    if not seen_app:
        # runtime/python → 앱 루트 (shortly/ 패키지)
        out.append("../..")
    if not seen_lib:
        out.append("Lib\\site-packages")
    if not seen_site:
        out.append("import site")
    pth.write_text("\n".join(out) + "\n", encoding="utf-8")


def setup_embedded_python(
    runtime_dir: Path,
    requirements: Path,
    cache_dir: Path,
    app_root: Path,
    progress: Progress = _noop,
) -> Path:
    """runtime/python 에 임베디드 Python + pip 패키지를 구성. python.exe 경로 반환."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    py_zip = cache_dir / f"python-{PYTHON_VERSION}-embed-amd64.zip"
    _download(PYTHON_EMBED_URL, py_zip, progress)

    python_dir = runtime_dir / "python"
    _extract_zip(py_zip, python_dir, progress)
    _enable_site_on_embed(python_dir)

    py_exe = python_dir / "python.exe"
    if not py_exe.is_file():
        raise RuntimeError(f"python.exe 없음: {python_dir}")

    get_pip = cache_dir / "get-pip.py"
    _download(GET_PIP_URL, get_pip, progress)
    progress("pip 설치 중...", None, None)
    subprocess.check_call([str(py_exe), str(get_pip), "--no-warn-script-location"],
                          cwd=str(python_dir))

    progress("팀용 패키지 설치 중 (수 분~수십 분 소요)...", None, None)
    subprocess.check_call([
        str(py_exe), "-m", "pip", "install", "--upgrade", "pip", "wheel", "setuptools",
    ])
    subprocess.check_call([
        str(py_exe), "-m", "pip", "install", "--no-warn-script-location",
        "-r", str(requirements),
    ])
    # shortly 패키지는 앱 루트에 두며, 실행 시 WorkingDir=앱 루트 + launcher 가 sys.path 에 추가.
    # 절대경로 .pth 는 다른 PC 설치 경로에서 깨지므로 쓰지 않음.
    _ = app_root  # 호출 시그니처 유지

    progress("임베디드 Python 준비 완료", None, None)
    return py_exe


def setup_ffmpeg(
    runtime_dir: Path,
    cache_dir: Path,
    progress: Progress = _noop,
) -> Path:
    """runtime/ffmpeg/bin 에 ffmpeg.exe 배치. bin 경로 반환."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    ff_zip = cache_dir / "ffmpeg-release-essentials.zip"
    _download(FFMPEG_URL, ff_zip, progress)

    extract_to = cache_dir / "ffmpeg_extract"
    if extract_to.exists():
        shutil.rmtree(extract_to)
    _extract_zip(ff_zip, extract_to, progress)

    # zip 루트에 ffmpeg-*-essentials_build/bin 구조
    bin_src = None
    for p in extract_to.rglob("ffmpeg.exe"):
        bin_src = p.parent
        break
    if bin_src is None:
        raise RuntimeError("ffmpeg.exe 를 zip 에서 찾지 못했습니다.")

    dest_bin = runtime_dir / "ffmpeg" / "bin"
    if dest_bin.exists():
        shutil.rmtree(dest_bin)
    dest_bin.mkdir(parents=True, exist_ok=True)
    for name in ("ffmpeg.exe", "ffprobe.exe", "ffplay.exe"):
        src = bin_src / name
        if src.is_file():
            shutil.copy2(src, dest_bin / name)
    if not (dest_bin / "ffmpeg.exe").is_file():
        raise RuntimeError("ffmpeg.exe 복사 실패")

    progress("ffmpeg 준비 완료", None, None)
    return dest_bin


def verify_runtime(python_exe: Path, ffmpeg_bin: Path, progress: Progress = _noop) -> None:
    subprocess.check_call([str(python_exe), "-c", "import fastapi, anthropic, cv2; print('ok')"])
    ff = ffmpeg_bin / "ffmpeg.exe"
    subprocess.check_call([str(ff), "-version"],
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    progress("런타임 검증 통과", None, None)
