"""Product-aware rendering for the shared web assets."""

from __future__ import annotations

from pathlib import Path

from ..product_profile import ProductKind


_LOCAL_START = "<!-- shortly:local-ui:start -->"
_LOCAL_END = "<!-- shortly:local-ui:end -->"
_LOCAL_SCRIPT_START = "/* shortly:local-script:start */"
_LOCAL_SCRIPT_END = "/* shortly:local-script:end */"


def render_index(path: Path, product: ProductKind) -> str:
    """Render the shared page while removing personal UI from team output."""

    html = path.read_text(encoding="utf-8")
    if ProductKind(product) is not ProductKind.TEAM:
        return html
    start = html.find(_LOCAL_START)
    end = html.find(_LOCAL_END)
    if start < 0 or end < 0 or end < start:
        raise RuntimeError("팀 UI를 생성할 로컬 기능 경계 마커가 없습니다.")
    end += len(_LOCAL_END)
    return html[:start] + html[end:]


def render_script(path: Path, product: ProductKind) -> str:
    """Remove every marked personal-only script block from team output."""

    script = path.read_text(encoding="utf-8")
    if ProductKind(product) is not ProductKind.TEAM:
        return script
    while True:
        start = script.find(_LOCAL_SCRIPT_START)
        if start < 0:
            break
        end = script.find(_LOCAL_SCRIPT_END, start)
        if end < 0:
            raise RuntimeError("팀 스크립트를 생성할 로컬 기능 경계가 닫히지 않았습니다.")
        end += len(_LOCAL_SCRIPT_END)
        script = script[:start] + script[end:]
    return script
