"""팀용 Windows 설치형(런타임 포함) 빌드.

결과물:
1) dist/shortly-app/  — Python·ffmpeg 포함, 더블클릭으로 실행 (설치 불필요)
2) dist/ShortlySetup.iss — Inno Setup 스크립트
3) ISCC 있으면 dist/ShortlySetup.exe 컴파일
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Callable, Optional

from ..config import (
    PORTABLE_ANALYSIS_MODEL,
    PORTABLE_GENERATION_MODEL,
    PROJECT_ROOT,
)
from .build import _copy_team_package, _write_requirements
from .runtime import setup_embedded_python, setup_ffmpeg, verify_runtime

Progress = Callable[[str, Optional[int], Optional[int]], None]


def _noop(msg: str, cur: Optional[int] = None, total: Optional[int] = None) -> None:
    pass


def _write_env_defaults(out_dir: Path) -> None:
    text = f"""# shortly 팀용 기본 설정 (API 키는 웹에서 입력 → AppData에 저장)
SHORTLY_PORTABLE=1
SHORTLY_LLM_PROVIDER=anthropic
SHORTLY_TEXT_MODEL={PORTABLE_ANALYSIS_MODEL}
SHORTLY_VISION_MODEL={PORTABLE_ANALYSIS_MODEL}
SHORTLY_GEN_PROVIDER=anthropic
SHORTLY_GEN_MODEL={PORTABLE_GENERATION_MODEL}
SHORTLY_HOST=127.0.0.1
SHORTLY_PORT=8756
SHORTLY_LAN_ONLY=1
SHORTLY_RESEARCH=1
SHORTLY_FACTCHECK=1
SHORTLY_MAX_SHORTS=30
ANTHROPIC_API_KEY=
# CapCut 내보내기 나레이션 TTS (https://typecast.ai/developers/api/api-key)
TYPECAST_API_KEY=
"""
    (out_dir / "env.defaults").write_text(text, encoding="utf-8")


def _write_start_scripts(out_dir: Path) -> None:
    # 앱 루트 부트스트랩 (임베디드 Python 의 ._pth 가 cwd 를 무시해도 동작)
    bootstrap = '''"""shortly 앱 진입점 — 이 파일을 앱 루트에 둡니다."""
from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from shortly.portable.launcher import main

if __name__ == "__main__":
    raise SystemExit(main())
'''
    (out_dir / "run_shortly.py").write_text(bootstrap, encoding="utf-8")

    # 콘솔 없는 시작 (팀원용) — 실패 시 메시지 표시
    vbs = '''Set fso = CreateObject("Scripting.FileSystemObject")
Set sh = CreateObject("WScript.Shell")
app = fso.GetParentFolderName(WScript.ScriptFullName)
sh.CurrentDirectory = app
pyw = app & "\\runtime\\python\\pythonw.exe"
boot = app & "\\run_shortly.py"
If Not fso.FileExists(pyw) Then
  MsgBox "실행 파일이 없습니다." & vbCrLf & pyw, 16, "shortly"
  WScript.Quit 1
End If
If Not fso.FileExists(boot) Then
  MsgBox "run_shortly.py 가 없습니다." & vbCrLf & boot, 16, "shortly"
  WScript.Quit 1
End If
' 0=숨김창, True=종료까지 대기하지 않음
sh.Run """" & pyw & """ """ & boot & """", 0, False
'''
    (out_dir / "Shortly.vbs").write_text(vbs, encoding="utf-8")
    (out_dir / "Shortly 시작.vbs").write_text(vbs, encoding="utf-8")

    # 오류 확인용 콘솔 시작
    bat = r"""@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo shortly 시작 중...
echo 브라우저가 자동으로 열립니다. 이 창을 닫으면 프로그램이 종료됩니다.
echo.
"runtime\python\python.exe" "%~dp0run_shortly.py"
if errorlevel 1 (
  echo.
  echo 실행에 실패했습니다. 위 오류 메시지를 관리자에게 보내 주세요.
  pause
)
"""
    (out_dir / "Shortly-디버그.bat").write_text(bat, encoding="utf-8")

    # USB 등에서 프로세스가 남을 때 강제 종료
    kill = r"""@echo off
chcp 65001 >nul
echo shortly 관련 프로세스를 종료합니다...
taskkill /F /IM pythonw.exe /T >nul 2>&1
taskkill /F /IM python.exe /T >nul 2>&1
echo 완료. 이제 USB 를 안전하게 제거할 수 있는지 확인해 보세요.
pause
"""
    (out_dir / "Shortly-종료.bat").write_text(kill, encoding="utf-8")


def _write_readme(out_dir: Path) -> None:
    text = f"""shortly 팀용 앱 (설치형 / 런타임 포함)
=====================================

이 폴더에는 Python 과 ffmpeg 가 이미 들어 있습니다.
별도로 설치할 필요 없습니다.

시작
----
1) "Shortly 시작.vbs" (또는 Shortly.vbs) 를 더블클릭
2) 잠시 후 브라우저가 자동으로 열립니다
3) 설정에서 Claude API 키 + Typecast TTS API 키를 넣고 저장
4) 가이드 생성 후 "CapCut 프로젝트로 내보내기" → TTS 음성 포함

고정 AI
-------
- 학습: {PORTABLE_ANALYSIS_MODEL} (Sonnet 5)
- 대본 생성: {PORTABLE_GENERATION_MODEL} (Opus 5)

내 데이터 위치 (업데이트해도 유지됨)
------------------------------------
%LOCALAPPDATA%\\Shortly\\
  .env          API 키
  data\\         학습·가이드 결과
  tts_output\\   음성 파일

업데이트
--------
새 버전 폴더/설치 파일로 프로그램을 교체하세요.
위 데이터 폴더는 지우지 않으면 그대로 남습니다.

문제가 있을 때
--------------
"Shortly-디버그.bat" 을 실행해 검은 창의 오류를 확인하세요.
USB 가 "사용 중"이라 안 빠지면 "Shortly-종료.bat" 을 실행하세요.
"""
    (out_dir / "사용법.txt").write_text(text, encoding="utf-8")


def _write_inno_iss(app_dir: Path, iss_path: Path, version: str = "0.1.0") -> None:
    app_dir_str = str(app_dir).replace("/", "\\")
    out_base = str(iss_path.parent).replace("/", "\\")
    # Inno 는 {app} 문법을 쓰므로 Python format 과 충돌 없게 치환만 사용
    iss = r"""
; shortly 팀용 설치 스크립트 — Inno Setup 6
; 컴파일: ISCC.exe ShortlySetup.iss

#define MyAppName "shortly"
#define MyAppVersion "VERSION_PLACEHOLDER"
#define MyAppPublisher "shortly"
#define MyAppExeName "Shortly 시작.vbs"

[Setup]
AppId={{A7E8C2D1-4B3F-4E9A-9C1D-8F0E2B7A6C11}}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={localappdata}\Programs\Shortly
DefaultGroupName=shortly
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=OUTPUT_DIR_PLACEHOLDER
OutputBaseFilename=ShortlySetup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
UninstallDisplayName=shortly
ArchitecturesInstallIn64BitMode=x64compatible

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a desktop icon"; GroupDescription: "Additional icons:"

[Files]
Source: "APP_DIR_PLACEHOLDER\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\shortly"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"
Name: "{autodesktop}\shortly"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Launch shortly"; Flags: nowait postinstall skipifsilent shellexec
""".lstrip()
    iss = (iss
           .replace("VERSION_PLACEHOLDER", version)
           .replace("OUTPUT_DIR_PLACEHOLDER", out_base)
           .replace("APP_DIR_PLACEHOLDER", app_dir_str))
    iss_path.write_text(iss, encoding="utf-8")


def _find_iscc() -> Optional[Path]:
    candidates = [
        Path(r"C:\Program Files (x86)\Inno Setup 6\ISCC.exe"),
        Path(r"C:\Program Files\Inno Setup 6\ISCC.exe"),
    ]
    for c in candidates:
        if c.is_file():
            return c
    which = shutil.which("ISCC") or shutil.which("ISCC.exe")
    return Path(which) if which else None


def build_installer(
    out_dir: Optional[Path] = None,
    progress: Progress = _noop,
    clean: bool = True,
    skip_runtime: bool = False,
    skip_compile: bool = False,
    cache_dir: Optional[Path] = None,
) -> dict:
    """런타임 포함 앱 폴더 + (가능하면) Setup.exe 생성."""
    out = Path(out_dir) if out_dir else PROJECT_ROOT / "dist" / "shortly-app"
    out = out.resolve()
    cache = Path(cache_dir) if cache_dir else PROJECT_ROOT / "dist" / "cache"
    cache = cache.resolve()

    progress(f"설치형 앱 출력: {out}", None, None)
    if out.exists() and clean:
        progress("기존 앱 폴더 삭제 중...", None, None)
        shutil.rmtree(out)
    out.mkdir(parents=True, exist_ok=True)

    progress("앱 소스 복사 중...", None, None)
    n = _copy_team_package(out)
    for name in ("pyproject.toml",):
        src = PROJECT_ROOT / name
        if src.is_file():
            shutil.copy2(src, out / name)
            n += 1

    _write_requirements(out)
    _write_env_defaults(out)
    _write_start_scripts(out)
    _write_readme(out)

    py_exe = out / "runtime" / "python" / "python.exe"
    ff_bin = out / "runtime" / "ffmpeg" / "bin"

    if skip_runtime:
        progress("런타임 생략 (--skip-runtime). 기존 runtime 이 있어야 실행됩니다.", None, None)
        (out / "runtime").mkdir(exist_ok=True)
    else:
        progress("임베디드 Python 구성...", None, None)
        py_exe = setup_embedded_python(
            out / "runtime",
            out / "requirements.txt",
            cache,
            out,
            progress=progress,
        )
        progress("ffmpeg 구성...", None, None)
        ff_bin = setup_ffmpeg(out / "runtime", cache, progress=progress)
        try:
            verify_runtime(py_exe, ff_bin, progress=progress)
        except Exception as exc:  # noqa: BLE001
            progress(f"경고: 런타임 검증 일부 실패 — {exc}", None, None)

    iss_path = PROJECT_ROOT / "dist" / "ShortlySetup.iss"
    iss_path.parent.mkdir(parents=True, exist_ok=True)
    _write_inno_iss(out, iss_path)
    progress(f"Inno Setup 스크립트: {iss_path}", None, None)

    setup_exe = None
    iscc = _find_iscc()
    if skip_compile:
        progress("Setup.exe 컴파일 생략 (--skip-compile)", None, None)
    elif iscc is None:
        progress(
            "Inno Setup(ISCC) 없음 — Setup.exe 는 건너뜁니다. "
            "https://jrsoftware.org/isinfo.php 설치 후 다시 빌드하거나 "
            "shortly-app 폴더를 zip 으로 배포하세요.",
            None, None,
        )
    else:
        progress("Setup.exe 컴파일 중...", None, None)
        subprocess.check_call([str(iscc), str(iss_path)])
        candidate = PROJECT_ROOT / "dist" / "ShortlySetup.exe"
        if candidate.is_file():
            setup_exe = str(candidate)
            progress(f"Setup.exe 생성: {setup_exe}", None, None)

    progress("설치형 빌드 완료", None, None)
    return {
        "ok": True,
        "app_dir": str(out),
        "setup_exe": setup_exe,
        "iss_path": str(iss_path),
        "files_copied": n,
        "analysis_model": PORTABLE_ANALYSIS_MODEL,
        "generation_model": PORTABLE_GENERATION_MODEL,
        "hint": (
            "팀원에게 ShortlySetup.exe 를 주거나, shortly-app 폴더를 zip 으로 주세요. "
            "더블클릭 → API 키 입력만 하면 됩니다. Python/ffmpeg 설치 불필요."
        ),
    }
