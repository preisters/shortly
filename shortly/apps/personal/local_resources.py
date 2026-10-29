"""Release model memory owned by personal-only local inference servers."""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Callable


JsonTransport = Callable[
    [str, str, dict[str, Any] | None, dict[str, str]],
    dict[str, Any],
]
CommandRunner = Callable[[list[str], float], None]
log = logging.getLogger(__name__)
_START_LOCK = threading.RLock()


def _run_command(command: list[str], timeout: float) -> None:
    try:
        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=max(1.0, timeout),
            creationflags=(
                subprocess.CREATE_NO_WINDOW
                if hasattr(subprocess, "CREATE_NO_WINDOW")
                else 0
            ),
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise RuntimeError("LM Studio CLI 실행에 실패했습니다.") from exc
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").strip()
        raise RuntimeError(
            "LM Studio CLI 명령이 실패했습니다."
            + (f" {detail[:300]}" if detail else "")
        )


def _find_lms_cli() -> str:
    discovered = shutil.which("lms")
    if discovered:
        return discovered
    home = Path.home()
    candidates = (
        home / ".lmstudio" / "bin" / "lms.exe",
        home / ".lmstudio" / "bin" / "lms.cmd",
    )
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)
    raise RuntimeError(
        "LM Studio CLI(lms)를 찾을 수 없습니다. LM Studio에서 CLI를 설치하세요."
    )


def _request_json(
    method: str,
    url: str,
    payload: dict[str, Any] | None,
    headers: dict[str, str],
) -> dict[str, Any]:
    data = (
        json.dumps(payload, ensure_ascii=False).encode("utf-8")
        if payload is not None
        else None
    )
    request = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
            **headers,
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            body = response.read(2 * 1024 * 1024 + 1)
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise RuntimeError("로컬 모델 관리 API에 연결할 수 없습니다.") from exc
    if len(body) > 2 * 1024 * 1024:
        raise RuntimeError("로컬 모델 관리 API 응답이 너무 큽니다.")
    if not body:
        return {}
    parsed = json.loads(body.decode("utf-8"))
    return parsed if isinstance(parsed, dict) else {}


class LocalModelResourceManager:
    """Unload only the model used by Shortly; never stops the server process."""

    def __init__(
        self,
        base_url: str,
        api_key: str = "",
        *,
        runtime: str = "auto",
        transport: JsonTransport = _request_json,
        command_runner: CommandRunner = _run_command,
        startup_timeout: float = 180.0,
    ) -> None:
        parsed = urllib.parse.urlsplit(str(base_url or "").strip())
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            raise ValueError("올바른 로컬 모델 서버 주소가 아닙니다.")
        self.origin = urllib.parse.urlunsplit(
            (parsed.scheme, parsed.netloc, "", "", "")
        ).rstrip("/")
        self.api_key = str(api_key or "").strip()
        self.runtime = self._runtime_hint(parsed, runtime)
        self._transport = transport
        self._command_runner = command_runner
        self._startup_timeout = max(5.0, float(startup_timeout))
        self._host = (parsed.hostname or "").lower()
        self._port = parsed.port

    @staticmethod
    def _runtime_hint(
        parsed: urllib.parse.SplitResult,
        configured: str,
    ) -> str:
        value = str(configured or "auto").strip().lower()
        if value in ("ollama", "lmstudio", "llamacpp"):
            return value
        if parsed.port == 11434:
            return "ollama"
        if parsed.port == 1234:
            return "lmstudio"
        return "auto"

    @property
    def _headers(self) -> dict[str, str]:
        if not self.api_key:
            return {}
        return {"Authorization": f"Bearer {self.api_key}"}

    def _detect_runtime(self) -> str:
        if self.runtime != "auto":
            return self.runtime
        try:
            value = self._transport(
                "GET",
                f"{self.origin}/api/version",
                None,
                self._headers,
            )
            if value.get("version"):
                self.runtime = "ollama"
                return self.runtime
        except Exception:
            pass
        try:
            value = self._transport(
                "GET",
                f"{self.origin}/api/v1/models",
                None,
                self._headers,
            )
            if isinstance(value.get("models"), list):
                self.runtime = "lmstudio"
                return self.runtime
        except Exception:
            pass
        self.runtime = "llamacpp"
        return self.runtime

    def unload(self, model: str) -> bool:
        identifier = str(model or "").strip()
        if not identifier:
            return False
        runtime = self._detect_runtime()
        if runtime == "ollama":
            response = self._transport(
                "POST",
                f"{self.origin}/api/generate",
                {
                    "model": identifier,
                    "prompt": "",
                    "stream": False,
                    "keep_alive": 0,
                },
                self._headers,
            )
            return bool(
                response.get("done")
                or response.get("done_reason") == "unload"
            )
        if runtime == "lmstudio":
            instance_ids = self._lmstudio_instance_ids(identifier)
            if not instance_ids:
                return True
            for instance_id in instance_ids:
                self._transport(
                    "POST",
                    f"{self.origin}/api/v1/models/unload",
                    {"instance_id": instance_id},
                    self._headers,
                )
            return True
        # llama.cpp's OpenAI-compatible server has no standard model-unload API.
        return False

    def ensure_ready(self, model: str) -> bool:
        """Start local LM Studio and load the requested model when necessary."""

        if self.runtime != "lmstudio":
            return False
        if self._host not in ("localhost", "127.0.0.1", "::1"):
            return False
        identifier = str(model or "").strip()
        if not identifier:
            raise RuntimeError("LM Studio에서 불러올 로컬 모델명이 비어 있습니다.")
        with _START_LOCK:
            models = self._try_lmstudio_models()
            if models is None:
                cli = _find_lms_cli()
                command = [cli, "server", "start", "--bind", "127.0.0.1"]
                if self._port:
                    command.extend(["--port", str(self._port)])
                log.info("LM Studio 서버가 응답하지 않아 자동으로 시작합니다.")
                self._command_runner(command, 30.0)
                models = self._wait_for_lmstudio()
            if self._model_is_loaded(models, identifier):
                return True
            cli = _find_lms_cli()
            log.info("LM Studio 검수 모델을 자동으로 로드합니다: %s", identifier)
            self._command_runner(
                [cli, "load", identifier, "--identifier", identifier, "-y"],
                self._startup_timeout,
            )
            models = self._wait_for_lmstudio(require_model=identifier)
            if not self._model_is_loaded(models, identifier):
                raise RuntimeError(
                    "LM Studio 서버는 실행됐지만 요청 모델이 로드되지 않았습니다."
                )
            return True

    def _try_lmstudio_models(self) -> dict[str, Any] | None:
        try:
            return self._transport(
                "GET",
                f"{self.origin}/api/v1/models",
                None,
                self._headers,
            )
        except Exception:
            return None

    def _wait_for_lmstudio(
        self,
        *,
        require_model: str = "",
    ) -> dict[str, Any]:
        deadline = time.monotonic() + self._startup_timeout
        while time.monotonic() < deadline:
            models = self._try_lmstudio_models()
            if models is not None and (
                not require_model
                or self._model_is_loaded(models, require_model)
            ):
                return models
            time.sleep(0.5)
        if require_model:
            raise RuntimeError(
                f"LM Studio 모델 로드 대기 시간이 초과됐습니다: {require_model}"
            )
        raise RuntimeError("LM Studio 서버 시작 대기 시간이 초과됐습니다.")

    @staticmethod
    def _model_is_loaded(response: dict[str, Any], model: str) -> bool:
        for item in response.get("models") or []:
            if not isinstance(item, dict):
                continue
            instances = [
                instance
                for instance in item.get("loaded_instances") or []
                if isinstance(instance, dict)
            ]
            if str(item.get("key") or "") == model and instances:
                return True
            if any(
                str(instance.get("id") or "") == model
                for instance in instances
            ):
                return True
        return False

    def _lmstudio_instance_ids(self, model: str) -> list[str]:
        response = self._transport(
            "GET",
            f"{self.origin}/api/v1/models",
            None,
            self._headers,
        )
        matches: list[str] = []
        for item in response.get("models") or []:
            if not isinstance(item, dict):
                continue
            instances = [
                instance
                for instance in item.get("loaded_instances") or []
                if isinstance(instance, dict) and instance.get("id")
            ]
            if str(item.get("key") or "") == model:
                matches.extend(str(instance["id"]) for instance in instances)
                continue
            matches.extend(
                str(instance["id"])
                for instance in instances
                if str(instance.get("id") or "") == model
            )
        return list(dict.fromkeys(matches))
