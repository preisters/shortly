"""전역 설정. 환경변수(.env 포함)에서 로드하며, 없으면 안전한 기본값을 씁니다."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from .path_policy import PathPolicy
from .product_profile import (
    FeatureManifest,
    ProductKind,
    feature_manifest_for,
    resolve_product_kind,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _load_dotenv(path: Path, *, override: bool = False) -> None:
    """의존성 없이 .env 를 읽어 os.environ 에 채웁니다.

    override=False 이면 이미 있는 환경변수는 유지합니다.
    """
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if not key:
            continue
        if override or key not in os.environ:
            os.environ[key] = value


def _portable_enabled() -> bool:
    return os.environ.get("SHORTLY_PORTABLE", "").strip() not in (
        "", "0", "false", "False"
    )


def current_product_kind() -> ProductKind:
    return resolve_product_kind(
        os.environ.get("SHORTLY_PRODUCT"),
        portable=_portable_enabled(),
    )


def current_path_policy() -> PathPolicy:
    return PathPolicy.for_product(
        current_product_kind(),
        project_root=PROJECT_ROOT,
    )


def user_data_dir() -> Path:
    """Current product's internal state root."""
    return current_path_policy().state_root


def env_file_path() -> Path:
    """설정 저장에 쓰는 .env 경로(포터블/설치형은 AppData)."""
    if _portable_enabled() or current_product_kind() is not ProductKind.LEGACY:
        return current_path_policy().env_file
    return PROJECT_ROOT / ".env"


def _bootstrap_env_files() -> None:
    """앱 기본값 → 사용자 .env 순으로 로드."""
    # 개발용 프로젝트 .env
    _load_dotenv(PROJECT_ROOT / ".env", override=False)
    # 설치  Bundled 기본값
    _load_dotenv(PROJECT_ROOT / "env.defaults", override=False)
    # 명시적 제품 런처는 제품별 사용자 .env 로 기본값을 덮어씀.
    if _portable_enabled() or current_product_kind() is not ProductKind.LEGACY:
        ud = user_data_dir()
        ud.mkdir(parents=True, exist_ok=True)
        user_env = ud / ".env"
        defaults = PROJECT_ROOT / "env.defaults"
        if defaults.is_file() and not user_env.is_file():
            user_env.write_text(defaults.read_text(encoding="utf-8"), encoding="utf-8")
        _load_dotenv(user_env, override=True)


_bootstrap_env_files()


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, "").strip() or default)
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, "").strip() or default)
    except ValueError:
        return default


# 제공자별 기본 모델
_DEFAULT_TEXT_MODELS = {
    "gemini": "gemini-1.5-pro",
    "openai": "gpt-4o",
    "anthropic": "claude-sonnet-4-6",
}
_DEFAULT_VISION_MODELS = {
    "gemini": "gemini-1.5-pro",
    "openai": "gpt-4o",
    "anthropic": "claude-sonnet-4-6",
}

# 팀용 포터블 빌드에서 강제하는 모델(변경 불가)
PORTABLE_ANALYSIS_MODEL = "claude-sonnet-5"
PORTABLE_GENERATION_MODEL = "claude-opus-4-8"


@dataclass
class Settings:
    product: ProductKind = ProductKind.LEGACY
    features: FeatureManifest = field(
        default_factory=lambda: feature_manifest_for(ProductKind.LEGACY)
    )

    llm_provider: str = "gemini"
    text_model: str = ""
    vision_model: str = ""

    # 가이드/대본 '생성' 전용 모델(창작 품질 우선 → 상용 모델 권장).
    # gen_provider 가 비어 있으면 위 분석용 설정(llm_provider)을 그대로 사용합니다.
    gen_provider: str = ""
    gen_text_model: str = ""

    gemini_api_key: str = ""
    openai_api_key: str = ""
    anthropic_api_key: str = ""
    typecast_api_key: str = ""

    # TTS(Typecast) 기본값 — 캐릭터 '지훈', 1.3배속, toneup 100%
    tts_voice_id: str = "tc_61f0859907085fc68561c9a1"
    tts_model: str = "ssfm-v30"
    tts_language: str = "kor"
    tts_emotion: str = "toneup"
    tts_intensity: float = 1.0
    tts_tempo: float = 1.3
    # TTS 음성 파일 저장 위치(프로젝트별 하위 폴더가 이 아래에 생성됨)
    tts_output_dir: Path = field(
        default_factory=lambda: Path(r"d:\youtube\챙 브랜드\챙맛\TTS"))
    # 캡컷 내보내기 시 복사·활용할 기본 템플릿 프로젝트명
    capcut_template: str = "챙맛 템플릿"

    # 로컬 OpenAI 호환 서버(Ollama/LM Studio/llama.cpp)
    local_base_url: str = "http://localhost:11434/v1"
    local_api_key: str = "ollama"
    # Qwen3 등 reasoning 모델의 thinking 을 꺼서 속도·토큰 절약(기본 켬)
    local_no_think: bool = True
    # 로컬 호출 완료 직후 모델을 RAM/VRAM 에서 해제(개인판 기본 켬)
    local_auto_unload: bool = True

    data_dir: Path = field(default_factory=lambda: PROJECT_ROOT / "data")
    artifact_dir: Path = field(default_factory=lambda: PROJECT_ROOT)
    log_dir: Path = field(default_factory=lambda: PROJECT_ROOT / "logs")
    sfx_dir: Path = field(
        default_factory=lambda: Path(r"d:\youtube\DaelhyukGisang\sfx_sound")
    )

    max_shorts: int = 30
    min_views: int = 0

    # 같은 공유기(LAN)의 다른 PC에서도 접속하도록 기본은 0.0.0.0(모든 인터페이스) 바인딩.
    # 연산·다운로드는 항상 이 PC(서버)에서 수행됩니다.
    host: str = "0.0.0.0"
    port: int = 8756
    # True 면 사설망(LAN)·로컬에서만 접속 허용. 외부(공인) IP 는 차단.
    lan_only: bool = True

    # 주제 대본 생성 시 웹 조사로 팩트/각도 보강
    research_enabled: bool = True
    research_max_results: int = 8
    research_max_queries: int = 5
    # 대본 생성 후 시점·주체 최종 팩트체크
    factcheck_enabled: bool = True

    # 팀용 포터블: AI 선택 잠금 + 효과음 기능 비활성
    portable: bool = False
    sfx_enabled: bool = True

    # 오디오 처리 공통 샘플레이트(지문 매칭용)
    audio_sr: int = 11025

    @classmethod
    def load(cls) -> "Settings":
        portable_flag = _env("SHORTLY_PORTABLE", "0") not in (
            "0", "false", "False", ""
        )
        product = resolve_product_kind(
            _env("SHORTLY_PRODUCT"),
            portable=portable_flag,
        )
        features = feature_manifest_for(product)
        policy = PathPolicy.for_product(product, project_root=PROJECT_ROOT)
        portable = portable_flag or product is ProductKind.TEAM
        provider = (_env("SHORTLY_LLM_PROVIDER", "gemini") or "gemini").lower()
        text_model = _env("SHORTLY_TEXT_MODEL") or _DEFAULT_TEXT_MODELS.get(provider, "")
        vision_model = _env("SHORTLY_VISION_MODEL") or _DEFAULT_VISION_MODELS.get(provider, "")
        gen_provider = (_env("SHORTLY_GEN_PROVIDER", "") or "").lower()
        gen_text_model = _env("SHORTLY_GEN_MODEL")
        data_dir = _env("SHORTLY_DATA_DIR")
        sfx_dir = _env("SHORTLY_SFX_DIR")
        tts_out = _env("SHORTLY_TTS_OUTPUT_DIR")

        if portable:
            # 팀 배포용: 모델·제공자 고정, 데이터는 AppData(사용자 폴더)
            provider = "anthropic"
            text_model = PORTABLE_ANALYSIS_MODEL
            vision_model = PORTABLE_ANALYSIS_MODEL
            gen_provider = "anthropic"
            gen_text_model = PORTABLE_GENERATION_MODEL
            if not data_dir:
                data_dir = str(policy.data_root)
            if not tts_out:
                tts_out = str(policy.tts_root)
        elif product is ProductKind.PERSONAL:
            if not data_dir:
                data_dir = str(policy.data_root)
            if not tts_out:
                tts_out = str(policy.tts_root)

        return cls(
            product=product,
            features=features,
            llm_provider=provider,
            text_model=text_model,
            vision_model=vision_model,
            gen_provider=gen_provider,
            gen_text_model=gen_text_model,
            gemini_api_key=_env("GEMINI_API_KEY"),
            openai_api_key=_env("OPENAI_API_KEY"),
            anthropic_api_key=_env("ANTHROPIC_API_KEY"),
            typecast_api_key=_env("TYPECAST_API_KEY"),
            tts_voice_id=_env("SHORTLY_TTS_VOICE_ID") or "tc_61f0859907085fc68561c9a1",
            tts_model=_env("SHORTLY_TTS_MODEL") or "ssfm-v30",
            tts_language=_env("SHORTLY_TTS_LANGUAGE") or "kor",
            tts_emotion=_env("SHORTLY_TTS_EMOTION") or "toneup",
            tts_intensity=_env_float("SHORTLY_TTS_INTENSITY", 1.0),
            tts_tempo=_env_float("SHORTLY_TTS_TEMPO", 1.3),
            tts_output_dir=Path(tts_out) if tts_out else Path(r"d:\youtube\챙 브랜드\챙맛\TTS"),
            capcut_template=_env("SHORTLY_CAPCUT_TEMPLATE") or "챙맛 템플릿",
            local_base_url=_env("SHORTLY_LOCAL_BASE_URL", "http://localhost:11434/v1")
            or "http://localhost:11434/v1",
            local_api_key=_env("SHORTLY_LOCAL_API_KEY", "ollama") or "ollama",
            local_no_think=_env("SHORTLY_LOCAL_NO_THINK", "1") not in ("0", "false", "False"),
            local_auto_unload=_env(
                "SHORTLY_LOCAL_AUTO_UNLOAD", "1"
            ) not in ("0", "false", "False", ""),
            data_dir=Path(data_dir) if data_dir else PROJECT_ROOT / "data",
            artifact_dir=policy.artifact_root,
            log_dir=policy.log_root,
            sfx_dir=Path(sfx_dir) if sfx_dir else Path(r"d:\youtube\DaelhyukGisang\sfx_sound"),
            max_shorts=_env_int("SHORTLY_MAX_SHORTS", 30),
            min_views=_env_int("SHORTLY_MIN_VIEWS", 0),
            host=_env("SHORTLY_HOST", "0.0.0.0") or "0.0.0.0",
            port=_env_int("SHORTLY_PORT", 8756),
            lan_only=_env("SHORTLY_LAN_ONLY", "1") not in ("0", "false", "False", ""),
            research_enabled=_env("SHORTLY_RESEARCH", "1") not in ("0", "false", "False", ""),
            research_max_results=_env_int("SHORTLY_RESEARCH_MAX_RESULTS", 8),
            research_max_queries=_env_int("SHORTLY_RESEARCH_MAX_QUERIES", 5),
            factcheck_enabled=_env("SHORTLY_FACTCHECK", "1") not in ("0", "false", "False", ""),
            portable=portable,
            sfx_enabled=features.sfx,
        )

    def key_for(self, provider: str) -> str:
        """제공자별 API 키(로컬은 local_api_key)."""
        return {
            "gemini": self.gemini_api_key,
            "openai": self.openai_api_key,
            "anthropic": self.anthropic_api_key,
            "local": self.local_api_key,
            "ollama": self.local_api_key,
            "lmstudio": self.local_api_key,
            "llamacpp": self.local_api_key,
        }.get(provider, "")

    def api_key_for_provider(self) -> Optional[str]:
        return self.key_for(self.llm_provider) or None

    def typecast_config(self):
        """현재 설정으로 Typecast TTS 설정 객체를 만듭니다."""
        from .tts.typecast import TypecastConfig
        return TypecastConfig(
            api_key=self.typecast_api_key,
            voice_id=self.tts_voice_id,
            model=self.tts_model,
            language=self.tts_language,
            emotion_preset=self.tts_emotion,
            emotion_intensity=self.tts_intensity,
            tempo=self.tts_tempo,
        )

    @property
    def channels_dir(self) -> Path:
        return self.data_dir / "channels"

    @property
    def sfx_index_dir(self) -> Path:
        return self.data_dir / "sfx_index"

    def ensure_dirs(self) -> None:
        for d in (self.data_dir, self.channels_dir, self.sfx_index_dir):
            d.mkdir(parents=True, exist_ok=True)


_settings: Optional[Settings] = None


def get_settings(reload: bool = False) -> Settings:
    global _settings
    if _settings is None or reload:
        if reload:
            _bootstrap_env_files()
        _settings = Settings.load()
    return _settings


# 웹/설정에서 저장 가능한 키 목록
SAVABLE_KEYS = {
    "SHORTLY_PORTABLE",
    "SHORTLY_LLM_PROVIDER",
    "SHORTLY_TEXT_MODEL",
    "SHORTLY_VISION_MODEL",
    "SHORTLY_GEN_PROVIDER",
    "SHORTLY_GEN_MODEL",
    "GEMINI_API_KEY",
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "TYPECAST_API_KEY",
    "SHORTLY_LOCAL_BASE_URL",
    "SHORTLY_LOCAL_API_KEY",
    "SHORTLY_LOCAL_AUTO_UNLOAD",
    "SHORTLY_MAX_SHORTS",
    "SHORTLY_MIN_VIEWS",
    "SHORTLY_TTS_VOICE_ID",
    "SHORTLY_TTS_TEMPO",
    "SHORTLY_TTS_EMOTION",
    "SHORTLY_TTS_OUTPUT_DIR",
    "SHORTLY_CAPCUT_TEMPLATE",
}

# 빈 값 저장(초기화)을 허용하는 키. 그 외(특히 API 키)는 빈 값이면 기존 값을 유지합니다.
CLEARABLE_KEYS = {
    "SHORTLY_GEN_PROVIDER",
    "SHORTLY_GEN_MODEL",
}


def save_settings(updates: dict) -> "Settings":
    """설정을 .env 파일에 저장하고 즉시 반영합니다.

    updates 는 {환경변수명: 값} 형태. 빈 문자열이면 해당 키를 건너뜁니다(기존 값 유지).
    포터블/설치형에서는 AppData\\Shortly\\.env 에 저장합니다.
    """
    env_path = env_file_path()
    env_path.parent.mkdir(parents=True, exist_ok=True)
    lines = env_path.read_text(encoding="utf-8").splitlines() if env_path.is_file() else []

    # 기존 키 -> 라인 인덱스
    index: dict = {}
    for i, raw in enumerate(lines):
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key = line.split("=", 1)[0].strip()
        index[key] = i

    for key, value in updates.items():
        if key not in SAVABLE_KEYS:
            continue
        value = "" if value is None else str(value).strip()
        if value == "" and key not in CLEARABLE_KEYS:
            continue  # 빈 값은 변경하지 않음(특히 API 키)
        os.environ[key] = value  # 런타임 즉시 반영
        new_line = f"{key}={value}"
        if key in index:
            lines[index[key]] = new_line
        else:
            lines.append(new_line)

    env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return get_settings(reload=True)
