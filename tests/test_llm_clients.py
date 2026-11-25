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

    from pydantic import BaseModel

    class M(BaseModel):
        x: int

    answers = iter(["не json", '{"x": 5}'])
    s = Settings(provider="mock", model="m", cache_dir=None)
    llm = MockLLM(s, {"t": lambda *a: next(answers)})
    assert asyncio.run(llm.generate_json("S", "U", M, stage="t")).x == 5
    assert [c.stage for c in llm.calls] == ["t", "t:repair"]
