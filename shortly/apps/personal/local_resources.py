"""Release model memory owned by personal-only local inference servers."""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable


JsonTransport = Callable[
    [str, str, dict[str, Any] | None, dict[str, str]],
    dict[str, Any],
]


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
