"""LLM-слой: формат REST-запроса Gemini, автозамена модели, ожидание при 429, кэш, ремонт JSON."""

import asyncio
import json

import httpx
import pytest

from solohive.config import Settings
from solohive.llm.base import FatalLLMError
from solohive.llm.gemini import GeminiLLM
from solohive.llm.jsonutil import extract_json
from solohive.llm.mock import MockLLM

OK_BODY = {"candidates": [{"content": {"role": "model", "parts": [
    {"text": "думаю...", "thought": True}, {"text": "{\"ok\": true}"}]}, "finishReason": "STOP"}],
    "usageMetadata": {"promptTokenCount": 100, "candidatesTokenCount": 5, "thoughtsTokenCount": 20}}


def make(handler, **kw):
    s = Settings(provider="gemini", model=kw.pop("model", "gemini-3.8-flash"), api_key="KEY", cache_dir=None,
                 max_retries=2, **kw)
    return GeminiLLM(s, transport=httpx.MockTransport(handler))


def test_request_format_and_thought_parts_skipped():
    seen = {}

    def handler(req: httpx.Request):
        seen["url"], seen["key"], seen["body"] = str(req.url), req.headers["x-goog-api-key"], json.loads(req.content)
        return httpx.Response(200, json=OK_BODY)

    llm = make(handler)
    r = asyncio.run(llm.generate("SYS", "USER", stage="t", effort="low"))
    assert r.text == '{"ok": true}' and r.input_tokens == 100 and r.output_tokens == 25
    assert seen["url"].endswith("/v1beta/models/gemini-3.8-flash:generateContent")
    assert seen["key"] == "KEY"
    b = seen["body"]
    assert b["systemInstruction"]["parts"][0]["text"] == "SYS"
    assert b["contents"][0]["parts"][0]["text"] == "USER"
    assert b["generationConfig"]["responseMimeType"] == "application/json"
    assert b["generationConfig"]["thinkingConfig"] == {"thinkingLevel": "LOW"}


def test_fallback_on_404_and_wait_on_429():
    calls = []

    def handler(req):
        calls.append(str(req.url).split("/models/")[1])
        if "old-model" in calls[-1]:
            return httpx.Response(404, json={"error": {"code": 404, "status": "NOT_FOUND", "message": "not found"}})
        if len(calls) == 2:
            return httpx.Response(429, json={"error": {"code": 429, "status": "RESOURCE_EXHAUSTED", "message": "quota",
                                                       "details": [{"retryDelay": "0.1s"}]}})
        return httpx.Response(200, json=OK_BODY)

    llm = make(handler, model="old-model", fallback_models=["gemini-3.6-flash"])
    r = asyncio.run(llm.generate("S", "U", stage="t"))
    assert r.model == "gemini-3.6-flash"
    assert calls == ["old-model:generateContent", "gemini-3.6-flash:generateContent",
                     "gemini-3.6-flash:generateContent"]
    assert llm.warnings and "old-model" in llm.warnings[0]


def test_unsupported_thinking_is_disabled_automatically():
    bodies = []

    def handler(req):
        body = json.loads(req.content)
        bodies.append(body)
        if "thinkingConfig" in body["generationConfig"]:
            return httpx.Response(400, json={"error": {"code": 400, "status": "INVALID_ARGUMENT",
                                                       "message": "thinking_level is not supported for this model"}})
        return httpx.Response(200, json=OK_BODY)

    r = asyncio.run(make(handler).generate("S", "U", stage="t", effort="low"))
    assert r.text and len(bodies) == 2


def test_invalid_key_is_fatal():
    def handler(req):
        return httpx.Response(400, json={"error": {"code": 400, "status": "INVALID_ARGUMENT",
                                                   "message": "API key not valid. Please pass a valid API key."}})

    with pytest.raises(FatalLLMError):
        asyncio.run(make(handler).generate("S", "U", stage="t"))


def test_cache_returns_same_answer_without_call(tmp_path):
    n = {"calls": 0}

    def handler(req):
        n["calls"] += 1
        return httpx.Response(200, json=OK_BODY)

    s = Settings(provider="gemini", model="m", api_key="K", cache_dir=tmp_path)
    for _ in range(2):
        llm = GeminiLLM(s, transport=httpx.MockTransport(handler))
        asyncio.run(llm.generate("S", "U", stage="t"))
    assert n["calls"] == 1 and llm.calls[-1].cached


def test_json_extraction_and_repair():
    assert extract_json('```json\n{"a": [1, 2,],}\n```') == {"a": [1, 2]}
    assert extract_json('Вот ответ: {"x": 1} — готово') == {"x": 1}
    # многострочная цитата документа с «сырым» переводом строки внутри строки — без повторного запроса
    assert extract_json('{"quote": "Content-Type: application/json .\nТело не используется."}') == \
        {"quote": "Content-Type: application/json .\nТело не используется."}

    from pydantic import BaseModel

    class M(BaseModel):
        x: int

    answers = iter(["не json", '{"x": 5}'])
    s = Settings(provider="mock", model="m", cache_dir=None)
    llm = MockLLM(s, {"t": lambda *a: next(answers)})
    assert asyncio.run(llm.generate_json("S", "U", M, stage="t")).x == 5
    assert [c.stage for c in llm.calls] == ["t", "t:repair"]


def test_overload_routes_call_to_fallback_and_returns_to_primary():
    import time
    calls = []
    state = {"primary_down": True}

    def handler(req):
        model = str(req.url).split("/models/")[1].split(":")[0]
        calls.append(model)
        if model == "main-model" and state["primary_down"]:
            return httpx.Response(503, json={"error": {"code": 503, "status": "UNAVAILABLE",
                                                       "message": "This model is currently experiencing high demand."}})
        return httpx.Response(200, json=OK_BODY)

    llm = make(handler, model="main-model", fallback_models=["lite-model"], cooldown_base=0.2)
    r1 = asyncio.run(llm.generate("S", "U1", stage="a"))
    assert r1.model == "lite-model" and calls == ["main-model", "lite-model"]     # тот же запрос — на запасную
    assert "main-model" not in llm._dead                                         # основную не исключили
    state["primary_down"] = False
    time.sleep(0.25)                                                             # основная «остыла»
    r2 = asyncio.run(llm.generate("S", "U2", stage="b"))
    assert r2.model == "main-model"                                              # вернулись на основную
    assert any("перегружен" in w for w in llm.summary_warnings())


def test_no_quota_anywhere_fails_fast():
    import time
    n = {"calls": 0}

    def handler(req):
        n["calls"] += 1
        return httpx.Response(429, json={"error": {"code": 429, "status": "RESOURCE_EXHAUSTED",
                                                   "message": "Quota exceeded ... limit: 0, model: x"}})

    llm = make(handler, model="m1", fallback_models=["m2"])
    t0 = time.monotonic()
    for i in range(5):
        with pytest.raises(Exception):
            asyncio.run(llm.generate("S", f"U{i}", stage="s"))
    assert time.monotonic() - t0 < 2.0          # «предохранитель»: без долгих ожиданий
    assert n["calls"] == 2                      # каждая модель опрошена один раз
    assert llm.exhausted


def test_one_token_reply_is_retried_not_returned():
    replies = iter([
        {"candidates": [{"content": {"parts": [{"text": "{"}]}, "finishReason": "STOP"}],
         "usageMetadata": {"promptTokenCount": 9000, "candidatesTokenCount": 1}},
        OK_BODY,
    ])

    def handler(req):
        return httpx.Response(200, json=next(replies))

    llm = make(handler, model="m", fallback_models=["m2"], cooldown_base=0.05)
    r = asyncio.run(llm.generate("S", "U", stage="review#1"))
    assert r.text == '{"ok": true}'


def test_only_valid_json_is_cached(tmp_path):
    from pydantic import BaseModel

    class M(BaseModel):
        ok: bool

    texts = iter(["это не json", '{"ok": true}'])
    n = {"calls": 0}

    def handler(req):
        n["calls"] += 1
        return httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": next(texts)}]},
                                                         "finishReason": "STOP"}]})

    s = Settings(provider="gemini", model="m", api_key="K", cache_dir=tmp_path)
    llm = GeminiLLM(s, transport=httpx.MockTransport(handler))
    assert asyncio.run(llm.generate_json("S", "U", M, stage="t")).ok
    assert n["calls"] == 2                                     # ответ + ремонт
    llm2 = GeminiLLM(s, transport=httpx.MockTransport(handler))
    assert asyncio.run(llm2.generate_json("S", "U", M, stage="t")).ok
    assert n["calls"] == 2                                     # второй прогон: валидный ответ из кэша
    assert llm2.calls[-1].cached


def test_late_stage_always_gets_one_attempt_after_breaker():
    """После срабатывания предохранителя поздний этап (критик/интервьюер) всё равно пробует хотя бы раз."""
    state = {"down": True, "calls": 0}

    def handler(req):
        state["calls"] += 1
        if state["down"]:
            return httpx.Response(503, json={"error": {"code": 503, "status": "UNAVAILABLE", "message": "high demand"}})
        return httpx.Response(200, json=OK_BODY)

    llm = make(handler, model="m", cooldown_base=30.0, max_wait=0.3)
    for i in range(3):                                    # три провала подряд → предохранитель
        with pytest.raises(Exception):
            asyncio.run(llm.generate("S", f"U{i}", stage=f"s{i}"))
    assert llm._tripped
    state["down"] = False                                 # Google «отпустило», но модель ещё «остывает» 30 с
    r = asyncio.run(llm.generate("S", "late", stage="interview"))
    assert r.text == '{"ok": true}'


def test_tolerant_schemas_accept_small_format_slips():
    """null, «L030», строка вместо списка, объект вместо списка — принимаются без повторного запроса."""
    from solohive.agents.reviewer import ReviewResponse
    from solohive.agents.scenarist import ScenarioResponse
    from solohive.agents.segmenter import SegmentResponse

    seg = SegmentResponse.model_validate({"requirements": [
        {"doc_id": None, "section": None, "line_start": "L030", "line_end": "31–32", "quote": "x", "summary": None}]})
    r = seg.requirements[0]
    assert (r.section, r.line_start, r.line_end, r.summary) == ("", 30, 31, "")

    sc = ScenarioResponse.model_validate({"scenarios": {"title": "t", "given_when": None,
                                                        "quotes": [{"quote": "a"}, None, "b"]}})
    assert sc.scenarios[0].quotes == ["a", "b"] and sc.scenarios[0].given_when == ""

    rv = ReviewResponse.model_validate({"reviews": [{"rid": "REQ-01", "rewrite": None, "test_ideas": "одна идея",
                                                     "defects": [{"severity": None, "quote": None, "title": "t"}]}]})
    assert rv.reviews[0].test_ideas == ["одна идея"] and rv.reviews[0].defects[0].severity == "MAJOR"


def test_failed_response_is_saved_for_diagnosis(tmp_path):
    from pydantic import BaseModel

    class M(BaseModel):
        x: int

    answers = iter(['{"x": "не число"}', '{"x": 5}'])
    s = Settings(provider="mock", model="m", cache_dir=tmp_path)
    llm = MockLLM(s, {"t": lambda *a: next(answers)})
    assert asyncio.run(llm.generate_json("S", "U", M, stage="t#1")).x == 5
    saved = list((tmp_path / "failures").glob("t_1-*.txt"))
    assert len(saved) == 1 and "не число" in saved[0].read_text(encoding="utf-8")
