"""Personal-only local model discovery."""

from __future__ import annotations

import json
import urllib.request


def discover_local_models(base_url: str, api_key: str) -> dict:
    url = (base_url or "").strip().rstrip("/")
    if not url:
        return {"models": [], "error": "로컬 서버 주소가 설정되지 않았습니다."}
    request = urllib.request.Request(
        url + "/models",
        headers={"Authorization": f"Bearer {api_key or 'not-needed'}"},
    )
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            data = json.loads(response.read().decode("utf-8"))
        identifiers = [
            model.get("id")
            for model in data.get("data", [])
            if model.get("id")
        ]
        generation = [
            model for model in identifiers if "embed" not in model.lower()
        ]
        embedding = [
            model for model in identifiers if "embed" in model.lower()
        ]
        return {"models": generation + embedding, "base_url": url}
    except Exception as exc:  # noqa: BLE001
        return {"models": [], "error": f"모델 목록 조회 실패: {exc}"}
