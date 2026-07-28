"""Open-licensed stock image search and local candidate selection."""

from __future__ import annotations

import html
import io
import ipaddress
import json
import re
import socket
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any
from uuid import uuid4

from PIL import Image, ImageOps

from ....core.domain.assets import (
    AssetCandidate,
    AssetType,
    CandidateStatus,
    MediaKind,
)
from .selection import select_candidate
from .store import AutomationStore


_OPENVERSE_API = "https://api.openverse.org/v1/images/"
_OPENVERSE_DEFAULT_LICENSES = ("pdm", "cc0")
_OPENVERSE_ALLOWED_LICENSES = (*_OPENVERSE_DEFAULT_LICENSES, "by")
_MAX_JSON_BYTES = 4 * 1024 * 1024
_MAX_IMAGE_BYTES = 25 * 1024 * 1024
_MAX_IMAGE_PIXELS = 40_000_000
_MAX_OUTPUT_EDGE = 4096
_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-"
    r"[89ab][0-9a-f]{3}-[0-9a-f]{12}$",
    re.IGNORECASE,
)
_TAG_RE = re.compile(r"<[^>]+>")


def _plain_text(value: Any) -> str:
    return re.sub(r"\s+", " ", html.unescape(_TAG_RE.sub("", str(value or "")))).strip()


def _read_limited(response, limit: int) -> bytes:
    content_length = response.headers.get("Content-Length")
    if content_length:
        try:
            declared_length = int(content_length)
        except (TypeError, ValueError):
            declared_length = 0
        if declared_length > limit:
            raise ValueError("원격 파일이 허용 크기를 초과합니다.")
    data = response.read(limit + 1)
    if len(data) > limit:
        raise ValueError("원격 파일이 허용 크기를 초과합니다.")
    return data


def _request_json(url: str) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": "Shortly/0.1 (personal stock image search)",
        },
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        data = _read_limited(response, _MAX_JSON_BYTES)
    parsed = json.loads(data.decode("utf-8"))
    if not isinstance(parsed, dict):
        raise ValueError("스톡 이미지 검색 응답 형식이 올바르지 않습니다.")
    return parsed


def _validate_public_https_url(url: str) -> str:
    """Reject local/private media URLs before server-side download."""

    parsed = urllib.parse.urlsplit(str(url or ""))
    if parsed.scheme.lower() != "https" or not parsed.hostname:
        raise ValueError("HTTPS 공개 이미지 주소만 사용할 수 있습니다.")
    hostname = parsed.hostname.rstrip(".").lower()
    if hostname == "localhost" or hostname.endswith(".localhost"):
        raise ValueError("로컬 주소의 이미지는 내려받을 수 없습니다.")
    try:
        addresses = socket.getaddrinfo(
            hostname,
            parsed.port or 443,
            type=socket.SOCK_STREAM,
        )
    except socket.gaierror as exc:
        raise ValueError("이미지 서버 주소를 확인할 수 없습니다.") from exc
    if not addresses:
        raise ValueError("이미지 서버 주소를 확인할 수 없습니다.")
    for address in addresses:
        ip = ipaddress.ip_address(address[4][0])
        if not ip.is_global:
            raise ValueError("공개 인터넷 이미지 주소만 사용할 수 있습니다.")
    return urllib.parse.urlunsplit(parsed)


class _SafeRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        _validate_public_https_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _download_image(url: str) -> tuple[bytes, str]:
    safe_url = _validate_public_https_url(url)
    opener = urllib.request.build_opener(_SafeRedirectHandler())
    request = urllib.request.Request(
        safe_url,
        headers={
            "Accept": "image/jpeg,image/png,image/webp",
            "User-Agent": "Shortly/0.1 (personal stock image download)",
        },
    )
    with opener.open(request, timeout=30) as response:
        final_url = _validate_public_https_url(response.geturl())
        content_type = str(
            response.headers.get_content_type() or ""
        ).lower()
        if content_type not in ("image/jpeg", "image/png", "image/webp"):
            raise ValueError("지원하지 않는 이미지 형식입니다.")
        data = _read_limited(response, _MAX_IMAGE_BYTES)
    return data, final_url


def _prepare_image(data: bytes, target: Path) -> tuple[int, int]:
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        with Image.open(io.BytesIO(data)) as source:
            source.verify()
        with Image.open(io.BytesIO(data)) as source:
            image = ImageOps.exif_transpose(source)
            width, height = image.size
            if width <= 0 or height <= 0:
                raise ValueError("이미지 크기가 올바르지 않습니다.")
            if width * height > _MAX_IMAGE_PIXELS:
                raise ValueError("이미지 해상도가 허용 범위를 초과합니다.")
            image = image.convert("RGB")
            image.thumbnail(
                (_MAX_OUTPUT_EDGE, _MAX_OUTPUT_EDGE),
                Image.Resampling.LANCZOS,
            )
            width, height = image.size
            image.save(target, format="JPEG", quality=95, optimize=True)
    except (OSError, Image.DecompressionBombError) as exc:
        raise ValueError("다운로드한 파일을 안전한 이미지로 해석할 수 없습니다.") from exc
    return int(width), int(height)


def _license_label(item: dict[str, Any]) -> str:
    license_name = str(item.get("license") or "").upper()
    version = str(item.get("license_version") or "").strip()
    return " ".join(value for value in (license_name, version) if value)


def _search_item(item: dict[str, Any]) -> dict[str, Any] | None:
    identifier = str(item.get("id") or "")
    license_name = str(item.get("license") or "").lower()
    thumbnail = str(item.get("thumbnail") or "")
    image_url = str(item.get("url") or "")
    if (
        not _UUID_RE.fullmatch(identifier)
        or license_name not in _OPENVERSE_ALLOWED_LICENSES
        or not thumbnail
        or not image_url
        or bool(item.get("mature"))
    ):
        return None
    return {
        "id": identifier,
        "title": _plain_text(item.get("title")) or "제목 없음",
        "thumbnail": thumbnail,
        "creator": _plain_text(item.get("creator")) or "작자 미상",
        "creator_url": str(item.get("creator_url") or ""),
        "license": license_name,
        "license_label": _license_label(item),
        "license_url": str(item.get("license_url") or ""),
        "source": _plain_text(item.get("source")),
        "provider": _plain_text(item.get("provider")),
        "landing_url": str(item.get("foreign_landing_url") or ""),
        "attribution": _plain_text(item.get("attribution")),
        "width": int(item.get("width") or 0),
        "height": int(item.get("height") or 0),
    }


class OpenverseStockService:
    """Small personal-only adapter around the anonymous Openverse API."""

    provider = "openverse"

    def __init__(self, assets_dir: Path):
        self.assets_dir = Path(assets_dir)

    def search(
        self,
        query: str,
        *,
        page: int = 1,
        page_size: int = 12,
        include_by: bool = False,
    ) -> dict[str, Any]:
        normalized = re.sub(r"\s+", " ", str(query or "")).strip()
        if not normalized:
            raise ValueError("검색어를 입력하세요.")
        if len(normalized) > 200:
            raise ValueError("검색어는 200자 이하여야 합니다.")
        page = max(1, min(int(page), 20))
        page_size = max(1, min(int(page_size), 20))
        licenses = (
            _OPENVERSE_ALLOWED_LICENSES
            if include_by
            else _OPENVERSE_DEFAULT_LICENSES
        )
        params = urllib.parse.urlencode({
            "q": normalized,
            "license": ",".join(licenses),
            "mature": "false",
            "page": page,
            "page_size": page_size,
        })
        response = _request_json(f"{_OPENVERSE_API}?{params}")
        results = [
            normalized_item
            for item in response.get("results") or []
            if isinstance(item, dict)
            for normalized_item in (_search_item(item),)
            if normalized_item is not None
        ]
        return {
            "provider": self.provider,
            "query": normalized,
            "page": page,
            "page_count": min(int(response.get("page_count") or page), 20),
            "result_count": int(response.get("result_count") or len(results)),
            "licenses": list(licenses),
            "results": results,
            "license_notice": (
                "Openverse의 라이선스 정보는 원 제공처에서 최종 확인해야 합니다."
            ),
        }

    def detail(self, result_id: str) -> dict[str, Any]:
        identifier = str(result_id or "").strip()
        if not _UUID_RE.fullmatch(identifier):
            raise ValueError("올바른 Openverse 이미지 식별자가 아닙니다.")
        item = _request_json(f"{_OPENVERSE_API}{identifier}/")
        license_name = str(item.get("license") or "").lower()
        if license_name not in _OPENVERSE_ALLOWED_LICENSES:
            raise ValueError("허용된 라이선스의 이미지가 아닙니다.")
        if bool(item.get("mature")):
            raise ValueError("민감한 이미지는 선택할 수 없습니다.")
        if not item.get("url"):
            raise ValueError("원본 이미지 주소가 없습니다.")
        return item

    def select(
        self,
        store: AutomationStore,
        *,
        manifest_id: str,
        cut_id: str,
        result_id: str,
        query: str = "",
    ) -> dict[str, Any]:
        manifest = store.load_manifest(manifest_id)
        cut = manifest.cut(cut_id)
        if cut.asset_type is not AssetType.STOCK_IMAGE:
            raise ValueError("스톡 이미지로 지정된 컷에서만 검색 이미지를 선택할 수 있습니다.")

        for existing in cut.candidates:
            if (
                existing.provider == self.provider
                and existing.external_id == result_id
                and existing.original_path
                and Path(existing.original_path).is_file()
            ):
                selected = select_candidate(
                    store,
                    manifest_id=manifest_id,
                    cut_id=cut_id,
                    candidate_id=existing.id,
                    reason="Openverse 검색 결과 재선택",
                )
                return {
                    "candidate": selected,
                    "cut": store.load_manifest(manifest_id).cut(cut_id).to_dict(),
                    "reused": True,
                }

        item = self.detail(result_id)
        image_bytes, final_url = _download_image(str(item["url"]))
        candidate_id = uuid4().hex
        target = (
            self.assets_dir
            / manifest_id
            / "stock"
            / cut_id
            / f"{candidate_id}-original.jpg"
        )
        width, height = _prepare_image(image_bytes, target)
        candidate = AssetCandidate(
            id=candidate_id,
            provider=self.provider,
            model="image-search",
            media_kind=MediaKind.IMAGE,
            status=CandidateStatus.READY,
            original_path=str(target.resolve()),
            prepared_path=str(target.resolve()),
            mime_type="image/jpeg",
            external_id=str(item.get("id") or result_id),
            cost_usd=0.0,
            metadata={
                "search_query": re.sub(r"\s+", " ", query).strip(),
                "title": _plain_text(item.get("title")) or "제목 없음",
                "creator": _plain_text(item.get("creator")) or "작자 미상",
                "creator_url": str(item.get("creator_url") or ""),
                "license": str(item.get("license") or "").lower(),
                "license_version": str(item.get("license_version") or ""),
                "license_url": str(item.get("license_url") or ""),
                "license_label": _license_label(item),
                "source": _plain_text(item.get("source")),
                "provider": _plain_text(item.get("provider")),
                "landing_url": str(item.get("foreign_landing_url") or ""),
                "attribution": _plain_text(item.get("attribution")),
                "download_url": final_url,
                "width": width,
                "height": height,
            },
        )
        cut.candidates.append(candidate)
        store.save_manifest(manifest)
        selected = select_candidate(
            store,
            manifest_id=manifest_id,
            cut_id=cut_id,
            candidate_id=candidate.id,
            reason="Openverse 스톡 이미지 선택",
        )
        return {
            "candidate": selected,
            "cut": store.load_manifest(manifest_id).cut(cut_id).to_dict(),
            "reused": False,
            "license_notice": (
                "사용 전 원본 페이지에서 라이선스와 저작자 표시 조건을 확인하세요."
            ),
        }
