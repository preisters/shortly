from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from support import load_fixture

from shortly.generate.capcut_export import export_capcut_draft


class CapcutDraftContractTests(unittest.TestCase):
    def test_minimal_guide_matches_versioned_golden_draft(self) -> None:
        guide = load_fixture("guide.min.json")
        expected = load_fixture("capcut_draft.expected.json")
        ids = [f"fixture-id-{index:02d}" for index in range(1, 9)]

        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "capcut-draft.json"
            with patch(
                "shortly.generate.capcut_export._uid",
                side_effect=ids,
            ):
                actual = export_capcut_draft(guide, output)
            saved = json.loads(output.read_text(encoding="utf-8"))

        self.assertEqual(actual, expected)
        self.assertEqual(saved, expected)
        self.assertEqual(actual["shortly_schema"], "capcut-draft-v0")
        self.assertEqual(actual["canvas_config"]["ratio"], "9:16")


if __name__ == "__main__":
    unittest.main()
