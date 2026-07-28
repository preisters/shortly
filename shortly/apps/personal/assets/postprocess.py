"""Generated image persistence and 9:16 normalization."""

from __future__ import annotations

import os
from pathlib import Path
from uuid import uuid4

from ....core.ports.assets import GeneratedAsset, ProviderUnavailableError


def _extension(mime_type: str) -> str:
    return {
        "image/png": ".png",
        "image/jpeg": ".jpg",
        "image/webp": ".webp",
        "video/mp4": ".mp4",
    }.get(mime_type, ".bin")


def atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    temporary.write_bytes(data)
    os.replace(temporary, path)


def persist_original(asset: GeneratedAsset, directory: Path, stem: str) -> Path:
    path = Path(directory) / f"{stem}-original{_extension(asset.mime_type)}"
    atomic_write(path, asset.data)
    return path


def prepare_image(
    original: Path,
    output: Path,
    *,
    width: int = 1080,
    height: int = 1920,
) -> Path:
    try:
        from PIL import Image, ImageOps
    except ImportError as exc:
        raise ProviderUnavailableError(
            "Pillow is required to prepare generated images."
        ) from exc
    output.parent.mkdir(parents=True, exist_ok=True)
    with Image.open(original) as image:
        image = ImageOps.exif_transpose(image).convert("RGB")
        prepared = ImageOps.fit(
            image,
            (width, height),
            method=Image.Resampling.LANCZOS,
            centering=(0.5, 0.5),
        )
        temporary = output.with_name(f".{output.name}.{uuid4().hex}.tmp")
        prepared.save(temporary, format="JPEG", quality=94, optimize=True)
        os.replace(temporary, output)
    return output
