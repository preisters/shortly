from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from support import ROOT, isolated_shortly_env

from shortly.path_policy import PathPolicy, documents_dir
from shortly.portable import launcher
from shortly.product_profile import ProductKind


class PathPolicyTests(unittest.TestCase):
    def test_team_and_personal_roots_are_separate(self) -> None:
        home = Path("C:/FixtureUser")
        local = Path("C:/FixtureLocal")
        documents = Path("C:/FixtureDocuments")
        env = {"LOCALAPPDATA": str(local)}

        team = PathPolicy.for_product(
            ProductKind.TEAM,
            project_root=ROOT,
            environ=env,
            home=home,
            documents=documents,
        )
        personal = PathPolicy.for_product(
            ProductKind.PERSONAL,
            project_root=ROOT,
            environ=env,
            home=home,
            documents=documents,
        )

        self.assertEqual(team.state_root, local / "Shortly")
        self.assertEqual(team.artifact_root, documents / "Shortly")
        self.assertEqual(personal.state_root, local / "ShortlyPersonal")
        self.assertEqual(
            personal.artifact_root,
            documents / "Shortly Personal",
        )
        self.assertNotEqual(team.state_root, personal.state_root)
        self.assertNotEqual(team.artifact_root, personal.artifact_root)

    def test_legacy_defaults_preserve_repository_paths(self) -> None:
        policy = PathPolicy.for_product(
            ProductKind.LEGACY,
            project_root=ROOT,
            environ={},
            home=Path("C:/FixtureUser"),
            documents=Path("C:/FixtureDocuments"),
        )
        self.assertEqual(policy.state_root, ROOT)
        self.assertEqual(policy.data_root, ROOT / "data")
        self.assertEqual(policy.artifact_root, ROOT)
        self.assertEqual(policy.env_file, ROOT / ".env")

    def test_explicit_path_overrides_win(self) -> None:
        env = {
            "SHORTLY_USER_DATA": "C:/Override/state",
            "SHORTLY_DATA_DIR": "C:/Override/data",
            "SHORTLY_ARTIFACT_DIR": "C:/Override/artifacts",
            "SHORTLY_LOG_DIR": "C:/Override/logs",
            "SHORTLY_TTS_OUTPUT_DIR": "C:/Override/tts",
        }
        policy = PathPolicy.for_product(
            ProductKind.TEAM,
            project_root=ROOT,
            environ=env,
            home=Path("C:/FixtureUser"),
            documents=Path("C:/FixtureDocuments"),
        )
        self.assertEqual(policy.state_root, Path("C:/Override/state"))
        self.assertEqual(policy.data_root, Path("C:/Override/data"))
        self.assertEqual(policy.artifact_root, Path("C:/Override/artifacts"))
        self.assertEqual(policy.log_root, Path("C:/Override/logs"))
        self.assertEqual(policy.tts_root, Path("C:/Override/tts"))

    def test_documents_prefers_windows_known_folder(self) -> None:
        known = Path("D:/Known/Documents")
        with patch(
            "shortly.path_policy._windows_known_documents",
            return_value=known,
        ):
            self.assertEqual(documents_dir(environ={}, home=Path("C:/Fallback")), known)

    def test_documents_has_user_profile_fallback(self) -> None:
        with patch(
            "shortly.path_policy._windows_known_documents",
            return_value=None,
        ):
            result = documents_dir(
                environ={"USERPROFILE": "C:/FixtureUser"},
                home=Path("D:/Ignored"),
            )
        self.assertEqual(result, Path("C:/FixtureUser") / "Documents")

    def test_portable_launcher_sets_team_profile_and_product_paths(self) -> None:
        previous_cwd = Path.cwd()
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            app_root = root / "app"
            state_parent = root / "local"
            artifacts = root / "documents" / "Shortly"
            app_root.mkdir()
            with isolated_shortly_env(
                SHORTLY_ARTIFACT_DIR=str(artifacts),
                SHORTLY_LOG_DIR=str(artifacts / "logs"),
            ):
                with patch.dict(
                    os.environ,
                    {"LOCALAPPDATA": str(state_parent)},
                    clear=False,
                ):
                    try:
                        user_data = launcher._prepare_env(app_root)
                        self.assertEqual(
                            os.environ["SHORTLY_PRODUCT"],
                            ProductKind.TEAM.value,
                        )
                        self.assertEqual(os.environ["SHORTLY_PORTABLE"], "1")
                        self.assertEqual(user_data, state_parent / "Shortly")
                        self.assertEqual(
                            Path(os.environ["SHORTLY_DATA_DIR"]),
                            user_data / "data",
                        )
                        self.assertEqual(
                            Path(os.environ["SHORTLY_TTS_OUTPUT_DIR"]),
                            artifacts / "tts",
                        )
                        self.assertTrue((artifacts / "logs").is_dir())
                    finally:
                        os.chdir(previous_cwd)


if __name__ == "__main__":
    unittest.main()
