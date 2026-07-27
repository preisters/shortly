"""Product identity and capability policy for the shared Shortly codebase."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional


class ProductKind(str, Enum):
    """Supported runtime compositions.

    LEGACY preserves the behavior that existed before product separation.
    TEAM and PERSONAL are explicit profiles used by their launchers.
    """

    TEAM = "team"
    PERSONAL = "personal"
    LEGACY = "legacy"


@dataclass(frozen=True)
class FeatureManifest:
    """Capabilities a product profile is allowed to register.

    These flags describe the product boundary, not whether a future feature has
    already been implemented.
    """

    local_llm: bool
    sfx: bool
    generative_assets: bool
    generated_video: bool
    personal_automation: bool

    def allows(self, feature: str) -> bool:
        if feature not in self.__dataclass_fields__:
            return False
        return bool(getattr(self, feature))


_FEATURES = {
    ProductKind.TEAM: FeatureManifest(
        local_llm=False,
        sfx=False,
        generative_assets=False,
        generated_video=False,
        personal_automation=False,
    ),
    ProductKind.PERSONAL: FeatureManifest(
        local_llm=True,
        sfx=True,
        generative_assets=True,
        generated_video=True,
        personal_automation=True,
    ),
    ProductKind.LEGACY: FeatureManifest(
        local_llm=True,
        sfx=True,
        generative_assets=False,
        generated_video=False,
        personal_automation=False,
    ),
}


def feature_manifest_for(product: ProductKind) -> FeatureManifest:
    return _FEATURES[ProductKind(product)]


def resolve_product_kind(
    value: Optional[str],
    *,
    portable: bool = False,
) -> ProductKind:
    """Resolve an explicit product value while preserving portable safety.

    A portable launcher always resolves to TEAM, even if an inherited
    environment accidentally contains another product value.
    """

    if portable:
        return ProductKind.TEAM
    normalized = (value or "").strip().lower()
    if not normalized:
        return ProductKind.LEGACY
    try:
        return ProductKind(normalized)
    except ValueError as exc:
        allowed = ", ".join(kind.value for kind in ProductKind)
        raise ValueError(
            f"지원하지 않는 SHORTLY_PRODUCT={value!r}; 허용값: {allowed}"
        ) from exc
