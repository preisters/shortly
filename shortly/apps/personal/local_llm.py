"""Personal-only OpenAI-compatible local LLM adapter."""

from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Any, Callable, TYPE_CHECKING

from ...llm.base import LLMClient
from ...llm.openai_transport import OpenAICompatibleClient
from .local_resources import LocalModelResourceManager

if TYPE_CHECKING:
    from ...config import Settings


LOCAL_PROVIDER_NAMES = ("local", "ollama", "lmstudio", "llamacpp")
log = logging.getLogger(__name__)
_ACTIVE_CALLS: dict[tuple[str, str], int] = {}
_ACTIVE_LOCK = threading.RLock()


class AutoUnloadingLocalClient(OpenAICompatibleClient):
    """OpenAI-compatible local client that releases model RAM after each use."""

    def __init__(
        self,
        *args,
        auto_unload: bool = True,
        runtime: str = "auto",
        **kwargs,
    ) -> None:
        super().__init__(*args, **kwargs)
        self._auto_unload = bool(auto_unload and self._base_url)
        self._resource_manager = (
            LocalModelResourceManager(
                self._base_url,
                kwargs.get("api_key", args[0] if args else ""),
                runtime=runtime,
            )
            if self._auto_unload
            else None
        )

    def _run_and_release(
        self,
        model: str,
        operation: Callable[[], Any],
    ) -> Any:
        manager = self._resource_manager
        if manager is None:
            return operation()
        key = (manager.origin, model)
        with _ACTIVE_LOCK:
            _ACTIVE_CALLS[key] = _ACTIVE_CALLS.get(key, 0) + 1
        try:
            return operation()
        finally:
            with _ACTIVE_LOCK:
                remaining = _ACTIVE_CALLS.get(key, 1) - 1
                if remaining > 0:
                    _ACTIVE_CALLS[key] = remaining
                    continue_unload = False
                else:
                    _ACTIVE_CALLS.pop(key, None)
                    continue_unload = True
                if continue_unload:
                    try:
                        released = manager.unload(model)
                        if released:
                            log.info(
                                "로컬 AI 사용 완료: 모델 메모리 해제 요청 성공"
                            )
                        else:
                            log.warning(
                                "로컬 서버가 모델 자동 해제를 지원하지 않습니다."
                            )
                    except Exception as exc:  # noqa: BLE001
                        log.warning(
                            "로컬 모델 메모리 해제 요청 실패: %s",
                            type(exc).__name__,
                        )

    def complete_text(
        self,
        system: str,
        user: str,
        temperature: float = 0.7,
        max_tokens: int = 4096,
    ) -> str:
        return self._run_and_release(
            self.text_model,
            lambda: super(AutoUnloadingLocalClient, self).complete_text(
                system,
                user,
                temperature=temperature,
                max_tokens=max_tokens,
            ),
        )

    def complete_json(
        self,
        system: str,
        user: str,
        temperature: float = 0.4,
        max_tokens: int = 4096,
    ) -> Any:
        return self._run_and_release(
            self.text_model,
            lambda: super(AutoUnloadingLocalClient, self).complete_json(
                system,
                user,
                temperature=temperature,
                max_tokens=max_tokens,
            ),
        )

    def describe_image(
        self,
        image_path: Path,
        prompt: str,
        temperature: float = 0.4,
        max_tokens: int = 1024,
    ) -> str:
        return self._run_and_release(
            self.vision_model,
            lambda: super(AutoUnloadingLocalClient, self).describe_image(
                image_path,
                prompt,
                temperature=temperature,
                max_tokens=max_tokens,
            ),
        )


def make_local_client(
    settings: "Settings",
    text_model: str,
    vision_model: str,
    *,
    runtime: str = "auto",
) -> LLMClient:
    return AutoUnloadingLocalClient(
        settings.local_api_key,
        text_model,
        vision_model or text_model,
        base_url=settings.local_base_url,
        provider="local",
        no_think=settings.local_no_think,
        auto_unload=settings.local_auto_unload,
        runtime=runtime,
    )
