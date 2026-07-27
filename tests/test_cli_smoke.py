from __future__ import annotations

import argparse
import subprocess
import sys
import unittest

from support import ROOT

from shortly.cli import build_parser


EXPECTED_COMMANDS = {
    "build-sfx",
    "learn",
    "guide",
    "list",
    "status",
    "web",
    "build-portable",
    "build-installer",
}


class CliSmokeTests(unittest.TestCase):
    def test_parser_exposes_the_eight_baseline_commands(self) -> None:
        parser = build_parser()
        subparsers = [
            action
            for action in parser._actions
            if isinstance(action, argparse._SubParsersAction)
        ]
        self.assertEqual(len(subparsers), 1)
        self.assertEqual(set(subparsers[0].choices), EXPECTED_COMMANDS)

    def test_help_runs_without_network_or_user_data(self) -> None:
        code = (
            "import sys;"
            f"sys.path.insert(0, {str(ROOT)!r});"
            "from shortly.cli import main;"
            "main(['--help'])"
        )
        result = subprocess.run(
            [sys.executable, "-c", code],
            cwd=ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        for command in EXPECTED_COMMANDS:
            self.assertIn(command, result.stdout)


if __name__ == "__main__":
    unittest.main()
