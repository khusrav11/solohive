"""Базовый LLM-клиент: кэш, ограничение параллелизма и частоты, повторы, смена модели, валидация JSON."""

from __future__ import annotations

import asyncio
import hashlib
import json
import random
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel, ValidationError

from ..config import Settings
from ..models import CallRecord
from .jsonutil import JSONExtractError, extract_json

T = TypeVar("T", bound=BaseModel)


class LLMError(RuntimeError):
    """Ошибка вызова модели."""


class FatalLLMError(LLMError):
    """Повторять бессмысленно (неверный ключ, запрещён доступ)."""


class ModelUnavailable(LLMError):
    """Модель недоступна или исчерпана квота — пробуем следующую модель из цепочки."""


class RateLimited(LLMError):
    def __init__(self, message: str, retry_after: float | None = None):
        super().__init__(message)
        self.retry_after = retry_after


class TransientLLMError(LLMError):
    """Временная ошибка (5xx, таймаут, обрезанный ответ) — повторяем."""


@dataclass
class LLMResponse:
    text: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    latency: float = 0.0
    cached: bool = False


class BaseLLM:
    provider = "base"

    def __init__(self, settings: Settings):
        self.settings = settings
        self.calls: list[CallRecord] = []
        # Примитивы asyncio создаются лениво внутри работающего цикла (совместимость с Python 3.9)
        self._sync: tuple[asyncio.Semaphore, asyncio.Lock, asyncio.Lock] | None = None
        self._loop = None
        self._last_start = 0.0
        self._chain = settings.models_chain
        self._model_idx = 0
        self.used_models: list[str] = []
        self.warnings: list[str] = []

    # --- реализуется провайдером -------------------------------------------------
    async def _generate(self, model: str, system: str, user: str, json_schema: dict | None,
                        effort: str | None = None) -> LLMResponse:
        """effort: None — режим модели по умолчанию, "low" — быстрый ответ без долгих рассуждений."""
        raise NotImplementedError

    # --- общая логика -----------------------------------------------------------
    def _primitives(self) -> tuple[asyncio.Semaphore, asyncio.Lock, asyncio.Lock]:
        loop = asyncio.get_running_loop()
        if self._sync is None or self._loop is not loop:
            self._sync = (asyncio.Semaphore(self.settings.concurrency), asyncio.Lock(), asyncio.Lock())
            self._loop = loop
        return self._sync

    @property
    def _sem(self) -> asyncio.Semaphore:
        return self._primitives()[0]

    @property
    def _rate_lock(self) -> asyncio.Lock:
        return self._primitives()[1]

    @property
    def _model_lock(self) -> asyncio.Lock:
        return self._primitives()[2]

    @property
    def model(self) -> str:
        return self._chain[min(self._model_idx, len(self._chain) - 1)]

    def _cache_path(self, model: str, system: str, user: str, effort: str | None = None) -> Path | None:
        if not self.settings.cache_dir:
            return None
        key = hashlib.sha256(
            json.dumps([self.provider, model, self.settings.temperature, effort, system, user], ensure_ascii=False).encode()
        ).hexdigest()
        return self.settings.cache_dir / f"{key}.json"

    async def _throttle(self) -> None:
        if self.settings.rpm <= 0:
            return
        async with self._rate_lock:
            interval = 60.0 / self.settings.rpm
            wait = self._last_start + interval - time.monotonic()
            if wait > 0:
                await asyncio.sleep(wait)
            self._last_start = time.monotonic()

    async def _advance_model(self, failed: str, reason: str) -> bool:
        async with self._model_lock:
            if self.model != failed:  # уже переключились из другой задачи
                return True
            if self._model_idx + 1 >= len(self._chain):
                return False
            self._model_idx += 1
            self.warnings.append(f"Модель {failed} недоступна ({reason[:160]}); переключение на {self.model}")
            return True

    async def generate(self, system: str, user: str, *, stage: str, json_schema: dict | None = None,
                       effort: str | None = None) -> LLMResponse:
        attempts = 0
        last_error = ""
        while True:
            model = self.model
            cache_path = self._cache_path(model, system, user, effort)
            if cache_path and cache_path.exists():
                data = json.loads(cache_path.read_text(encoding="utf-8"))
                resp = LLMResponse(text=data["text"], model=data["model"], input_tokens=data.get("in", 0),
                                   output_tokens=data.get("out", 0), latency=0.0, cached=True)
                self._record(stage, resp, attempts + 1)
                return resp
            attempts += 1
            try:
                async with self._sem:
                    await self._throttle()
                    t0 = time.monotonic()
                    resp = await self._generate(model, system, user, json_schema, effort)
                    resp.latency = time.monotonic() - t0
            except FatalLLMError as exc:
                self._record_error(stage, model, attempts, str(exc))
                raise
            except ModelUnavailable as exc:
                last_error = str(exc)
                if await self._advance_model(model, last_error):
                    continue
                self._record_error(stage, model, attempts, last_error)
                raise LLMError(f"Все модели цепочки недоступны: {last_error}") from exc
            except (RateLimited, TransientLLMError, asyncio.TimeoutError) as exc:
                last_error = str(exc) or exc.__class__.__name__
                if attempts > self.settings.max_retries:
                    # после исчерпания повторов при 429 пробуем следующую модель
                    if isinstance(exc, (RateLimited, TransientLLMError)) and await self._advance_model(model, last_error):
                        attempts = 0
                        continue
                    self._record_error(stage, model, attempts, last_error)
                    raise LLMError(f"[{stage}] не удалось получить ответ модели: {last_error}") from exc
                retry_after = getattr(exc, "retry_after", None)
                delay = retry_after if retry_after else min(60.0, 2.0 * 2 ** (attempts - 1))
                await asyncio.sleep(delay + random.uniform(0, 0.8))
                continue

            if model not in self.used_models:
                self.used_models.append(model)
            if cache_path:
                cache_path.parent.mkdir(parents=True, exist_ok=True)
                cache_path.write_text(
                    json.dumps({"text": resp.text, "model": resp.model, "in": resp.input_tokens,
                                "out": resp.output_tokens}, ensure_ascii=False),
                    encoding="utf-8",
                )
            self._record(stage, resp, attempts)
            return resp

    async def generate_json(self, system: str, user: str, schema: type[T], *, stage: str,
                            effort: str | None = None) -> T:
        """Вызов с валидацией ответа по pydantic-схеме; при ошибке — одна попытка ремонта."""
        json_schema = None
        resp = await self.generate(system, user, stage=stage, json_schema=json_schema, effort=effort)
        try:
            return schema.model_validate(extract_json(resp.text))
        except (JSONExtractError, ValidationError) as exc:
            if resp.cached and self.settings.cache_dir:
                self._cache_path(resp.model, system, user, effort).unlink(missing_ok=True)  # type: ignore[union-attr]
            repair_user = (
                user
                + "\n\n---\nТвой предыдущий ответ не прошёл проверку формата: "
                + str(exc)[:600]
                + "\nВерни ТОЛЬКО корректный JSON по указанной схеме, без пояснений и без ```."
                + " Если ответ получался слишком длинным — сократи тексты комментариев, но сохрани все находки."
            )
            resp2 = await self.generate(system, repair_user, stage=f"{stage}:repair", json_schema=json_schema,
                                        effort=effort)
            return schema.model_validate(extract_json(resp2.text))

    # --- учёт вызовов -----------------------------------------------------------
    def _record(self, stage: str, resp: LLMResponse, attempts: int) -> None:
        self.calls.append(CallRecord(stage=stage, model=resp.model, input_tokens=resp.input_tokens,
                                     output_tokens=resp.output_tokens, latency=round(resp.latency, 2),
                                     cached=resp.cached, attempts=attempts))
        if resp.model not in self.used_models:
            self.used_models.append(resp.model)

    def _record_error(self, stage: str, model: str, attempts: int, error: str) -> None:
        self.calls.append(CallRecord(stage=stage, model=model, attempts=attempts, ok=False, error=error[:300]))

    async def aclose(self) -> None:
        return None
