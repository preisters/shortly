from __future__ import annotations

import unittest

from support import ROOT  # noqa: F401 - ensures repository root is importable

from shortly.product_profile import (
    ProductKind,
    feature_manifest_for,
    resolve_product_kind,
)


class ProductProfileTests(unittest.TestCase):
    def test_missing_product_preserves_legacy_profile(self) -> None:
        self.assertIs(resolve_product_kind(None), ProductKind.LEGACY)
        self.assertIs(resolve_product_kind(""), ProductKind.LEGACY)

    def test_portable_mode_always_resolves_to_team(self) -> None:
        self.assertIs(
            resolve_product_kind("personal", portable=True),
            ProductKind.TEAM,
        )

    def test_explicit_profiles_are_case_insensitive(self) -> None:
        self.assertIs(resolve_product_kind(" TEAM "), ProductKind.TEAM)
        self.assertIs(resolve_product_kind("Personal"), ProductKind.PERSONAL)

    def test_invalid_profile_has_actionable_error(self) -> None:
        with self.assertRaisesRegex(ValueError, "허용값"):
            resolve_product_kind("unknown")

    def test_team_manifest_denies_personal_capabilities(self) -> None:
        features = feature_manifest_for(ProductKind.TEAM)
        for feature in (
            "local_llm",
            "sfx",
            "generative_assets",
            "generated_video",
            "personal_automation",
        ):
            self.assertFalse(features.allows(feature))
        self.assertFalse(features.allows("not_registered"))

    def test_personal_and_legacy_manifests_are_distinct(self) -> None:
        personal = feature_manifest_for(ProductKind.PERSONAL)
        legacy = feature_manifest_for(ProductKind.LEGACY)
        self.assertTrue(personal.local_llm)
        self.assertTrue(personal.personal_automation)
        self.assertTrue(legacy.local_llm)
        self.assertFalse(legacy.personal_automation)


if __name__ == "__main__":
    unittest.main()
