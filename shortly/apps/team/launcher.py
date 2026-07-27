"""Team launcher compatibility entrypoint."""

from __future__ import annotations


def main() -> int:
    from ...portable.launcher import main as portable_main

    return portable_main()


if __name__ == "__main__":
    raise SystemExit(main())
