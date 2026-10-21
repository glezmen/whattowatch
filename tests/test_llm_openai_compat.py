import json

import httpx
import pytest
import respx

from whattowatch.config import Settings
from whattowatch.llm.base import LLMError, Message, ToolCall, ToolSpec
from whattowatch.llm.openai_compat import LMStudioProvider, OpenAIProvider

BASE = "http://lm.test/v1"


def settings(**kw: object) -> Settings:
    return Settings(_env_file=None, lm_studio_base_url=BASE, **kw)  # type: ignore[call-arg]


def chat_reply(message: dict[str, object], finish: str = "stop") -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "model": "local-model",
            "choices": [{"message": message, "finish_reason": finish}],
            "usage": {"prompt_tokens": 11, "completion_tokens": 7},
        },
    )


@respx.mock
def test_tool_call_round_trip() -> None:
    route = respx.post(f"{BASE}/chat/completions").mock(
        side_effect=[
            chat_reply(
                {
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "c1",
                            "type": "function",
                            "function": {"name": "search_titles", "arguments": '{"q": "Dűne"}'},
                        }
                    ],
                },
                "tool_calls",
            ),
            chat_reply({"content": "Megvan."}),
        ]
    )
    p = LMStudioProvider(settings(llm_model="local-model"))
    tool = ToolSpec("search_titles", "Keresés", {"type": "object", "properties": {}})
    r1 = p.complete([Message("user", "Dűne")], system="sys", tools=[tool])
    assert r1.tool_calls == (ToolCall("c1", "search_titles", {"q": "Dűne"}),)
    assert r1.stop_reason == "tool_use"
    assert r1.usage == {"input_tokens": 11, "output_tokens": 7}
    body1 = json.loads(route.calls[0].request.content)
    assert body1["messages"][0] == {"role": "system", "content": "sys"}
    assert body1["tools"][0]["function"]["name"] == "search_titles"
    assert "Authorization" not in route.calls[0].request.headers

    history = [
        Message("user", "Dűne"),
        Message("assistant", tool_calls=r1.tool_calls),
        Message("tool", '{"results": []}', tool_call_id="c1"),
    ]
    r2 = p.complete(history, tools=[tool])
    assert r2.text == "Megvan." and r2.stop_reason == "end_turn"
    msgs = json.loads(route.calls[1].request.content)["messages"]
    assert msgs[1]["tool_calls"][0]["function"] == {
        "name": "search_titles",
        "arguments": '{"q": "Dűne"}',
    }
    assert msgs[2] == {"role": "tool", "tool_call_id": "c1", "content": '{"results": []}'}


@respx.mock
def test_json_schema_response_format_and_default_model() -> None:
    respx.get(f"{BASE}/models").mock(
        return_value=httpx.Response(200, json={"data": [{"id": "first"}, {"id": "second"}]})
    )
    route = respx.post(f"{BASE}/chat/completions").mock(
        return_value=chat_reply({"content": '{"signals": []}'})
    )
    p = LMStudioProvider(settings())
    schema = {"type": "object", "properties": {}}
    r = p.complete([Message("user", "x")], json_schema=schema)
    assert r.json == {"signals": []}
    body = json.loads(route.calls[0].request.content)
    assert body["model"] == "first"
    assert body["response_format"] == {
        "type": "json_schema",
        "json_schema": {"name": "response", "schema": schema, "strict": True},
    }


@respx.mock
def test_embeddings() -> None:
    respx.post(f"{BASE}/embeddings").mock(
        return_value=httpx.Response(
            200,
            json={"data": [{"index": 1, "embedding": [0, 1]}, {"index": 0, "embedding": [1, 0]}]},
        )
    )
    p = LMStudioProvider(settings(llm_model="m", llm_embedding_model="emb"))
    assert p.embed(["a", "b"]) == [[1.0, 0.0], [0.0, 1.0]]
    assert LMStudioProvider(settings(llm_model="m")).embed(["a"]) is None


@respx.mock
def test_embeddings_404_is_none() -> None:
    respx.post(f"{BASE}/embeddings").mock(return_value=httpx.Response(404))
    p = LMStudioProvider(settings(llm_model="m", llm_embedding_model="emb"))
    assert p.embed(["a"]) is None


@respx.mock
def test_server_error_raises() -> None:
    respx.post(f"{BASE}/chat/completions").mock(return_value=httpx.Response(500, text="boom"))
    with pytest.raises(LLMError, match="HTTP 500"):
        LMStudioProvider(settings(llm_model="m")).complete([Message("user", "x")])


@respx.mock
def test_openai_uses_key_header_and_placeholder_model() -> None:
    route = respx.post("https://api.openai.com/v1/chat/completions").mock(
        return_value=chat_reply({"content": "ok"})
    )
    p = OpenAIProvider(settings(openai_api_key="sk-test"))
    assert p.is_local is False and p.model == "gpt-4o-mini"
    p.complete([Message("user", "x")], max_tokens=5)
    req = route.calls[0].request
    assert req.headers["Authorization"] == "Bearer sk-test"
    assert json.loads(req.content)["max_completion_tokens"] == 5
