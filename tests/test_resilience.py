"""Сценарий реального прогона: основная модель Gemini перегружена (503), лёгкая работает — отчёт полный и быстрый."""

import asyncio
import json
import time

import httpx
from conftest import RESPONDERS, SAMPLE

from solohive.config import Settings
from solohive.llm.gemini import GeminiLLM
from solohive.pipeline import run_pipeline

MARKERS = [("нарезка документа", "segment"), ("проверка отдельных требований", "review"),
           ("непротиворечивость, понятность", "set:consistency"), ("полнота, реализуемость", "set:completeness"),
           ("сценарный анализ", "scenarios"), ("верификация находок", "critic"),
           ("уточняющие вопросы и итоговое", "interview")]


def stage_of(user: str) -> str:
    head = user[:300].lower()
    return next(st for marker, st in MARKERS if marker in head)


def gemini_reply(text: str) -> httpx.Response:
    return httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": text}]}, "finishReason": "STOP"}],
                                     "usageMetadata": {"promptTokenCount": 1000, "candidatesTokenCount": 300}})


def test_pipeline_completes_on_fallback_when_primary_overloaded():
    seen = []

    def handler(req: httpx.Request):
        model = str(req.url).split("/models/")[1].split(":")[0]
        body = json.loads(req.content)
        user = body["contents"][0]["parts"][0]["text"]
        seen.append(model)
        if model == "gemini-3.8-flash":
            return httpx.Response(503, json={"error": {"code": 503, "status": "UNAVAILABLE",
                                                       "message": "This model is currently experiencing high demand."}})
        stage = stage_of(user)
        key = next(k for k in RESPONDERS if stage.startswith(k))
        out = RESPONDERS[key](stage, body["systemInstruction"]["parts"][0]["text"], user)
        return gemini_reply(out if isinstance(out, str) else json.dumps(out, ensure_ascii=False))

    s = Settings(provider="gemini", model="gemini-3.8-flash", fallback_models=["gemini-3.5-flash-lite"],
                 api_key="K", cache_dir=None, cooldown_base=30.0, review_batch_size=12)
    llm = GeminiLLM(s, transport=httpx.MockTransport(handler))
    t0 = time.monotonic()
    report = asyncio.run(run_pipeline(SAMPLE, s, llm=llm))
    assert time.monotonic() - t0 < 10                       # без многоминутных ожиданий
    assert report.passport.models == ["gemini-3.5-flash-lite"]
    assert report.reviews and report.scenarios and report.questions
    assert not any("не обработали" in w for w in report.warnings)
    assert any("перегружен" in w for w in report.warnings)   # сводка сбоев — одной строкой


def test_pipeline_degrades_quickly_when_llm_unavailable():
    def handler(req):
        return httpx.Response(503, json={"error": {"code": 503, "status": "UNAVAILABLE", "message": "high demand"}})

    s = Settings(provider="gemini", model="m1", fallback_models=["m2"], api_key="K", cache_dir=None,
                 cooldown_base=0.05, max_wait=0.6, review_batch_size=12)
    t0 = time.monotonic()
    report = asyncio.run(run_pipeline(SAMPLE, s, llm=GeminiLLM(s, transport=httpx.MockTransport(handler))))
    assert time.monotonic() - t0 < 15
    assert report.active_findings                           # правила всё равно дали отчёт
    assert any("без LLM" in w for w in report.warnings) or any("недоступны" in w for w in report.warnings)
    assert len(report.warnings) <= 10                       # без простыни из одинаковых ошибок


def test_rerun_fills_only_missing_parts_from_cache(tmp_path):
    """Первый прогон: часть запросов падает на перегрузке. Повторный: готовое — из кэша, дозапрос — только упавшее."""
    state = {"flaky": True, "calls": 0}

    def handler(req: httpx.Request):
        body = json.loads(req.content)
        user = body["contents"][0]["parts"][0]["text"]
        stage = stage_of(user)
        state["calls"] += 1
        if state["flaky"] and stage in ("review", "critic", "interview"):
            return httpx.Response(503, json={"error": {"code": 503, "status": "UNAVAILABLE", "message": "high demand"}})
        key = next(k for k in RESPONDERS if stage.startswith(k))
        out = RESPONDERS[key](stage, body["systemInstruction"]["parts"][0]["text"], user)
        return gemini_reply(out if isinstance(out, str) else json.dumps(out, ensure_ascii=False))

    s = Settings(provider="gemini", model="m1", api_key="K", cache_dir=tmp_path, cooldown_base=0.05, max_wait=0.4,
                 review_batch_size=12)
    first = asyncio.run(run_pipeline(SAMPLE, s, llm=GeminiLLM(s, transport=httpx.MockTransport(handler))))
    assert not first.reviews
    state.update(flaky=False, calls=0)
    second = asyncio.run(run_pipeline(SAMPLE, s, llm=GeminiLLM(s, transport=httpx.MockTransport(handler))))
    assert second.reviews and second.questions
    cached = [c for c in second.calls if c.cached]
    assert {c.stage for c in cached} >= {"segment", "set:consistency", "set:completeness", "scenarios"}
    assert state["calls"] == len([c for c in second.calls if c.ok and not c.cached])   # дозапрошено только недостающее
