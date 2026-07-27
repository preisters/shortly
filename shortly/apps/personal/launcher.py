"""Personal local web application entrypoint."""

from __future__ import annotations

import os

from ...product_profile import ProductKind


def main() -> int:
    os.environ["SHORTLY_PRODUCT"] = ProductKind.PERSONAL.value
    os.environ["SHORTLY_PORTABLE"] = "0"

    from ...config import get_settings
    from .api import create_app

    settings = get_settings(reload=True)
    app = create_app()

    import uvicorn

    uvicorn.run(
        app,
        host=settings.host,
        port=settings.port,
        log_level="info",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
