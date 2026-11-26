"""Базовый LLM-клиент: кэш, параллелизм, частота запросов, умная маршрутизация между моделями, валидация JSON.

Маршрутизация по типу ошибки:
- 503/500/тайм-аут (перегрузка) → модель «остывает» 20–90 с, ЭТОТ запрос уходит на следующую модель;
  когда основная модель освободится, следующие запросы снова идут на неё;
- 429 по минутному лимиту → ждём retryDelay из ответа (если недолго) или переключаемся на время ожидания;
- 404 / нет квоты бесплатного тарифа / дневной лимит → модель исключается до конца прогона;
- все модели исключены → «предохранитель»: остальные вызовы сразу завершаются ошибкой, и прогон
  за секунды доводится в режиме правил вместо долгого ожидания;
- неверный ключ → фатальная ошибка с понятным сообщением.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import random
import time
from collections import Counter
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
    """Модель не существует или у проекта нет на неё квоты — исключаем до конца прогона."""


class RateLimited(LLMError):
    def __init__(self, message: str, retry_after: float | None = None):
        super().__init__(message)
        self.retry_after = retry_after


class TransientLLMError(LLMError):
    """Временная ошибка (перегрузка 503, 5xx, тайм-аут, пустой ответ) — повторяем, можно на другой модели."""


@dataclass
class LLMResponse:
    text: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    latency: float = 0.0
    cached: bool = False


def short_reason(exc: BaseException) -> str:
    """Короткая человекочитаемая причина для журналов и консоли."""
    import re as _re
    msg = _re.sub(r"^\[[^\]]+\]\s*", "", str(exc))
    low = msg.lower()
    if "503" in msg or "overload" in low or "high demand" in low or "unavailable" in low:
        return "сервис Gemini перегружен (503)"
    if "квот" in low or "quota" in low or "429" in msg:
        return "исчерпана квота или лимит запросов (429)"
    if "не найдена" in low or "404" in msg:
        return "модель не найдена (404)"
    if "тайм-аут" in low or "timeout" in low:
        return "тайм-аут запроса"
    first = msg.strip().splitlines()[0] if msg.strip() else exc.__class__.__name__
    return first[:140]


class BaseLLM:
    provider = "base"

    def __init__(self, settings: Settings):
        self.settings = settings
        self.calls: list[CallRecord] = []
        self.used_models: list[str] = []
        self.warnings: list[str] = []
        self._chain = settings.models_chain
        self._dead: dict[str, str] = {}            # модель → причина исключения
        self._cool: dict[str, float] = {}          # модель → monotonic-время окончания «остывания»
        self._fails: Counter[str] = Counter()      # подряд идущие временные сбои модели
        self.events: Counter[tuple[str, str]] = Counter()   # (модель, причина) → сколько раз
        # Примитивы asyncio создаются лениво внутри работающего цикла (совместимость с Python 3.9)
        self._sync: tuple[asyncio.Semaphore, asyncio.Lock] | None = None
        self._loop = None
        self._last_start = 0.0
        self._failed_in_row = 0                    # подряд проваленные запросы (после всех повторов)
        self._tripped = False                      # «предохранитель»: дальше короткое ожидание, без долгих пауз
        self.on_wait = None                        # колбэк для вывода «жду N с…» в консоль
        self._last_notice = 0.0

    # --- реализуется провайдером -------------------------------------------------
    async def _generate(self, model: str, system: str, user: str, json_schema: dict | None,
                        effort: str | None = None) -> LLMResponse:
        """effort: None — режим модели по умолчанию, "low" — быстрый ответ без долгих рассуждений."""
        raise NotImplementedError

    # --- служебное ---------------------------------------------------------------
    def _primitives(self) -> tuple[asyncio.Semaphore, asyncio.Lock]:
        loop = asyncio.get_running_loop()
        if self._sync is None or self._loop is not loop:
            self._sync = (asyncio.Semaphore(self.settings.concurrency), asyncio.Lock())
            self._loop = loop
        return self._sync

    @property
    def model(self) -> str:
        """Текущая предпочтительная модель (первая живая в цепочке)."""
        alive = [m for m in self._chain if m not in self._dead]
        return alive[0] if alive else self._chain[0]

    @property
    def exhausted(self) -> bool:
        return all(m in self._dead for m in self._chain)

    def _pick(self) -> tuple[str | None, float]:
        """Выбирает модель для запроса: первая живая и не «остывающая»; иначе — сколько ждать."""
        now = time.monotonic()
        alive = [m for m in self._chain if m not in self._dead]
        if not alive:
            return None, 0.0
        for m in alive:
            if self._cool.get(m, 0.0) <= now:
                return m, 0.0
        soonest = min(alive, key=lambda m: self._cool.get(m, 0.0))
        return soonest, max(0.0, self._cool[soonest] - now)

    def _cache_path(self, model: str, system: str, user: str, effort: str | None = None) -> Path | None:
        if not self.settings.cache_dir:
            return None
        key = hashlib.sha256(
            json.dumps([self.provider, model, self.settings.temperature, effort, system, user],
                       ensure_ascii=False).encode()
        ).hexdigest()
        return self.settings.cache_dir / f"{key}.json"

    def _cached(self, system: str, user: str, effort: str | None) -> LLMResponse | None:
        """Ответ из кэша от любой модели цепочки (повторный прогон того же документа — мгновенно)."""
        for m in self._chain:
            p = self._cache_path(m, system, user, effort)
            if p and p.exists():
                data = json.loads(p.read_text(encoding="utf-8"))
                return LLMResponse(text=data["text"], model=data["model"], input_tokens=data.get("in", 0),
                                   output_tokens=data.get("out", 0), cached=True)
        return None

    async def _throttle(self) -> None:
        if self.settings.rpm <= 0:
            return
        async with self._primitives()[1]:
            interval = 60.0 / self.settings.rpm
            wait = self._last_start + interval - time.monotonic()
            if wait > 0:
                await asyncio.sleep(wait)
            self._last_start = time.monotonic()

    def _kill(self, model: str, exc: BaseException) -> None:
        if model not in self._dead:
            self._dead[model] = short_reason(exc)
            alive = [m for m in self._chain if m not in self._dead]
            tail = f"; дальше используется {alive[0]}" if alive else "; доступных моделей не осталось"
            self.warnings.append(f"Модель {model} исключена: {self._dead[model]}{tail}.")

    # --- основной вызов ----------------------------------------------------------
    def _notify(self, text: str) -> None:
        if self.on_wait and time.monotonic() - self._last_notice > 12:
            self._last_notice = time.monotonic()
            try:
                self.on_wait(text)
            except Exception:  # noqa: BLE001
                pass

    def _store(self, model: str, system: str, user: str, effort: str | None, resp: LLMResponse) -> None:
        path = self._cache_path(model, system, user, effort)
        if path and not resp.cached:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps({"text": resp.text, "model": resp.model, "in": resp.input_tokens,
                                        "out": resp.output_tokens}, ensure_ascii=False), encoding="utf-8")

    async def generate(self, system: str, user: str, *, stage: str, json_schema: dict | None = None,
                       effort: str | None = None, cache: bool = True) -> LLMResponse:
        cached = self._cached(system, user, effort)
        if cached:
            self._record(stage, cached, 1)
            return cached
        attempts = 0
        started = time.monotonic()
        # после срабатывания предохранителя — только короткое ожидание: поздние этапы получают шанс, но без долгих пауз
        budget = min(self.settings.max_wait, 25.0) if self._tripped else self.settings.max_wait
        last: BaseException | None = None
        while True:
            model, wait = self._pick()
            if model is None:
                why = "LLM недоступна, прогон продолжен на правилах" if self._tripped else \
                    (short_reason(last) if last else "нет квоты ни на одну модель")
                err = LLMError(f"[{stage}] все модели недоступны ({why})")
                self._record_error(stage, self._chain[-1], attempts, str(err))
                raise err
            over = attempts >= self.settings.max_retries + 3 or time.monotonic() - started + wait > budget
            if over and attempts == 0:
                wait = 0.0      # этап ещё ни разу не пробовал — делаем хотя бы одну попытку сразу, без ожидания
                over = False
            if over:
                reason = short_reason(last) if last else "превышено время ожидания"
                err = LLMError(f"[{stage}] не удалось получить ответ: {reason}")
                self._record_error(stage, model, attempts, str(err))
                self._failed_in_row += 1
                if self._failed_in_row >= 3 and not self._tripped:
                    self._tripped = True
                    self.warnings.append("Gemini долго перегружен: оставшиеся этапы получили короткое ожидание, "
                                         "чтобы не затягивать прогон. Повторите команду — готовые части возьмутся "
                                         "из кэша, дозапросится только недостающее.")
                raise err
            if wait > 0:
                self._notify(f"Gemini перегружен, жду {wait:.0f} с и повторяю ({stage})…")
                await asyncio.sleep(wait + random.uniform(0, 1.0))
                continue
            attempts += 1
            try:
                async with self._primitives()[0]:
                    await self._throttle()
                    t0 = time.monotonic()
                    resp = await self._generate(model, system, user, json_schema, effort)
                    resp.latency = time.monotonic() - t0
            except FatalLLMError as exc:
                self._record_error(stage, model, attempts, str(exc))
                raise
            except ModelUnavailable as exc:
                last = exc
                self.events[(model, short_reason(exc))] += 1
                self._kill(model, exc)
                continue
            except RateLimited as exc:
                last = exc
                self.events[(model, short_reason(exc))] += 1
                delay = exc.retry_after if exc.retry_after else 20.0
                self._cool[model] = time.monotonic() + min(delay, 90.0)
                continue
            except (TransientLLMError, asyncio.TimeoutError) as exc:
                last = exc
                self.events[(model, short_reason(exc))] += 1
                self._fails[model] += 1
                # перегрузка: модель «остывает» 20 → 40 → 90 с; запрос сразу пробует следующую модель
                base = self.settings.cooldown_base
                self._cool[model] = time.monotonic() + min(45.0, base * 2 ** (self._fails[model] - 1))
                continue

            self._fails[model] = 0
            self._failed_in_row = 0
            if model not in self.used_models:
                self.used_models.append(model)
            if cache:
                self._store(model, system, user, effort, resp)
            self._record(stage, resp, attempts)
            return resp

    async def generate_json(self, system: str, user: str, schema: type[T], *, stage: str,
                            effort: str | None = None) -> T:
        """Вызов с валидацией ответа по pydantic-схеме; при ошибке — одна попытка ремонта.

        В кэш попадает только ответ, прошедший валидацию: «сломанный» ответ не будет переиспользован.
        """
        resp = await self.generate(system, user, stage=stage, effort=effort, cache=False)
        try:
            result = schema.model_validate(extract_json(resp.text))
            self._store(resp.model, system, user, effort, resp)
            return result
        except (JSONExtractError, ValidationError) as exc:
            if resp.cached and self.settings.cache_dir:
                for m in self._chain:
                    p = self._cache_path(m, system, user, effort)
                    if p:
                        p.unlink(missing_ok=True)
            repair_user = (
                user
                + "\n\n---\nТвой предыдущий ответ не прошёл проверку формата: "
                + str(exc)[:600]
                + "\nВерни ТОЛЬКО корректный JSON по указанной схеме, без пояснений и без ```."
                + " Если ответ получался слишком длинным — сократи тексты комментариев, но сохрани все находки."
            )
            resp2 = await self.generate(system, repair_user, stage=f"{stage}:repair", effort=effort, cache=False)
            result = schema.model_validate(extract_json(resp2.text))
            self._store(resp2.model, system, user, effort, resp2)   # исправленный ответ — под исходным запросом
            return result

    # --- учёт вызовов -----------------------------------------------------------
    def _record(self, stage: str, resp: LLMResponse, attempts: int) -> None:
        self.calls.append(CallRecord(stage=stage, model=resp.model, input_tokens=resp.input_tokens,
                                     output_tokens=resp.output_tokens, latency=round(resp.latency, 2),
                                     cached=resp.cached, attempts=attempts))
        if resp.model not in self.used_models:
            self.used_models.append(resp.model)

    def _record_error(self, stage: str, model: str, attempts: int, error: str) -> None:
        self.calls.append(CallRecord(stage=stage, model=model, attempts=attempts, ok=False, error=error[:300]))

    def summary_warnings(self) -> list[str]:
        """Сводка сбоев по моделям — одной строкой на модель/причину вместо десятков повторов."""
        out = list(self.warnings)
        for (model, reason), n in sorted(self.events.items()):
            if model not in self._dead:
                out.append(f"{model}: {reason} ×{n} — запросы автоматически повторены или переведены на запасную модель.")
        return out

    async def aclose(self) -> None:
        return None
