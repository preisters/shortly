"""FastAPI factory for explicit Shortly product compositions."""

from __future__ import annotations

from typing import Any, Optional

from fastapi import FastAPI, Request

from .. import service as default_service
from ..product_profile import (
    ProductKind,
    feature_manifest_for,
)
from . import app as legacy_app
from .composition import (
    AppComposition,
    bind_composition,
    reset_composition,
)


_FRAMEWORK_ROUTES = {
    "/openapi.json",
    "/docs",
    "/docs/oauth2-redirect",
    "/redoc",
}


def route_manifest(app: FastAPI) -> tuple[tuple[str, str], ...]:
    rows = []
    for route in app.routes:
        path = getattr(route, "path", None)
        if path is None:
            continue
        methods = sorted(getattr(route, "methods", None) or ())
        if methods:
            rows.extend((method, path) for method in methods)
        else:
            rows.append(("MOUNT", path))
    return tuple(sorted(rows))


def create_product_app(
    product: ProductKind,
    *,
    service_api: Optional[Any] = None,
) -> FastAPI:
    """Build a distinct app object around the existing compatible routes."""

    product = ProductKind(product)
    composition = AppComposition(
        product=product,
        features=feature_manifest_for(product),
        service=service_api if service_api is not None else default_service,
    )
    app = FastAPI(title="shortly", version="0.1.0")
    # Keep framework-owned OpenAPI routes from the new app and reuse only the
    # established application routes. The legacy app remains untouched.
    app.router.routes.extend(
        route
        for route in legacy_app.app.router.routes
        if getattr(route, "path", None) not in _FRAMEWORK_ROUTES
    )
    app.state.shortly_composition = composition
    app.state.product = product.value
    app.state.features = composition.features

    # Preserve the existing LAN guard and add request-scoped service binding.
    app.middleware("http")(legacy_app._lan_only_guard)

    @app.middleware("http")
    async def _bind_product_composition(request: Request, call_next):
        token = bind_composition(composition)
        try:
            return await call_next(request)
        finally:
            reset_composition(token)

    return app
