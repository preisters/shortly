from __future__ import annotations

import re
import subprocess
import unittest
from pathlib import Path

from support import ROOT


TEXT_SUFFIXES = {
    ".bat",
    ".cmd",
    ".css",
    ".html",
    ".ini",
    ".js",
    ".json",
    ".md",
    ".ps1",
    ".py",
    ".toml",
    ".txt",
    ".yaml",
    ".yml",
}


def git(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-c", f"safe.directory={ROOT.as_posix()}", *args],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        check=False,
    )


class RepositoryHygieneTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        result = git("ls-files")
        if result.returncode != 0:
            raise RuntimeError(result.stderr)
        cls.tracked = [line.strip() for line in result.stdout.splitlines() if line.strip()]

    def test_local_secrets_and_generated_roots_are_not_tracked(self) -> None:
        forbidden = [
            path
            for path in self.tracked
            if path == ".env"
            or path.startswith(("data/", "dist/"))
            or "__pycache__/" in path
            or ("/" not in path and path.startswith(("_tmp_", "_make_", "_plan_")))
            or path == "_tts_once.py"
        ]
        self.assertEqual(forbidden, [])

    def test_gitignore_covers_local_runtime_outputs(self) -> None:
        candidates = [
            ".env",
            "data/example.json",
            "dist/example.zip",
            "__pycache__/example.pyc",
            "_tmp_example.py",
        ]
        result = git("check-ignore", "--", *candidates)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(set(result.stdout.splitlines()), set(candidates))

    def test_no_known_credential_shapes_in_tracked_runtime_text(self) -> None:
        patterns = [
            re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"),
            re.compile(r"\b(?:ghp|gho|github_pat)_[A-Za-z0-9_]{20,}\b"),
            re.compile(r"\bAIza[A-Za-z0-9_-]{20,}\b"),
            re.compile(
                r"(?m)^(?:GEMINI_API_KEY|OPENAI_API_KEY|ANTHROPIC_API_KEY|"
                r"TYPECAST_API_KEY|PIXABAY_API_KEY|PEXELS_API_KEY)"
                r"[ \t]*=[ \t]*\S+"
            ),
        ]
        matches = []
        for relative in self.tracked:
            path = ROOT / relative
            if relative.startswith("docs/"):
                continue
            if path.suffix.lower() not in TEXT_SUFFIXES and relative != ".env.example":
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            for pattern in patterns:
                if pattern.search(text):
                    matches.append((relative, pattern.pattern))
        self.assertEqual(matches, [])

    def test_new_personal_absolute_paths_are_not_introduced(self) -> None:
        allowed_existing_debt = {
            ".env.example",
            "shortly/config.py",
        }
        personal_path = re.compile(
            r"(?i)(?:[a-z]:[\\/](?:users|youtube)[\\/])"
        )
        offenders = set()
        for relative in self.tracked:
            path = ROOT / relative
            if path.suffix.lower() != ".py" and relative != ".env.example":
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            if personal_path.search(text):
                offenders.add(relative)
        self.assertTrue(
            offenders <= allowed_existing_debt,
            f"unexpected tracked personal paths: {sorted(offenders - allowed_existing_debt)}",
        )


if __name__ == "__main__":
    unittest.main()
