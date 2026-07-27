"""Product-specific state and user artifact path policy."""

from __future__ import annotations

import ctypes
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Optional
from uuid import UUID

from .product_profile import ProductKind


_FOLDERID_DOCUMENTS = UUID("FDD39AD0-238F-46AF-ADB4-6C85480369C7")


class _GUID(ctypes.Structure):
    _fields_ = [
        ("data1", ctypes.c_uint32),
        ("data2", ctypes.c_uint16),
        ("data3", ctypes.c_uint16),
        ("data4", ctypes.c_ubyte * 8),
    ]

    @classmethod
    def from_uuid(cls, value: UUID) -> "_GUID":
        raw = value.bytes_le
        return cls.from_buffer_copy(raw)


def _windows_known_documents() -> Optional[Path]:
    """Return the Windows Documents Known Folder without hard-coded names."""

    if os.name != "nt" or not hasattr(ctypes, "windll"):
        return None
    folder_id = _GUID.from_uuid(_FOLDERID_DOCUMENTS)
    value = ctypes.c_void_p()
    try:
        result = ctypes.windll.shell32.SHGetKnownFolderPath(  # type: ignore[attr-defined]
            ctypes.byref(folder_id),
            0,
            None,
            ctypes.byref(value),
        )
        if result != 0 or value.value is None:
            return None
        return Path(ctypes.wstring_at(value.value))
    except (AttributeError, OSError):
        return None
    finally:
        if value.value is not None:
            try:
                ctypes.windll.ole32.CoTaskMemFree(value)  # type: ignore[attr-defined]
            except (AttributeError, OSError):
                pass


def documents_dir(
    *,
    environ: Optional[Mapping[str, str]] = None,
    home: Optional[Path] = None,
) -> Path:
    """Resolve Documents with a Windows Known Folder and safe fallback."""

    known = _windows_known_documents()
    if known is not None:
        return known
    env = os.environ if environ is None else environ
    fallback_home = Path(
        env.get("USERPROFILE")
        or env.get("HOME")
        or home
        or Path.home()
    )
    return fallback_home / "Documents"


@dataclass(frozen=True)
class PathPolicy:
    product: ProductKind
    state_root: Path
    data_root: Path
    artifact_root: Path
    log_root: Path
    tts_root: Path

    @property
    def env_file(self) -> Path:
        return self.state_root / ".env"

    def ensure_dirs(self) -> None:
        for path in (
            self.state_root,
            self.data_root,
            self.artifact_root,
            self.log_root,
            self.tts_root,
        ):
            path.mkdir(parents=True, exist_ok=True)

    @classmethod
    def for_product(
        cls,
        product: ProductKind,
        *,
        project_root: Path,
        environ: Optional[Mapping[str, str]] = None,
        home: Optional[Path] = None,
        documents: Optional[Path] = None,
    ) -> "PathPolicy":
        env = os.environ if environ is None else environ
        product = ProductKind(product)
        home_dir = Path(home or env.get("USERPROFILE") or env.get("HOME") or Path.home())
        local_app_data = Path(
            env.get("LOCALAPPDATA")
            or (
                home_dir / "AppData" / "Local"
                if os.name == "nt"
                else home_dir / ".local" / "share"
            )
        )

        if product is ProductKind.TEAM:
            default_state = local_app_data / "Shortly"
            default_artifact = Path(
                documents or documents_dir(environ=env, home=home_dir)
            ) / "Shortly"
        elif product is ProductKind.PERSONAL:
            default_state = local_app_data / "ShortlyPersonal"
            default_artifact = Path(
                documents or documents_dir(environ=env, home=home_dir)
            ) / "Shortly Personal"
        else:
            default_state = Path(project_root)
            default_artifact = Path(project_root)

        state_root = Path(env.get("SHORTLY_USER_DATA") or default_state)
        data_root = Path(env.get("SHORTLY_DATA_DIR") or (state_root / "data"))
        artifact_root = Path(
            env.get("SHORTLY_ARTIFACT_DIR") or default_artifact
        )
        log_root = Path(env.get("SHORTLY_LOG_DIR") or (artifact_root / "logs"))
        tts_root = Path(env.get("SHORTLY_TTS_OUTPUT_DIR") or (artifact_root / "tts"))

        return cls(
            product=product,
            state_root=state_root,
            data_root=data_root,
            artifact_root=artifact_root,
            log_root=log_root,
            tts_root=tts_root,
        )
