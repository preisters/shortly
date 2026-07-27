"""Personal FastAPI composition root."""

from __future__ import annotations

import os
from typing import Any, Optional

from ...product_profile import ProductKind


def create_app(*, service_api: Optional[Any] = None):
    os.environ["SHORTLY_PRODUCT"] = ProductKind.PERSONAL.value
    os.environ["SHORTLY_PORTABLE"] = "0"
    from ...web.factory import create_product_app

    return create_product_app(ProductKind.PERSONAL, service_api=service_api)
