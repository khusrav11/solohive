"""Клиент Google Gemini через REST API (generateContent).

Прямой REST через httpx вместо SDK: работает на Python 3.9+, не зависит от версии google-genai
и полностью контролирует повторы (429 с retryDelay, 5xx) и автозамену модели.
"""

from __future__ import annotations

import json
import re

import httpx

from .base import (BaseLLM, FatalLLMError, LLMResponse, ModelUnavailable, RateLimited,
                   TransientLLMError)

API_BASE = "https://generativelanguage.googleapis.com/v1beta"
_RETRY_DELAY = re.compile(r"retry(?:Delay|_delay)?\W{0,4}(\d+(?:\.\d+)?)\s*s", re.IGNORECASE)


class GeminiLLM(BaseLLM):
    provider = "gemini"

    def __init__(self, settings, transport: httpx.AsyncBaseTransport | None = None):
        super().__init__(settings)
        self._base = (settings.base_url or API_BASE).rstrip("/")
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(settings.timeout, connect=20.0),
            headers={"x-goog-api-key": settings.api_key or "", "Content-Type": "application/json"},
            transport=transport,
        )
        self._thinking_supported = True
        self._json_mode_supported = True

    def _body(self, system: str, user: str, effort: str | None) -> dict:
        config: dict = {"temperature": self.settings.temperature, "maxOutputTokens": self.settings.max_output_tokens}
        if self._json_mode_supported:
            config["responseMimeType"] = "application/json"
        level = self.settings.thinking_level or effort
        if level and self._thinking_supported:
            config["thinkingConfig"] = {"thinkingLevel": level.upper()}
        return {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": user}]}],
            "generationConfig": config,
        }

    async def _generate(self, model, system, user, json_schema, effort=None) -> LLMResponse:
        url = f"{self._base}/models/{model}:generateContent"
        for _ in range(3):  # внутренние повторы, если модель не поддерживает опцию запроса
            try:
                resp = await self._client.post(url, json=self._body(system, user, effort))
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                raise TransientLLMError(f"сетевая ошибка/таймаут: {exc.__class__.__name__}: {exc}") from exc
            if resp.status_code == 200:
                break
            msg = _error_text(resp)
            low = msg.lower()
            if resp.status_code == 400 and "thinking" in low and self._thinking_supported:
                self._thinking_supported = False
                continue
            if resp.status_code == 400 and ("mime" in low or "json mode" in low) and self._json_mode_supported:
                self._json_mode_supported = False
                continue
            raise _map_error(resp.status_code, msg)
        else:  # pragma: no cover
            raise TransientLLMError("не удалось подобрать параметры запроса")

        data = resp.json()
        cands = data.get("candidates") or []
        if not cands:
            feedback = data.get("promptFeedback", {})
            raise TransientLLMError(f"пустой ответ модели (promptFeedback={feedback})")
        parts = (cands[0].get("content") or {}).get("parts") or []
        text = "".join(p.get("text", "") for p in parts if not p.get("thought"))
        reason = str(cands[0].get("finishReason") or "STOP")
        if reason not in ("STOP", "MAX_TOKENS", "FINISH_REASON_UNSPECIFIED"):
            raise TransientLLMError(f"ответ модели прерван (finishReason={reason})")
        if len(text.strip()) < 3:
            # модель «сорвалась» и вернула 1–2 символа — повторяем, а не тратим запрос на ремонт JSON
            raise TransientLLMError(f"пустой ответ модели (finishReason={reason})")
        usage = data.get("usageMetadata") or {}
        return LLMResponse(
            text=text,
            model=model,
            input_tokens=int(usage.get("promptTokenCount", 0) or 0),
            output_tokens=int((usage.get("candidatesTokenCount", 0) or 0) + (usage.get("thoughtsTokenCount", 0) or 0)),
        )

    async def aclose(self) -> None:
        await self._client.aclose()


def _error_text(resp: httpx.Response) -> str:
    try:
        err = resp.json().get("error", {})
        return f"{resp.status_code} {err.get('status', '')} {err.get('message', '')} {json.dumps(err.get('details', []))}"
    except Exception:  # noqa: BLE001
        return f"{resp.status_code} {resp.text[:500]}"


def _map_error(code: int, msg: str) -> Exception:
    low = msg.lower()
    if code == 429 or "resource_exhausted" in low:
        if "limit: 0" in low or "perday" in low or "per_day" in low:
            return ModelUnavailable(f"исчерпана/недоступна квота бесплатного тарифа: {msg[:300]}")
        m = _RETRY_DELAY.search(msg)
        return RateLimited(f"превышен лимит запросов (429): {msg[:200]}", float(m.group(1)) if m else None)
    if code == 404:
        return ModelUnavailable(f"модель не найдена: {msg[:300]}")
    if code == 401 or "api key not valid" in low or "api_key_invalid" in low:
        return FatalLLMError(f"неверный API-ключ Gemini: {msg[:300]}")
    if code == 403:
        if "model" in low:
            return ModelUnavailable(f"нет доступа к модели: {msg[:300]}")
        return FatalLLMError(f"доступ запрещён (проверьте ключ и проект в AI Studio): {msg[:300]}")
    if code == 400 and "location" in low and "not supported" in low:
        return FatalLLMError(f"Gemini API недоступен в вашем регионе: {msg[:300]}")
    if code in (408, 409) or code >= 500:
        return TransientLLMError(f"временная ошибка сервиса ({code}): {msg[:200]}")
    if code == 400 and "model" in low and ("not supported" in low or "not found" in low):
        return ModelUnavailable(f"модель не поддерживает запрос: {msg[:300]}")
    return FatalLLMError(f"ошибка запроса к Gemini ({code}): {msg[:400]}")
