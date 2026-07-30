"""Personal FastAPI composition root."""

from __future__ import annotations

import os
import ipaddress
from typing import Any, Optional

from ... import service as default_service
from ...product_profile import ProductKind


def create_app(
    *,
    service_api: Optional[Any] = None,
    base_settings=None,
    configure_environment: bool = True,
):
    """Create the personal HTTP surface.

    Explicit personal launchers configure the personal product environment.
    The legacy-compatible ``shortly web`` command can opt out so its existing
    learning data, saved guides, and settings paths remain unchanged while the
    personal-only automation routes are added.
    """

    if configure_environment:
        os.environ["SHORTLY_PRODUCT"] = ProductKind.PERSONAL.value
        os.environ["SHORTLY_PORTABLE"] = "0"
    from ...web.factory import create_product_app
    from .automation.api import create_automation_components, create_router

    app = create_product_app(ProductKind.PERSONAL, service_api=service_api)
    automation_settings, store, executor = create_automation_components(
        base_settings=base_settings,
    )
    # Extend concrete routes so route manifests and artifact-boundary tests can
    # inspect the personal surface on FastAPI versions that defer include_router.
    automation_router = create_router(
        automation_settings,
        store,
        executor,
        service_api=service_api if service_api is not None else default_service,
    )
    app.router.routes.extend(automation_router.routes)
    app.state.personal_automation = {
        "settings": automation_settings,
        "store": store,
        "executor": executor,
    }

    @app.middleware("http")
    async def _personal_automation_loopback_only(request, call_next):
        if request.url.path.startswith("/api/personal/"):
            client = request.client.host if request.client else ""
            try:
                allowed = ipaddress.ip_address(client).is_loopback
            except ValueError:
                allowed = False
            if not allowed:
                from fastapi.responses import JSONResponse

                return JSONResponse(
                    status_code=403,
                    content={
                        "detail": (
                            "개인 자동 생성 API는 비용과 개인 파일 보호를 위해 "
                            "이 PC에서만 사용할 수 있습니다."
                        )
                    },
                )
        return await call_next(request)

    return app
