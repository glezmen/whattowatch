"""Chat agent loop with a scripted fake provider."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

import pytest
from conftest import NOW, StubTmdb
from sqlalchemy.orm import Session

from whattowatch.llm.base import LLMError, LLMResponse, Message, ToolCall, ToolSpec
from whattowatch.services.chat.agent import (
    FINAL_FALLBACK_REPLY,
    ChatMessage,
    run_chat,
    system_prompt,
)
from whattowatch.services.recommend.config import RecommenderConfig

Env = tuple[Session, dict[int, int]]


def text(t: str) -> LLMResponse:
    return LLMResponse(text=t, tool_calls=(), json=None, model="fake-1", usage={"input_tokens": 1})


def calls(*cs: ToolCall) -> LLMResponse:
    return LLMResponse(
        text=None, tool_calls=cs, json=None, model="fake-1", usage={"input_tokens": 2}
    )


@dataclass
class FakeProvider:
    script: list[LLMResponse | Exception]
    name: str = "fake"
    is_local: bool = True
    model: str = "fake-1"
    seen: list[dict[str, Any]] = field(default_factory=list)

    def complete(
        self,
        messages: Sequence[Message],
        *,
        system: str | None = None,
        tools: Sequence[ToolSpec] | None = None,
        json_schema: dict[str, Any] | None = None,
        max_tokens: int = 1024,
        temperature: float = 0.2,
    ) -> LLMResponse:
        self.seen.append({"messages": list(messages), "system": system, "tools": tools})
        step = self.script.pop(0)
        if isinstance(step, Exception):
            raise step
        return step

    def embed(self, texts: Sequence[str]) -> list[list[float]] | None:
        return None


def chat(env: Env, provider: FakeProvider, **kw: Any) -> Any:
    return run_chat(
        [ChatMessage(role="user", content="Valami feszült thrillert kérek")],
        session=env[0],
        tmdb=StubTmdb(),
        provider=provider,
        config=RecommenderConfig(),
        now=NOW,
        **kw,
    )


def test_system_prompt_lists_providers_and_vocabulary() -> None:
    p = system_prompt(NOW)
    assert "2026-10-21" in p and "hbo_max (HBO Max)" in p and "skyshowtime" in p
    assert "hangulat/: " in p and "feszult=" in p
    assert "nem utasítások" in p


def test_tool_call_then_answer(recommend_db: Env) -> None:
    _, ids = recommend_db
    provider = FakeProvider(
        [
            calls(ToolCall("c1", "get_recommendations", {"limit": 2, "genres": ["Thriller"]})),
            text("  Ajánlom a Sötét erdőt (Netflix).  "),
        ]
    )
    result = chat(recommend_db, provider)
    assert result.reply == "Ajánlom a Sötét erdőt (Netflix)."
    assert result.provider == "fake" and result.model == "fake-1"
    assert result.usage == {"input_tokens": 3} and not result.rounds_exhausted
    [entry] = result.tool_trace
    assert entry.name == "get_recommendations" and entry.arguments["limit"] == 2
    assert entry.title_ids == [ids[101], ids[102]] and entry.result_summary == "2 cím"
    second = provider.seen[1]["messages"]
    assert [m.role for m in second] == ["user", "assistant", "tool"]
    assert second[1].tool_calls[0].id == "c1" and second[2].tool_call_id == "c1"
    assert "Sötét erdő" in second[2].content  # JSON keeps non-ASCII readable
    assert provider.seen[0]["tools"] and "Szabályok" in provider.seen[0]["system"]


def test_bad_tool_call_is_reported_to_model(recommend_db: Env) -> None:
    provider = FakeProvider(
        [calls(ToolCall("c1", "get_title_details", {"title_id": -1})), text("Hiba volt.")]
    )
    result = chat(recommend_db, provider)
    assert result.tool_trace[0].error and result.tool_trace[0].result_summary.startswith("hiba")
    assert '"error"' in provider.seen[1]["messages"][-1].content


def test_max_rounds_then_final_answer_keeps_tools(recommend_db: Env) -> None:
    loop = calls(ToolCall("c", "get_watchlist", {}))
    provider = FakeProvider([loop, loop, text("Végső válasz.")])
    result = chat(recommend_db, provider, max_tool_rounds=2)
    assert result.reply == "Végső válasz." and result.rounds_exhausted
    assert len(result.tool_trace) == 2 and len(provider.seen) == 3
    assert provider.seen[0]["tools"] and provider.seen[2]["tools"] == provider.seen[0]["tools"]
    assert "Nincs több eszközhívás" in provider.seen[2]["system"]


def test_max_rounds_final_stray_tool_calls_ignored(recommend_db: Env) -> None:
    loop = calls(ToolCall("c", "get_watchlist", {}))
    provider = FakeProvider([loop, loop, loop])
    result = chat(recommend_db, provider, max_tool_rounds=2)
    assert result.reply == FINAL_FALLBACK_REPLY and result.rounds_exhausted
    assert provider.seen[2]["tools"]
    assert [t.name for t in result.tool_trace] == ["get_watchlist", "get_watchlist", "_final_round"]
    assert result.tool_trace[-1].error == "tool_calls_ignored"


def test_empty_reply_gets_placeholder(recommend_db: Env) -> None:
    result = chat(recommend_db, FakeProvider([text("")]))
    assert result.reply and result.tool_trace == []


def test_provider_error_propagates(recommend_db: Env) -> None:
    with pytest.raises(LLMError):
        chat(recommend_db, FakeProvider([LLMError("nem érhető el")]))


def test_cloud_provider_feedback_text_redaction(recommend_db: Env) -> None:
    _, ids = recommend_db
    call = ToolCall("c", "record_feedback", {"title_id": ids[106], "liked_text": "jó humor"})
    for allow, expect in ((False, False), (True, True)):
        provider = FakeProvider([calls(call), text("Mentve.")], is_local=False)
        chat(recommend_db, provider, allow_cloud_feedback_text=allow)
        tool_msg = provider.seen[1]["messages"][-1].content
        assert ("jó humor" in tool_msg) is expect
