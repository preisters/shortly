"""shortly 커맨드라인 인터페이스."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .config import get_settings
from . import service


def _force_utf8_output() -> None:
    """Windows 콘솔(cp949 등)에서 이모지·다국어 출력 시 UnicodeEncodeError 방지."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001
            pass


def _progress(msg: str, cur=None, total=None) -> None:
    if cur is not None and total:
        print(f"  ({cur}/{total}) {msg}")
    else:
        print(f"  {msg}")


def cmd_build_sfx(args) -> int:
    settings = get_settings()
    res = service.build_sfx_index(settings, progress=_progress,
                                  max_items=args.max_items, probe=not args.no_probe)
    print(res)
    return 0 if res.get("ok") else 1


def cmd_learn(args) -> int:
    settings = get_settings()
    res = service.learn_channel(
        args.channel, settings, progress=_progress,
        max_count=args.max, min_views=args.min_views, do_ocr=not args.no_ocr,
        separate=args.separate_vocals, force=args.force,
    )
    if not res.get("ok"):
        print(res)
        return 1
    print(f"\n학습 완료: {res['analyzed']}개 분석")
    print(f"프로파일: {settings.channels_dir / res['slug'] / 'profile.json'}")
    print(f"리포트:  {settings.channels_dir / res['slug'] / 'report.md'}")
    return 0


def cmd_guide(args) -> int:
    settings = get_settings()
    text = args.text
    if args.file:
        text = Path(args.file).read_text(encoding="utf-8")
    res = service.generate_guide(args.channel, text, is_script=args.script,
                                 settings=settings, progress=_progress)
    if not res.get("ok"):
        print(res)
        return 1
    print("\n" + res["guide_md"])
    print(f"\n출력 폴더: {res['output_dir']}")
    return 0


def cmd_list(args) -> int:
    for c in service.list_channels():
        mark = "[학습됨]" if c["learned"] else "[미학습]"
        print(f"{mark} {c['input']} (쇼츠 {c['count']}개, slug={c['slug']})")
    return 0


def cmd_status(args) -> int:
    print(service.sfx_index_status())
    return 0


def cmd_web(args) -> int:
    from .web.app import run
    run(host=args.host, port=args.port)
    return 0


def cmd_build_portable(args) -> int:
    """레거시: 런타임 없는 폴더만 생성. 팀 배포는 build-installer 권장."""
    from .portable import build_portable
    out = Path(args.out) if args.out else None
    res = build_portable(out_dir=out, progress=_progress, clean=not args.keep)
    if not res.get("ok"):
        print(res)
        return 1
    print(f"\n포터블 빌드 완료: {res['output_dir']}")
    print("  ※ 팀원용은 Python/ffmpeg 포함 `build-installer` 를 쓰세요.")
    print(f"  학습 AI: {res['analysis_model']}")
    print(f"  생성 AI: {res['generation_model']}")
    print(f"  {res.get('hint', '')}")
    return 0


def cmd_build_installer(args) -> int:
    from .portable import build_installer
    out = Path(args.out) if args.out else None
    cache = Path(args.cache) if args.cache else None
    res = build_installer(
        out_dir=out,
        progress=_progress,
        clean=not args.keep,
        skip_runtime=args.skip_runtime,
        skip_compile=args.skip_compile,
        cache_dir=cache,
    )
    if not res.get("ok"):
        print(res)
        return 1
    print(f"\n설치형 빌드 완료: {res['app_dir']}")
    if res.get("setup_exe"):
        print(f"  Setup.exe: {res['setup_exe']}")
    else:
        print(f"  Inno 스크립트: {res.get('iss_path')}")
        print("  (Setup.exe 없음 → shortly-app 폴더를 zip 으로 배포해도 됩니다)")
    print(f"  학습 AI: {res['analysis_model']}")
    print(f"  생성 AI: {res['generation_model']}")
    print(f"  {res.get('hint', '')}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="shortly",
                                description="유튜브 쇼츠 스타일 분석·제작 가이드 생성 툴")
    sub = p.add_subparsers(dest="command", required=True)

    sp = sub.add_parser("build-sfx", help="효과음 라이브러리로 지문 인덱스 구축")
    sp.add_argument("--max-items", type=int, default=None, help="인덱싱할 최대 효과음 수")
    sp.add_argument("--no-probe", action="store_true", help="ffprobe 길이 측정 생략")
    sp.set_defaults(func=cmd_build_sfx)

    sp = sub.add_parser("learn", help="채널 쇼츠 학습")
    sp.add_argument("channel", help="채널 URL/핸들 (예: @handle 또는 채널 URL)")
    sp.add_argument("--max", type=int, default=None, help="학습할 최대 쇼츠 수(조회수 높은 순)")
    sp.add_argument("--min-views", type=int, default=None, help="이 조회수 이상만 학습")
    sp.add_argument("--no-ocr", action="store_true", help="영상 속 자막 글자 읽기 생략")
    sp.add_argument("--separate-vocals", action="store_true",
                    help="효과음 매칭 정확도 향상(음성 제거 후 분석, 느림)")
    sp.add_argument("--force", action="store_true", help="이미 분석한 영상도 다시 분석")
    sp.set_defaults(func=cmd_learn)

    sp = sub.add_parser("guide", help="학습된 스타일로 제작 가이드 생성")
    sp.add_argument("channel", help="학습된 채널 URL/핸들")
    sp.add_argument("text", nargs="?", default="", help="주제 또는 대본 텍스트")
    sp.add_argument("--file", help="대본/주제를 담은 파일 경로")
    sp.add_argument("--script", action="store_true", help="입력을 완성 대본으로 취급(스타일 보정)")
    sp.set_defaults(func=cmd_guide)

    sp = sub.add_parser("list", help="학습된 채널 목록")
    sp.set_defaults(func=cmd_list)

    sp = sub.add_parser("status", help="효과음 인덱스 상태")
    sp.set_defaults(func=cmd_status)

    settings = get_settings()
    sp = sub.add_parser("web", help="로컬 웹 UI 실행")
    sp.add_argument("--host", default=settings.host)
    sp.add_argument("--port", type=int, default=settings.port)
    sp.set_defaults(func=cmd_web)

    sp = sub.add_parser(
        "build-portable",
        help="(레거시) 런타임 없는 포터블 폴더 — 팀 배포는 build-installer 권장",
    )
    sp.add_argument(
        "--out",
        default=None,
        help="출력 폴더 (기본: dist/shortly-portable)",
    )
    sp.add_argument(
        "--keep",
        action="store_true",
        help="기존 출력 폴더를 지우지 않고 덮어씀",
    )
    sp.set_defaults(func=cmd_build_portable)

    sp = sub.add_parser(
        "build-installer",
        help="팀용 설치형 빌드 (Python·ffmpeg 포함, Sonnet5/Opus4.8 고정)",
    )
    sp.add_argument(
        "--out",
        default=None,
        help="앱 출력 폴더 (기본: dist/shortly-app)",
    )
    sp.add_argument(
        "--cache",
        default=None,
        help="다운로드 캐시 폴더 (기본: dist/cache)",
    )
    sp.add_argument(
        "--keep",
        action="store_true",
        help="기존 출력 폴더를 지우지 않음",
    )
    sp.add_argument(
        "--skip-runtime",
        action="store_true",
        help="Python/ffmpeg 다운로드·설치 생략 (구조만 갱신)",
    )
    sp.add_argument(
        "--skip-compile",
        action="store_true",
        help="Inno Setup Setup.exe 컴파일 생략",
    )
    sp.set_defaults(func=cmd_build_installer)

    return p


def main(argv=None) -> int:
    _force_utf8_output()
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
