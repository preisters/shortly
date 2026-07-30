"""Open-licensed stock image search and local candidate selection."""

from __future__ import annotations

import html
import hashlib
import io
import ipaddress
import json
import re
import socket
import time
import urllib.parse
import urllib.error
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
_PIXABAY_API = "https://pixabay.com/api/"
_PEXELS_API = "https://api.pexels.com/v1/"
_PIXABAY_LICENSE_URL = "https://pixabay.com/service/license-summary/"
_PEXELS_LICENSE_URL = "https://www.pexels.com/license/"
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
_NUMERIC_ID_RE = re.compile(r"^[1-9][0-9]*$")
_TAG_RE = re.compile(r"<[^>]+>")
_HANGUL_RE = re.compile(r"[\uac00-\ud7a3]")


def _plain_text(value: Any) -> str:
    return re.sub(r"\s+", " ", html.unescape(_TAG_RE.sub("", str(value or "")))).strip()


def _query_language(query: str) -> tuple[str, str]:
    """Return Pixabay language and Pexels locale for the actual query text."""

    if _HANGUL_RE.search(query):
        return "ko", "ko-KR"
    return "en", "en-US"


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


def _request_json(
    url: str,
    *,
    headers: dict[str, str] | None = None,
    error_label: str = "스톡 이미지",
) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": "Shortly/0.1 (personal stock image search)",
            **(headers or {}),
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            data = _read_limited(response, _MAX_JSON_BYTES)
    except urllib.error.HTTPError as exc:
        raise ValueError(
            f"{error_label} 요청에 실패했습니다. (HTTP {exc.code})"
        ) from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise ValueError(f"{error_label} 서버에 연결할 수 없습니다.") from exc
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
        "source_provider": _plain_text(item.get("provider")),
        "provider": "openverse",
        "provider_label": "Openverse",
        "landing_url": str(item.get("foreign_landing_url") or ""),
        "attribution": _plain_text(item.get("attribution")),
        "width": int(item.get("width") or 0),
        "height": int(item.get("height") or 0),
    }


class OpenverseStockService:
    """Small personal-only adapter around the anonymous Openverse API."""

    provider = "openverse"
    provider_label = "Openverse"
    available = True

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

    def status(self) -> dict[str, Any]:
        return {
            "id": self.provider,
            "label": self.provider_label,
            "available": True,
            "requires_api_key": False,
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


def _positive_numeric_id(result_id: str, provider_label: str) -> str:
    identifier = str(result_id or "").strip()
    if not _NUMERIC_ID_RE.fullmatch(identifier):
        raise ValueError(f"올바른 {provider_label} 이미지 식별자가 아닙니다.")
    return identifier


def _cache_key(provider: str, values: dict[str, Any]) -> str:
    serialized = json.dumps(
        {"provider": provider, **values},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


class _CachedKeyedProvider:
    cache_ttl_seconds = 24 * 60 * 60

    def __init__(self, assets_dir: Path, api_key: str):
        self.assets_dir = Path(assets_dir)
        self.api_key = str(api_key or "").strip()

    @property
    def available(self) -> bool:
        return bool(self.api_key)

    def status(self) -> dict[str, Any]:
        return {
            "id": self.provider,
            "label": self.provider_label,
            "available": self.available,
            "requires_api_key": True,
            "env_var": self.env_var,
        }

    def _cached_request(
        self,
        cache_values: dict[str, Any],
        request_url: str,
        *,
        headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        cache_dir = self.assets_dir / ".stock-search-cache"
        cache_path = cache_dir / (
            _cache_key(self.provider, cache_values) + ".json"
        )
        try:
            cached = json.loads(cache_path.read_text(encoding="utf-8"))
            if (
                isinstance(cached, dict)
                and float(cached.get("expires_at") or 0) > time.time()
                and isinstance(cached.get("payload"), dict)
            ):
                return dict(cached["payload"])
        except (OSError, ValueError, TypeError):
            pass
        payload = _request_json(
            request_url,
            headers=headers,
            error_label=self.provider_label,
        )
        cache_dir.mkdir(parents=True, exist_ok=True)
        temporary = cache_path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(
                {
                    "expires_at": time.time() + self.cache_ttl_seconds,
                    "payload": payload,
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        temporary.replace(cache_path)
        return payload


class PixabayStockService(_CachedKeyedProvider):
    provider = "pixabay"
    provider_label = "Pixabay"
    env_var = "PIXABAY_API_KEY"

    def search(
        self,
        query: str,
        *,
        page: int = 1,
        page_size: int = 8,
        include_by: bool = False,
    ) -> dict[str, Any]:
        del include_by
        if not self.available:
            raise ValueError("PIXABAY_API_KEY가 설정되지 않았습니다.")
        normalized = re.sub(r"\s+", " ", str(query or "")).strip()
        if not normalized:
            raise ValueError("검색어를 입력하세요.")
        normalized = normalized[:100]
        page = max(1, min(int(page), 20))
        page_size = max(3, min(int(page_size), 20))
        request_params = {
            "key": self.api_key,
            "q": normalized,
            "lang": _query_language(normalized)[0],
            "image_type": "photo",
            "orientation": "vertical",
            "safesearch": "true",
            "page": page,
            "per_page": page_size,
        }
        response = self._cached_request(
            {
                key: value
                for key, value in request_params.items()
                if key != "key"
            },
            f"{_PIXABAY_API}?{urllib.parse.urlencode(request_params)}",
        )
        results = []
        for item in response.get("hits") or []:
            if not isinstance(item, dict):
                continue
            identifier = str(item.get("id") or "")
            thumbnail = str(
                item.get("webformatURL") or item.get("previewURL") or ""
            )
            landing_url = str(item.get("pageURL") or "")
            if not _NUMERIC_ID_RE.fullmatch(identifier) or not thumbnail:
                continue
            creator = _plain_text(item.get("user")) or "작자 미상"
            results.append(
                {
                    "id": identifier,
                    "title": _plain_text(item.get("tags")) or "Pixabay 이미지",
                    "thumbnail": thumbnail,
                    "creator": creator,
                    "creator_url": landing_url,
                    "license": "pixabay",
                    "license_label": "Pixabay Content License",
                    "license_url": _PIXABAY_LICENSE_URL,
                    "source": "Pixabay",
                    "provider": self.provider,
                    "provider_label": self.provider_label,
                    "landing_url": landing_url,
                    "attribution": f"{creator} · Pixabay",
                    "width": int(item.get("imageWidth") or 0),
                    "height": int(item.get("imageHeight") or 0),
                }
            )
        total = int(response.get("totalHits") or len(results))
        return {
            "provider": self.provider,
            "query": normalized,
            "page": page,
            "page_count": max(1, min(20, (total + page_size - 1) // page_size)),
            "result_count": total,
            "licenses": ["pixabay"],
            "results": results,
            "license_notice": (
                "Pixabay Content License와 원본 페이지의 제3자 권리를 확인하세요."
            ),
        }

    def detail(self, result_id: str) -> dict[str, Any]:
        if not self.available:
            raise ValueError("PIXABAY_API_KEY가 설정되지 않았습니다.")
        identifier = _positive_numeric_id(result_id, self.provider_label)
        params = {"key": self.api_key, "id": identifier}
        response = _request_json(
            f"{_PIXABAY_API}?{urllib.parse.urlencode(params)}",
            error_label=self.provider_label,
        )
        hits = [item for item in response.get("hits") or [] if isinstance(item, dict)]
        if not hits:
            raise ValueError("Pixabay 이미지 정보를 찾을 수 없습니다.")
        item = hits[0]
        creator = _plain_text(item.get("user")) or "작자 미상"
        landing_url = str(item.get("pageURL") or "")
        return {
            "id": identifier,
            "title": _plain_text(item.get("tags")) or "Pixabay 이미지",
            "creator": creator,
            "creator_url": landing_url,
            "license": "pixabay",
            "license_label": "Pixabay Content License",
            "license_url": _PIXABAY_LICENSE_URL,
            "source": "Pixabay",
            "provider": self.provider,
            "provider_label": self.provider_label,
            "landing_url": landing_url,
            "attribution": f"{creator} · Pixabay",
            "download_url": str(
                item.get("largeImageURL") or item.get("webformatURL") or ""
            ),
        }


class PexelsStockService(_CachedKeyedProvider):
    provider = "pexels"
    provider_label = "Pexels"
    env_var = "PEXELS_API_KEY"

    def search(
        self,
        query: str,
        *,
        page: int = 1,
        page_size: int = 8,
        include_by: bool = False,
    ) -> dict[str, Any]:
        del include_by
        if not self.available:
            raise ValueError("PEXELS_API_KEY가 설정되지 않았습니다.")
        normalized = re.sub(r"\s+", " ", str(query or "")).strip()
        if not normalized:
            raise ValueError("검색어를 입력하세요.")
        if len(normalized) > 200:
            raise ValueError("검색어는 200자 이하여야 합니다.")
        page = max(1, min(int(page), 20))
        page_size = max(1, min(int(page_size), 20))
        params = {
            "query": normalized,
            "orientation": "portrait",
            "locale": _query_language(normalized)[1],
            "page": page,
            "per_page": page_size,
        }
        response = self._cached_request(
            params,
            f"{_PEXELS_API}search?{urllib.parse.urlencode(params)}",
            headers={"Authorization": self.api_key},
        )
        results = []
        for item in response.get("photos") or []:
            if not isinstance(item, dict):
                continue
            identifier = str(item.get("id") or "")
            sources = item.get("src") or {}
            thumbnail = str(
                sources.get("medium") or sources.get("portrait") or ""
            )
            if not _NUMERIC_ID_RE.fullmatch(identifier) or not thumbnail:
                continue
            creator = _plain_text(item.get("photographer")) or "작자 미상"
            results.append(
                {
                    "id": identifier,
                    "title": _plain_text(item.get("alt")) or "Pexels 이미지",
                    "thumbnail": thumbnail,
                    "creator": creator,
                    "creator_url": str(item.get("photographer_url") or ""),
                    "license": "pexels",
                    "license_label": "Pexels License",
                    "license_url": _PEXELS_LICENSE_URL,
                    "source": "Pexels",
                    "provider": self.provider,
                    "provider_label": self.provider_label,
                    "landing_url": str(item.get("url") or ""),
                    "attribution": f"{creator} · Pexels",
                    "width": int(item.get("width") or 0),
                    "height": int(item.get("height") or 0),
                }
            )
        total = int(response.get("total_results") or len(results))
        return {
            "provider": self.provider,
            "query": normalized,
            "page": page,
            "page_count": max(1, min(20, (total + page_size - 1) // page_size)),
            "result_count": total,
            "licenses": ["pexels"],
            "results": results,
            "license_notice": (
                "Pexels 링크와 사진가 출처를 유지하고 원본 라이선스를 확인하세요."
            ),
        }

    def detail(self, result_id: str) -> dict[str, Any]:
        if not self.available:
            raise ValueError("PEXELS_API_KEY가 설정되지 않았습니다.")
        identifier = _positive_numeric_id(result_id, self.provider_label)
        item = _request_json(
            f"{_PEXELS_API}photos/{identifier}",
            headers={"Authorization": self.api_key},
            error_label=self.provider_label,
        )
        sources = item.get("src") or {}
        creator = _plain_text(item.get("photographer")) or "작자 미상"
        return {
            "id": identifier,
            "title": _plain_text(item.get("alt")) or "Pexels 이미지",
            "creator": creator,
            "creator_url": str(item.get("photographer_url") or ""),
            "license": "pexels",
            "license_label": "Pexels License",
            "license_url": _PEXELS_LICENSE_URL,
            "source": "Pexels",
            "provider": self.provider,
            "provider_label": self.provider_label,
            "landing_url": str(item.get("url") or ""),
            "attribution": f"{creator} · Pexels",
            "download_url": str(
                sources.get("original")
                or sources.get("large2x")
                or sources.get("large")
                or ""
            ),
        }


def _select_provider_item(
    service,
    store: AutomationStore,
    *,
    manifest_id: str,
    cut_id: str,
    result_id: str,
    query: str,
) -> dict[str, Any]:
    manifest = store.load_manifest(manifest_id)
    cut = manifest.cut(cut_id)
    if cut.asset_type is not AssetType.STOCK_IMAGE:
        raise ValueError("스톡 이미지로 지정된 컷에서만 검색 이미지를 선택할 수 있습니다.")
    for existing in cut.candidates:
        if (
            existing.provider == service.provider
            and existing.external_id == result_id
            and existing.original_path
            and Path(existing.original_path).is_file()
        ):
            selected = select_candidate(
                store,
                manifest_id=manifest_id,
                cut_id=cut_id,
                candidate_id=existing.id,
                reason=f"{service.provider_label} 검색 결과 재선택",
            )
            return {
                "candidate": selected,
                "cut": store.load_manifest(manifest_id).cut(cut_id).to_dict(),
                "reused": True,
            }
    item = service.detail(result_id)
    download_url = str(item.get("download_url") or "")
    if not download_url:
        raise ValueError("원본 이미지 주소가 없습니다.")
    image_bytes, final_url = _download_image(download_url)
    candidate_id = uuid4().hex
    target = (
        service.assets_dir
        / manifest_id
        / "stock"
        / service.provider
        / cut_id
        / f"{candidate_id}-original.jpg"
    )
    width, height = _prepare_image(image_bytes, target)
    candidate = AssetCandidate(
        id=candidate_id,
        provider=service.provider,
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
            "title": item.get("title") or "제목 없음",
            "creator": item.get("creator") or "작자 미상",
            "creator_url": item.get("creator_url") or "",
            "license": item.get("license") or "",
            "license_url": item.get("license_url") or "",
            "license_label": item.get("license_label") or "",
            "source": item.get("source") or service.provider_label,
            "provider": service.provider,
            "provider_label": service.provider_label,
            "landing_url": item.get("landing_url") or "",
            "attribution": item.get("attribution") or "",
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
        reason=f"{service.provider_label} 스톡 이미지 선택",
    )
    return {
        "candidate": selected,
        "cut": store.load_manifest(manifest_id).cut(cut_id).to_dict(),
        "reused": False,
        "license_notice": (
            "사용 전 원본 페이지에서 라이선스와 저작자 표시 조건을 확인하세요."
        ),
    }


class StockSearchService:
    """Aggregate available stock providers behind one personal-only API."""

    provider = "multi"

    def __init__(
        self,
        assets_dir: Path,
        *,
        pixabay_api_key: str = "",
        pexels_api_key: str = "",
    ):
        self.providers = {
            "openverse": OpenverseStockService(assets_dir),
            "pixabay": PixabayStockService(assets_dir, pixabay_api_key),
            "pexels": PexelsStockService(assets_dir, pexels_api_key),
        }

    def statuses(self) -> list[dict[str, Any]]:
        return [provider.status() for provider in self.providers.values()]

    def search(
        self,
        query: str,
        *,
        provider: str = "all",
        page: int = 1,
        page_size: int = 18,
        include_by: bool = False,
    ) -> dict[str, Any]:
        provider_id = str(provider or "all").strip().lower()
        if provider_id != "all" and provider_id not in self.providers:
            raise ValueError("지원하지 않는 스톡 이미지 사이트입니다.")
        targets = (
            list(self.providers.values())
            if provider_id == "all"
            else [self.providers[provider_id]]
        )
        targets = [item for item in targets if item.available]
        if not targets:
            raise ValueError("선택한 스톡 이미지 사이트의 API 키가 설정되지 않았습니다.")
        per_provider = max(3, min(20, int(page_size) // len(targets)))
        responses = []
        errors = []
        for item in targets:
            try:
                responses.append(
                    item.search(
                        query,
                        page=page,
                        page_size=per_provider,
                        include_by=include_by,
                    )
                )
            except ValueError as exc:
                errors.append(
                    {"provider": item.provider, "message": str(exc)}
                )
        if not responses:
            message = errors[0]["message"] if errors else "검색 결과를 가져오지 못했습니다."
            raise ValueError(message)
        buckets = [list(response["results"]) for response in responses]
        interleaved = [
            item
            for index in range(max((len(bucket) for bucket in buckets), default=0))
            for bucket in buckets
            if index < len(bucket)
            for item in (bucket[index],)
        ]
        licenses = [
            license_name
            for response in responses
            for license_name in response.get("licenses") or []
        ]
        return {
            "provider": provider_id,
            "query": responses[0]["query"],
            "page": max(1, int(page)),
            "page_count": max(
                int(response.get("page_count") or 1)
                for response in responses
            ),
            "result_count": sum(
                int(response.get("result_count") or 0)
                for response in responses
            ),
            "licenses": list(dict.fromkeys(licenses)),
            "results": interleaved,
            "provider_errors": errors,
            "license_notice": (
                "각 카드의 사이트와 원본 페이지에서 라이선스·출처 조건을 확인하세요."
            ),
        }

    def select(
        self,
        store: AutomationStore,
        *,
        provider: str,
        manifest_id: str,
        cut_id: str,
        result_id: str,
        query: str = "",
    ) -> dict[str, Any]:
        provider_id = str(provider or "").strip().lower()
        service = self.providers.get(provider_id)
        if service is None:
            raise ValueError("지원하지 않는 스톡 이미지 사이트입니다.")
        if not service.available:
            raise ValueError(
                f"{service.env_var}가 설정되지 않았습니다."
            )
        if provider_id == "openverse":
            return service.select(
                store,
                manifest_id=manifest_id,
                cut_id=cut_id,
                result_id=result_id,
                query=query,
            )
        return _select_provider_item(
            service,
            store,
            manifest_id=manifest_id,
            cut_id=cut_id,
            result_id=result_id,
            query=query,
        )
