from types import SimpleNamespace
from typing import Any

import anthropic
import httpx
import pytest

from whattowatch.llm.anthropic_provider import SERVER_FALLBACK_BETA, AnthropicProvider
from whattowatch.llm.base import LLMError, Message, ToolCall, ToolSpec


class FakeMessages:
    def __init__(self, response: Any) -> None:
        self.response = response
        self.calls: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


class FakeClient:
    def __init__(self, response: Any) -> None:
        self.messages = FakeMessages(response)
        self.beta = SimpleNamespace(messages=FakeMessages(response))


def resp(*blocks: Any, stop: str = "end_turn", details: Any = None) -> SimpleNamespace:
    return SimpleNamespace(
        content=list(blocks),
        model="claude-opus-5-5",
        stop_reason=stop,
        stop_details=details,
        usage=SimpleNamespace(input_tokens=3, output_tokens=4),
    )


def text(t: str) -> SimpleNamespace:
    return SimpleNamespace(type="text", text=t)


def provider(response: Any, *, server_fallback: bool = False) -> tuple[AnthropicProvider, Any]:
    client = FakeClient(response)
    return AnthropicProvider("k", server_fallback=server_fallback, client=client), client


def test_tool_flow_and_message_translation() -> None:
    tool_use = SimpleNamespace(type="tool_use", id="tu1", name="search_titles", input={"q": "x"})
    p, client = provider(resp(text("Keresek."), tool_use, stop="tool_use"))
    tool = ToolSpec("search_titles", "Keresés", {"type": "object", "properties": {}})
    history = [
        Message("system", "extra"),
        Message("user", "Ajánlj"),
        Message("assistant", "", tool_calls=(ToolCall("t0", "get_watchlist", {}),)),
        Message("tool", "[1]", tool_call_id="t0"),
        Message("tool", "[2]", tool_call_id="t0b"),
    ]
    r = p.complete(history, system="sys", tools=[tool])
    assert r.tool_calls == (ToolCall("tu1", "search_titles", {"q": "x"}),)
    assert r.text == "Keresek." and r.stop_reason == "tool_use"
    assert r.usage == {"input_tokens": 3, "output_tokens": 4}

    kw = client.messages.calls[0]
    assert "thinking" not in kw and "temperature" not in kw
    assert kw["tool_choice"] == {"type": "auto"}
    assert kw["output_config"] == {"effort": "medium"}
    assert kw["system"] == "sys\n\nextra"
    assert kw["tools"][0]["input_schema"] == {"type": "object", "properties": {}}
    assert kw["messages"] == [
        {"role": "user", "content": [{"type": "text", "text": "Ajánlj"}]},
        {
            "role": "assistant",
            "content": [{"type": "tool_use", "id": "t0", "name": "get_watchlist", "input": {}}],
        },
        {
            "role": "user",
            "content": [
                {"type": "tool_result", "tool_use_id": "t0", "content": "[1]"},
                {"type": "tool_result", "tool_use_id": "t0b", "content": "[2]"},
            ],
        },
    ]
    assert client.beta.messages.calls == []


def test_json_schema_output_config_and_server_fallback() -> None:
    p, client = provider(resp(text('{"signals": []}')), server_fallback=True)
    schema = {"type": "object", "properties": {}}
    r = p.complete([Message("user", "x")], json_schema=schema)
    assert r.json == {"signals": []}
    kw = client.beta.messages.calls[0]
    assert kw["output_config"] == {
        "effort": "low",
        "format": {"type": "json_schema", "schema": schema},
    }
    assert kw["betas"] == [SERVER_FALLBACK_BETA] and kw["fallbacks"] == "default"
    assert "tool_choice" not in kw and "thinking" not in kw


def test_refusal_raises_with_category() -> None:
    p, _ = provider(resp(stop="refusal", details=SimpleNamespace(category="cyber")))
    with pytest.raises(LLMError, match="cyber"):
        p.complete([Message("user", "x")])


def test_sdk_errors_wrapped() -> None:
    request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    err = anthropic.RateLimitError(
        "slow down", response=httpx.Response(429, request=request), body=None
    )
    p, _ = provider(err)
    with pytest.raises(LLMError, match="429"):
        p.complete([Message("user", "x")])
    p2, _ = provider(anthropic.APIConnectionError(request=request))
    with pytest.raises(LLMError, match="kapcsolódási"):
        p2.complete([Message("user", "x")])


def test_embed_not_offered() -> None:
    p, _ = provider(resp())
    assert p.embed(["a"]) is None and p.is_local is False
