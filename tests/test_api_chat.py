"""API: POST /api/chat with the provider dependency overridden."""

from __future__ import annotations

from typing import Any

from conftest import ApiEnv
from test_chat_agent import FakeProvider, calls, text

from whattowatch.api.chat import provider_factory_dep
from whattowatch.llm.base import LLMError, ToolCall


def _override(api: ApiEnv, factory: Any) -> None:
    app: Any = api.client.app
    app.dependency_overrides[provider_factory_dep] = lambda: factory


def test_chat_ok(api: ApiEnv) -> None:
    requested: list[str | None] = []
    provider = FakeProvider(
        [calls(ToolCall("c1", "search_titles", {"q": "erdő"})), text("Sötét erdő, Netflix.")]
    )

    def factory(name: str | None) -> FakeProvider:
        requested.append(name)
        return provider

    _override(api, factory)
    body = {
        "messages": [
            {"role": "user", "content": "Szia"},
            {"role": "assistant", "content": "Szia! Miben segíthetek?"},
            {"role": "user", "content": "Keresd meg a Sötét erdőt"},
        ],
        "provider": "anthropic",
    }
    r = api.client.post("/api/chat", json=body)
    assert r.status_code == 200, r.text
    data = r.json()
    assert requested == ["anthropic"]
    assert data["reply"] == "Sötét erdő, Netflix." and data["provider"] == "fake"
    assert data["tool_trace"][0]["name"] == "search_titles"
    assert data["tool_trace"][0]["title_ids"] == [api.ids[101]]
    assert [m.role for m in provider.seen[0]["messages"]] == ["user", "assistant", "user"]


def test_chat_provider_not_configured_is_409(api: ApiEnv) -> None:
    def factory(name: str | None) -> FakeProvider:
        raise LLMError("Nincs beállítva ANTHROPIC_API_KEY.")

    _override(api, factory)
    r = api.client.post("/api/chat", json={"messages": [{"role": "user", "content": "hi"}]})
    assert r.status_code == 409 and r.json()["detail"] == "Nincs beállítva ANTHROPIC_API_KEY."


def test_chat_provider_failure_is_502(api: ApiEnv) -> None:
    _override(api, lambda name: FakeProvider([LLMError("timeout")]))
    r = api.client.post("/api/chat", json={"messages": [{"role": "user", "content": "hi"}]})
    assert r.status_code == 502 and "timeout" in r.json()["detail"]


def test_chat_unknown_provider_name_is_409(api: ApiEnv) -> None:
    r = api.client.post(
        "/api/chat", json={"messages": [{"role": "user", "content": "hi"}], "provider": "nope"}
    )
    assert r.status_code == 409 and "nope" in r.json()["detail"]


def test_chat_validation(api: ApiEnv) -> None:
    _override(api, lambda name: FakeProvider([]))
    assert api.client.post("/api/chat", json={"messages": []}).status_code == 422
    bad_role = {"messages": [{"role": "system", "content": "x"}]}
    assert api.client.post("/api/chat", json=bad_role).status_code == 422
    last_assistant = {"messages": [{"role": "assistant", "content": "x"}]}
    assert api.client.post("/api/chat", json=last_assistant).status_code == 422
