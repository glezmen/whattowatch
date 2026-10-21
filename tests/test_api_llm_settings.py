"""API: /settings/llm, /settings/llm/test (docs/api.md M7b)."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import httpx
import pytest
import respx
from conftest import ApiEnv

from whattowatch.llm import registry
from whattowatch.llm.base import LLMError, LLMResponse, Message

LMS = "http://localhost:1234/v1"


@respx.mock
def test_get_options_with_lm_studio_models(api: ApiEnv) -> None:
    respx.get(f"{LMS}/models").mock(
        return_value=httpx.Response(200, json={"data": [{"id": "qwen3-8b"}, {"id": "gemma"}]})
    )
    body = api.client.get("/api/settings/llm").json()
    assert body["provider"] == "lmstudio" and body["model"] is None
    assert body["options"]["lmstudio"] == {
        "available": True,
        "base_url": LMS,
        "models": ["qwen3-8b", "gemma"],
    }
    assert body["options"]["anthropic"] == {
        "key_set": False,
        "models": ["claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-4-5", "claude-fable-5-1"],
    }
    assert body["options"]["openai"] == {"key_set": False, "models": []}
    assert body["cloud_send_feedback_text"] is False and body["fallback_allowed"] is False


@respx.mock
def test_lm_studio_down_is_unavailable(api: ApiEnv) -> None:
    respx.get(f"{LMS}/models").mock(side_effect=httpx.ConnectError("refused"))
    lms = api.client.get("/api/settings/llm").json()["options"]["lmstudio"]
    assert lms == {"available": False, "base_url": LMS, "models": []}


@respx.mock
def test_put_roundtrip_and_validation(api: ApiEnv) -> None:
    respx.get(f"{LMS}/models").mock(return_value=httpx.Response(500))
    r = api.client.put(
        "/api/settings/llm",
        json={"provider": "anthropic", "model": "claude-sonnet-5-5", "fallback_allowed": True},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert (body["provider"], body["model"], body["fallback_allowed"]) == (
        "anthropic",
        "claude-sonnet-5-5",
        True,
    )
    assert body["cloud_send_feedback_text"] is False
    r = api.client.put("/api/settings/llm", json={"model": None})
    assert r.json()["model"] is None and r.json()["provider"] == "anthropic"
    assert api.client.put("/api/settings/llm", json={"provider": "gemini"}).status_code == 422
    assert api.client.get("/api/settings/llm").json()["provider"] == "anthropic"


class FakeProvider:
    name = "fake"
    is_local = True
    model = "fake-model"

    def __init__(self, outcome: LLMResponse | Exception) -> None:
        self.outcome = outcome
        self.prompts: list[str] = []

    def complete(self, messages: Sequence[Message], **_: Any) -> LLMResponse:
        self.prompts.append(messages[-1].content)
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome


def _patch(monkeypatch: pytest.MonkeyPatch, provider: Any, seen: list[Any]) -> None:
    def fake_get_provider(settings: Any, override: Any = None, *, config: Any = None) -> Any:
        seen.append(config)
        if isinstance(provider, Exception):
            raise provider
        return provider

    monkeypatch.setattr(registry, "get_provider", fake_get_provider)


def test_llm_test_ok(api: ApiEnv, monkeypatch: pytest.MonkeyPatch) -> None:
    resp = LLMResponse(
        text=" Működik. ",
        tool_calls=(),
        json=None,
        model="fake-model",
        usage={"input_tokens": 9, "output_tokens": 2},
    )
    fake = FakeProvider(resp)
    seen: list[Any] = []
    _patch(monkeypatch, fake, seen)
    r = api.client.post("/api/settings/llm/test", json={"provider": "openai", "model": "m1"})
    assert r.status_code == 200
    assert r.json() == {
        "ok": True,
        "model": "fake-model",
        "reply": "Működik.",
        "usage": {"input_tokens": 9, "output_tokens": 2},
        "error": None,
    }
    assert fake.prompts == ["Válaszolj egy szóval: működik?"]
    assert (seen[0].provider, seen[0].model) == ("openai", "m1")


def test_llm_test_errors_are_ok_false(api: ApiEnv, monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[Any] = []
    _patch(monkeypatch, LLMError("Nincs ANTHROPIC_API_KEY."), seen)
    r = api.client.post("/api/settings/llm/test", json={"provider": "anthropic"})
    assert r.status_code == 200
    assert r.json()["ok"] is False and "ANTHROPIC_API_KEY" in r.json()["error"]
    _patch(monkeypatch, FakeProvider(LLMError("HTTP 500")), seen)
    r = api.client.post("/api/settings/llm/test", json={})
    assert r.status_code == 200 and r.json() == {
        "ok": False,
        "model": None,
        "reply": None,
        "usage": {},
        "error": "HTTP 500",
    }
    _patch(monkeypatch, FakeProvider(RuntimeError("boom")), seen)
    r = api.client.post("/api/settings/llm/test", json={})
    assert r.status_code == 200 and r.json()["ok"] is False
    assert api.client.post("/api/settings/llm/test", json={"provider": "x"}).status_code == 422
