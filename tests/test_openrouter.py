"""OpenRouter: настройки из .env, формат запроса, 402 (нет кредитов) и автозамена модели при 503."""

import asyncio
import json

import httpx
import pytest

openai = pytest.importorskip("openai")

from solohive import config as cfg  # noqa: E402
from solohive.config import Settings  # noqa: E402
from solohive.llm import create_llm  # noqa: E402
from solohive.llm.base import FatalLLMError  # noqa: E402

ENV_KEYS = ("LLM_PROVIDER", "LLM_MODEL", "GEMINI_API_KEY", "GOOGLE_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY",
            "OLLAMA_MODEL", "OPENROUTER_API_KEY", "OPENROUTER_MODEL", "OPENROUTER_FALLBACK_MODELS",
            "OPENROUTER_BASE_URL")


@pytest.fixture
def clean_env(monkeypatch):
    monkeypatch.setattr(cfg, "_load_dotenv", lambda: None)
    for k in ENV_KEYS:
        monkeypatch.delenv(k, raising=False)
    return monkeypatch


def completion(text, model="google/gemini-3.8-flash"):
    return {"id": "gen-1", "object": "chat.completion", "created": 0, "model": model,
            "choices": [{"index": 0, "message": {"role": "assistant", "content": text}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 120, "completion_tokens": 7, "total_tokens": 127}}


def make(handler, **kw):
    s = Settings(provider="openrouter", model=kw.pop("model", "google/gemini-3.8-flash"), api_key="KEY",
                 base_url=cfg.OPENROUTER_BASE_URL, cache_dir=None, max_retries=2, cooldown_base=0.05, **kw)
    llm = create_llm(s)
    llm._client = openai.AsyncOpenAI(
        api_key="KEY", base_url=s.base_url, max_retries=0, default_headers={"X-Title": "SoloHive"},
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    return llm


def test_settings_from_env(clean_env):
    clean_env.setenv("GEMINI_API_KEY", "g")
    clean_env.setenv("OPENROUTER_API_KEY", "or-key")
    clean_env.setenv("LLM_PROVIDER", "openrouter")
    s = Settings.from_env()
    assert s.provider == "openrouter" and s.api_key == "or-key"
    assert s.base_url == "https://openrouter.ai/api/v1"
    assert s.models_chain == ["google/gemini-3.8-flash", "google/gemini-3.6-flash"]
    assert (s.concurrency, s.review_batch_size) == (6, 8)      # платный тариф: больше параллельности


def test_autodetect_and_missing_key(clean_env):
    clean_env.setenv("OPENROUTER_API_KEY", "or-key")
    assert Settings.from_env().provider == "openrouter"
    clean_env.delenv("OPENROUTER_API_KEY")
    clean_env.setenv("LLM_PROVIDER", "openrouter")
    with pytest.raises(cfg.ConfigError, match="OPENROUTER_API_KEY"):
        Settings.from_env()


def test_request_format_and_answer():
    seen = {}

    def handler(req: httpx.Request):
        seen["url"], seen["title"], seen["body"] = str(req.url), req.headers.get("x-title"), json.loads(req.content)
        return httpx.Response(200, json=completion('{"ok": true}'))

    llm = make(handler)
    resp = asyncio.run(llm.generate("SYS", "USER", stage="t"))
    assert resp.text == '{"ok": true}' and resp.input_tokens == 120
    assert seen["url"].endswith("/api/v1/chat/completions") and seen["title"] == "SoloHive"
    body = seen["body"]
    assert body["model"] == "google/gemini-3.8-flash"
    assert body["response_format"] == {"type": "json_object"}
    assert [m["role"] for m in body["messages"]] == ["system", "user"]


def test_no_credits_is_fatal_with_clear_message():
    def handler(req):
        return httpx.Response(402, json={"error": {"code": 402, "message": "Insufficient credits"}})

    llm = make(handler)
    with pytest.raises(FatalLLMError, match="кредиты"):
        asyncio.run(llm.generate("S", "U", stage="t"))


def test_overloaded_model_routes_to_fallback():
    calls = []

    def handler(req):
        model = json.loads(req.content)["model"]
        calls.append(model)
        if model == "google/gemini-3.8-flash":
            return httpx.Response(503, json={"error": {"code": 503, "message": "Provider overloaded"}})
        return httpx.Response(200, json=completion('{"ok": 1}', model))

    llm = make(handler, fallback_models=["google/gemini-3.6-flash"])
    resp = asyncio.run(llm.generate("S", "U", stage="t"))
    assert resp.model == "google/gemini-3.6-flash"
    assert calls[0] == "google/gemini-3.8-flash"


def test_stage_effort_is_passed_as_reasoning():
    bodies = []

    def handler(req):
        bodies.append(json.loads(req.content))
        return httpx.Response(200, json=completion('{"ok": true}'))

    llm = make(handler)
    asyncio.run(llm.generate("S", "U1", stage="segment", effort="low"))
    asyncio.run(llm.generate("S", "U2", stage="review"))             # без подсказки — модель думает как обычно
    asyncio.run(llm.generate("S", "U3", stage="critic", effort="medium"))
    assert bodies[0]["reasoning"] == {"effort": "low"}
    assert "reasoning" not in bodies[1]
    assert bodies[2]["reasoning"] == {"effort": "medium"}


def test_reasoning_dropped_if_provider_rejects_it():
    bodies = []

    def handler(req):
        body = json.loads(req.content)
        bodies.append(body)
        if "reasoning" in body:
            return httpx.Response(400, json={"error": {"code": 400, "message": "Unsupported parameter: reasoning"}})
        return httpx.Response(200, json=completion('{"ok": true}'))

    llm = make(handler)
    resp = asyncio.run(llm.generate("S", "U", stage="segment", effort="low"))
    assert resp.text == '{"ok": true}'
    assert "reasoning" in bodies[0] and "reasoning" not in bodies[1]
    asyncio.run(llm.generate("S", "U2", stage="interview", effort="low"))
    assert "reasoning" not in bodies[2]                              # больше не отправляется
