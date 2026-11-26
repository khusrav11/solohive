"""Настройки агента из переменных окружения и файла .env."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

PROVIDERS = ("gemini", "openai", "anthropic", "ollama", "mock", "offline")

DEFAULT_MODELS = {
    "gemini": "gemini-3.8-flash",
    "openai": "gpt-4o-mini",
    "anthropic": "claude-sonnet-5-5",
    "ollama": "qwen2.5:7b",
    "mock": "mock-model",
    "offline": "без LLM (только правила)",
}

# Запасные модели Gemini: при перегрузке основной (503) запрос временно уходит на запасную,
# при отсутствии квоты модель исключается до конца прогона.
DEFAULT_GEMINI_FALLBACKS = "gemini-3.6-flash,gemini-3.5-flash-lite"


class ConfigError(RuntimeError):
    """Ошибка конфигурации, понятная пользователю."""


def _load_dotenv() -> None:
    try:
        from dotenv import load_dotenv
    except ImportError:  # python-dotenv необязателен
        return
    # Ищем .env в текущей папке и в корне проекта
    for candidate in (Path.cwd() / ".env", Path(__file__).resolve().parents[1] / ".env"):
        if candidate.exists():
            load_dotenv(candidate, override=False)


def _env(name: str, default: str | None = None) -> str | None:
    value = os.getenv(name)
    if value is None or value.strip() == "" or value.strip().startswith("your_"):
        return default
    return value.strip()


def _split(value: str | None) -> list[str]:
    return [v.strip() for v in (value or "").split(",") if v.strip()]


@dataclass
class Settings:
    provider: str
    model: str
    fallback_models: list[str] = field(default_factory=list)
    api_key: str | None = None
    base_url: str | None = None
    temperature: float = 0.2
    max_output_tokens: int = 32768
    concurrency: int = 3
    rpm: int = 0                    # 0 = без ограничения частоты (429 всё равно обрабатывается)
    timeout: float = 240.0
    max_retries: int = 5
    max_wait: float = 150.0          # сколько максимум ждать ответа на один запрос с учётом повторов, с
    cooldown_base: float = 20.0      # «остывание» модели после перегрузки (503): 20 → 40 → 90 с
    thinking_level: str | None = None
    review_batch_size: int = 12
    cache_dir: Path | None = Path(".solohive_cache")

    @property
    def models_chain(self) -> list[str]:
        chain = [self.model] + [m for m in self.fallback_models if m != self.model]
        return chain

    @classmethod
    def from_env(
        cls,
        provider: str | None = None,
        model: str | None = None,
        use_cache: bool = True,
    ) -> "Settings":
        _load_dotenv()
        provider = (provider or _env("LLM_PROVIDER") or "").lower() or _autodetect_provider()
        if provider not in PROVIDERS:
            raise ConfigError(f"Неизвестный провайдер LLM: {provider!r}. Допустимо: {', '.join(PROVIDERS)}")

        api_key = base_url = None
        fallbacks: list[str] = []
        if provider == "gemini":
            api_key = _env("GEMINI_API_KEY") or _env("GOOGLE_API_KEY")
            env_model = _env("GEMINI_MODEL")
            fallbacks = _split(_env("GEMINI_FALLBACK_MODELS", DEFAULT_GEMINI_FALLBACKS))
        elif provider == "openai":
            api_key = _env("OPENAI_API_KEY")
            base_url = _env("OPENAI_BASE_URL")
            env_model = _env("OPENAI_MODEL")
            fallbacks = _split(_env("OPENAI_FALLBACK_MODELS"))
        elif provider == "anthropic":
            api_key = _env("ANTHROPIC_API_KEY")
            env_model = _env("ANTHROPIC_MODEL")
            fallbacks = _split(_env("ANTHROPIC_FALLBACK_MODELS"))
        elif provider == "ollama":
            base_url = (_env("OLLAMA_BASE_URL", "http://localhost:11434") or "").rstrip("/") + "/v1"
            api_key = "ollama"
            env_model = _env("OLLAMA_MODEL")
        else:  # mock / offline
            env_model = None

        if provider in ("gemini", "openai", "anthropic") and not api_key:
            key_name = {"gemini": "GEMINI_API_KEY", "openai": "OPENAI_API_KEY", "anthropic": "ANTHROPIC_API_KEY"}[provider]
            raise ConfigError(
                f"Не найден ключ {key_name}. Скопируйте .env.example в .env и впишите ключ "
                f"(для Gemini бесплатный ключ: https://aistudio.google.com/apikey)."
            )

        # LLM_MODEL переопределяет модель для любого провайдера
        chosen = model or _env("LLM_MODEL") or env_model or DEFAULT_MODELS[provider]
        if model:  # явный выбор модели в CLI отключает автозамену
            fallbacks = []

        return cls(
            provider=provider,
            model=chosen,
            fallback_models=fallbacks,
            api_key=api_key,
            base_url=base_url,
            temperature=float(_env("LLM_TEMPERATURE", "0.2")),
            max_output_tokens=int(_env("LLM_MAX_OUTPUT_TOKENS", "32768")),
            concurrency=max(1, int(_env("LLM_CONCURRENCY", "3"))),
            rpm=max(0, int(_env("LLM_RPM", "0"))),
            timeout=float(_env("LLM_TIMEOUT", "240")),
            max_retries=max(0, int(_env("LLM_MAX_RETRIES", "5"))),
            max_wait=float(_env("LLM_MAX_WAIT", "150")),
            thinking_level=_env("GEMINI_THINKING_LEVEL"),
            review_batch_size=max(1, int(_env("REVIEW_BATCH_SIZE", "12"))),
            cache_dir=Path(_env("SOLOHIVE_CACHE_DIR", ".solohive_cache")) if use_cache else None,
        )


def _autodetect_provider() -> str:
    if _env("GEMINI_API_KEY") or _env("GOOGLE_API_KEY"):
        return "gemini"
    if _env("OPENAI_API_KEY"):
        return "openai"
    if _env("ANTHROPIC_API_KEY"):
        return "anthropic"
    if _env("OLLAMA_MODEL"):
        return "ollama"
    raise ConfigError(
        "Не задан ключ LLM. Скопируйте .env.example в .env и укажите GEMINI_API_KEY "
        "(бесплатно: https://aistudio.google.com/apikey) или другой провайдер."
    )
