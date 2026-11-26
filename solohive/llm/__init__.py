"""Фабрика LLM-клиентов."""

from __future__ import annotations

from ..config import Settings
from .base import BaseLLM, FatalLLMError, LLMError


def create_llm(settings: Settings) -> BaseLLM:
    if settings.provider == "gemini":
        from .gemini import GeminiLLM
        return GeminiLLM(settings)
    if settings.provider in ("openai", "openrouter", "ollama"):
        from .openai_compat import OpenAICompatLLM
        return OpenAICompatLLM(settings)
    if settings.provider == "anthropic":
        from .anthropic_client import AnthropicLLM
        return AnthropicLLM(settings)
    if settings.provider == "offline":
        from .mock import OfflineLLM
        return OfflineLLM(settings)
    if settings.provider == "mock":
        from .mock import MockLLM
        return MockLLM(settings)
    raise FatalLLMError(f"Неизвестный провайдер: {settings.provider}")


__all__ = ["BaseLLM", "LLMError", "FatalLLMError", "create_llm"]
