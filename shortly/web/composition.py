"""Request-scoped application composition for shared web route functions."""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass
from typing import Any, Iterator, Optional

from ..product_profile import FeatureManifest, ProductKind


@dataclass(frozen=True)
class AppComposition:
    product: ProductKind
    features: FeatureManifest
    service: Any


_CURRENT: ContextVar[Optional[AppComposition]] = ContextVar(
    "shortly_app_composition",
    default=None,
)


def current_composition() -> Optional[AppComposition]:
    return _CURRENT.get()


def bind_composition(composition: AppComposition) -> Token:
    return _CURRENT.set(composition)


def reset_composition(token: Token) -> None:
    _CURRENT.reset(token)


@contextmanager
def composition_scope(composition: AppComposition) -> Iterator[None]:
    token = bind_composition(composition)
    try:
        yield
    finally:
        reset_composition(token)


class ServiceProxy:
    """Resolve the injected service for this request, or the legacy default."""

    def __init__(self, default_service: Any) -> None:
        self._default_service = default_service

    def resolve(self) -> Any:
        composition = current_composition()
        if composition is None:
            return self._default_service
        return composition.service

    def __getattr__(self, name: str) -> Any:
        return getattr(self.resolve(), name)
