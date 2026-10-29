from __future__ import annotations

import ast
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from shortly.portable.build import build_portable


class TeamArtifactBoundaryTests(unittest.TestCase):
    def test_portable_artifact_excludes_personal_provider_sources(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "shortly-team"
            result = build_portable(output)

            self.assertTrue(result["ok"])
            self.assertFalse((output / "shortly" / "apps" / "personal").exists())
            self.assertFalse(
                (output / "shortly" / "llm" / "openai_transport.py").exists()
            )
            self.assertFalse(
                (output / "shortly" / "llm" / "openai_client.py").exists()
            )
            self.assertFalse((output / "shortly" / "llm" / "gemini.py").exists())
            self.assertTrue(
                (output / "shortly" / "apps" / "team" / "providers.py").is_file()
            )

            requirements = (output / "requirements.txt").read_text(encoding="utf-8")
            self.assertNotIn("openai", requirements.lower())
            self.assertNotIn("google-generativeai", requirements.lower())

            index = (
                output / "shortly" / "web" / "static" / "index.html"
            ).read_text(encoding="utf-8")
            script = (
                output / "shortly" / "web" / "static" / "app.js"
            ).read_text(encoding="utf-8")
            self.assertNotIn('value="local"', index)
            self.assertNotIn("Ollama", index)
            self.assertNotIn("/api/local-models", script)
            self.assertNotIn("loadLocalModels", script)
            self.assertNotIn("/api/personal/manifests/from-guide", script)
            self.assertNotIn("addPersonalAssetAutomation", script)
            self.assertIn(
                "shortly.apps.team.launcher",
                (output / "run.bat").read_text(encoding="utf-8"),
            )

            for source in (output / "shortly").rglob("*.py"):
                ast.parse(source.read_text(encoding="utf-8"), filename=str(source))

            smoke = f"""
import importlib.util
import sys
sys.path.insert(0, {str(output)!r})
import shortly
from shortly.config import Settings
from shortly.llm.base import LLMClient
from shortly.llm.factory import get_llm
from shortly.product_profile import ProductKind
client = get_llm(Settings(
    product=ProductKind.TEAM,
    llm_provider="local",
    text_model="blocked-local",
))
assert type(client) is LLMClient
assert importlib.util.find_spec("shortly.apps.personal") is None
assert str(shortly.__file__).startswith({str(output)!r})
if importlib.util.find_spec("fastapi") is not None:
    from fastapi.testclient import TestClient
    from shortly.apps.team.api import create_app
    app = create_app()
    paths = {{getattr(route, "path", None) for route in app.routes}}
    assert "/api/local-models" not in paths
    with TestClient(app, client=("127.0.0.1", 50000)) as client:
        response = client.get("/")
    assert response.status_code == 200, response.text
    assert 'value="local"' not in response.text
    assert "Ollama" not in response.text
"""
            completed = subprocess.run(
                [sys.executable, "-I", "-c", smoke],
                cwd=output,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(
                completed.returncode,
                0,
                completed.stderr or completed.stdout,
            )


if __name__ == "__main__":
    unittest.main()
