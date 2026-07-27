"""앱 루트 진입점 — 임베디드 Python 이 shortly 패키지를 못 찾는 문제를 막습니다.

사용: runtime\\python\\python.exe run_shortly.py
"""

from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from shortly.portable.launcher import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
