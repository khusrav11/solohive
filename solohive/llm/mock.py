"""Модель-заглушка для тестов и офлайн-демо: отвечает по сценарию для каждого этапа."""

from __future__ import annotations

import json
from typing import Callable

from .base import BaseLLM, LLMResponse

Responder = Callable[[str, str, str], "str | dict"]


class MockLLM(BaseLLM):
    provider = "mock"

    def __init__(self, settings, responders: dict[str, Responder] | None = None):
        super().__init__(settings)
        self.responders = responders or {}
        self.prompts: list[tuple[str, str, str]] = []

    async def _generate(self, model, system, user, json_schema, effort=None) -> LLMResponse:
        stage = getattr(self, "_current_stage", "")
        self.prompts.append((stage, system, user))
        for key, fn in self.responders.items():
            if stage.startswith(key):
                out = fn(stage, system, user)
                text = out if isinstance(out, str) else json.dumps(out, ensure_ascii=False)
                return LLMResponse(text=text, model=model, input_tokens=len(user) // 4, output_tokens=len(text) // 4)
        return LLMResponse(text="{}", model=model)

    async def generate(self, system, user, *, stage, json_schema=None, effort=None, cache=True):
        self._current_stage = stage
        return await super().generate(system, user, stage=stage, json_schema=json_schema, effort=effort, cache=cache)


class OfflineLLM(BaseLLM):
    """Режим без LLM: любой вызов модели недоступен, конвейер работает на правилах и детерминированной нарезке."""

    provider = "offline"

    async def generate(self, system, user, *, stage, json_schema=None, effort=None, cache=True):
        from .base import LLMError
        raise LLMError("офлайн-режим: LLM не используется")
