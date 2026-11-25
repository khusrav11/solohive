"""Клиент Anthropic Claude."""

from __future__ import annotations

from .base import (BaseLLM, FatalLLMError, LLMResponse, ModelUnavailable, RateLimited,
                   TransientLLMError)


class AnthropicLLM(BaseLLM):
    provider = "anthropic"

    def __init__(self, settings):
        super().__init__(settings)
        try:
            import anthropic
        except ImportError as exc:  # pragma: no cover
            raise FatalLLMError("Не установлен пакет anthropic: pip install anthropic") from exc
        self._anthropic = anthropic
        self._client = anthropic.AsyncAnthropic(api_key=settings.api_key, timeout=settings.timeout, max_retries=0)

    async def _generate(self, model, system, user, json_schema, effort=None) -> LLMResponse:
        an = self._anthropic
        try:
            resp = await self._client.messages.create(
                model=model, system=system, max_tokens=min(self.settings.max_output_tokens, 32000),
                temperature=self.settings.temperature,
                messages=[{"role": "user", "content": user}],
            )
        except an.AuthenticationError as exc:
            raise FatalLLMError(f"неверный API-ключ: {exc}") from exc
        except an.NotFoundError as exc:
            raise ModelUnavailable(f"модель не найдена: {exc}") from exc
        except an.RateLimitError as exc:
            raise RateLimited(f"превышен лимит запросов: {exc}") from exc
        except (an.APITimeoutError, an.APIConnectionError, an.InternalServerError) as exc:
            raise TransientLLMError(f"временная ошибка: {exc}") from exc
        except an.BadRequestError as exc:
            raise FatalLLMError(f"ошибка запроса: {exc}") from exc
        text = "".join(getattr(b, "text", "") for b in resp.content)
        if not text:
            raise TransientLLMError("пустой ответ модели")
        return LLMResponse(text=text, model=model, input_tokens=resp.usage.input_tokens,
                           output_tokens=resp.usage.output_tokens)

    async def aclose(self) -> None:
        await self._client.close()
