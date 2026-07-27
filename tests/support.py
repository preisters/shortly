from __future__ import annotations

import json
import os
import sys
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


ROOT = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).resolve().parent / "fixtures"

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def load_fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


@contextmanager
def isolated_shortly_env(**updates: str) -> Iterator[None]:
    """Temporarily remove Shortly settings and apply explicit test values."""
    prefixes = (
        "SHORTLY_",
        "GEMINI_API_KEY",
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "TYPECAST_API_KEY",
    )
    before = {
        key: value
        for key, value in os.environ.items()
        if key.startswith(prefixes)
    }
    try:
        for key in list(os.environ):
            if key.startswith(prefixes):
                os.environ.pop(key, None)
        os.environ.update({key: str(value) for key, value in updates.items()})
        yield
    finally:
        for key in list(os.environ):
            if key.startswith(prefixes):
                os.environ.pop(key, None)
        os.environ.update(before)
