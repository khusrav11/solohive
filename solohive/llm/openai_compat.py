"""Клиент для OpenAI и совместимых API (OpenRouter, DeepSeek, Ollama, vLLM)."""

from __future__ import annotations

from .base import (BaseLLM, FatalLLMError, LLMResponse, ModelUnavailable, RateLimited,
                   TransientLLMError)


class OpenAICompatLLM(BaseLLM):
    provider = "openai"

    def __init__(self, settings):
        super().__init__(settings)
        try:
            import openai
        except ImportError as exc:  # pragma: no cover
            raise FatalLLMError("Не установлен пакет openai: pip install openai") from exc
        self._openai = openai
        headers = None
        if "openrouter.ai" in (settings.base_url or ""):
            # необязательная атрибуция приложения в OpenRouter
            headers = {"HTTP-Referer": "https://github.com/khusrav11/solohive", "X-Title": "SoloHive"}
        self._client = openai.AsyncOpenAI(api_key=settings.api_key, base_url=settings.base_url,
                                          timeout=settings.timeout, max_retries=0, default_headers=headers)
        if settings.provider in ("ollama", "openrouter"):
            self.provider = settings.provider
        # Параметры, которые некоторые модели не принимают, отключаются после первой ошибки 400
        self._use_temperature = True
        self._use_json_format = True
        self._max_tokens_key = "max_tokens"

    async def _generate(self, model, system, user, json_schema, effort=None) -> LLMResponse:
        oa = self._openai
        for _ in range(4):
            kwargs = dict(model=model, messages=[{"role": "system", "content": system},
                                                 {"role": "user", "content": user}])
            kwargs[self._max_tokens_key] = self.settings.max_output_tokens
            if self._use_temperature:
                kwargs["temperature"] = self.settings.temperature
            if self._use_json_format:
                kwargs["response_format"] = {"type": "json_object"}
            try:
                resp = await self._client.chat.completions.create(**kwargs)
                break
            except oa.BadRequestError as exc:
                low = str(exc).lower()
                if "temperature" in low and self._use_temperature:
                    self._use_temperature = False
                    continue
                if "response_format" in low and self._use_json_format:
                    self._use_json_format = False
                    continue
                if "max_tokens" in low and self._max_tokens_key == "max_tokens":
                    self._max_tokens_key = "max_completion_tokens"
                    continue
                raise FatalLLMError(f"ошибка запроса ({exc})") from exc
            except oa.AuthenticationError as exc:
                raise FatalLLMError(f"неверный API-ключ: {exc}") from exc
            except oa.NotFoundError as exc:
                raise ModelUnavailable(f"модель не найдена: {exc}") from exc
            except oa.PermissionDeniedError as exc:
                raise ModelUnavailable(f"нет доступа к модели: {exc}") from exc
            except oa.RateLimitError as exc:
                if "insufficient_quota" in str(exc):
                    raise ModelUnavailable(f"исчерпана квота: {exc}") from exc
                raise RateLimited(f"превышен лимит запросов: {exc}") from exc
            except (oa.APITimeoutError, oa.APIConnectionError, oa.InternalServerError) as exc:
                raise TransientLLMError(f"временная ошибка: {exc}") from exc
            except oa.APIStatusError as exc:
                code = getattr(exc, "status_code", 0) or 0
                if code == 402:
                    raise FatalLLMError("на счёте провайдера закончились кредиты (402) — пополните баланс") from exc
                if code in (408, 409, 425) or code >= 500:
                    raise TransientLLMError(f"временная ошибка ({code}): {exc}") from exc
                raise FatalLLMError(f"ошибка API ({code}): {exc}") from exc
        else:  # pragma: no cover
            raise TransientLLMError("не удалось подобрать параметры запроса")
        text = resp.choices[0].message.content if resp.choices else None
        if not text or len(text.strip()) < 3:
            # OpenRouter может вернуть 200 с ошибкой провайдера внутри — это повод повторить, а не результат
            raise TransientLLMError("пустой ответ модели")
        usage = resp.usage
        return LLMResponse(text=text, model=model,
                           input_tokens=getattr(usage, "prompt_tokens", 0) or 0,
                           output_tokens=getattr(usage, "completion_tokens", 0) or 0)

    async def aclose(self) -> None:
        await self._client.close()
