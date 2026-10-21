"""Claude Code (CLI) provider via a faked Claude Agent SDK `query`."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

import claude_agent_sdk as sdk
import httpx
import pytest
import respx
from conftest import NOW, ApiEnv, StubTmdb
from sqlalchemy.orm import Session

from whattowatch.api import settings as settings_api
from whattowatch.config import Settings
from whattowatch.llm import claude_code_provider as ccp
from whattowatch.llm import registry
from whattowatch.llm.base import AgentCapable, AgentRun, LLMError, Message, ToolSpec
from whattowatch.llm.claude_code_provider import DISALLOWED_TOOLS, ClaudeCodeProvider
from whattowatch.services.chat.agent import ChatMessage, run_chat
from whattowatch.services.recommend.config import RecommenderConfig

Env = tuple[Session, dict[int, int]]


def result(**kw: Any) -> sdk.ResultMessage:
    base: dict[str, Any] = {
        "subtype": "success",
        "duration_ms": 1,
        "duration_api_ms": 1,
        "is_error": False,
        "num_turns": 1,
        "session_id": "s",
        "result": "Működik.",
        "usage": {"input_tokens": 7, "output_tokens": 3, "cache_read_input_tokens": 100},
        "stop_reason": "end_turn",
    }
    base.update(kw)
    return sdk.ResultMessage(**base)


def assistant(text: str = "Működik.") -> sdk.AssistantMessage:
    return sdk.AssistantMessage(content=[sdk.TextBlock(text=text)], model="claude-sonnet-x")


@dataclass
class FakeQuery:
    messages: list[Any]
    error: Exception | None = None
    calls: list[dict[str, Any]] = field(default_factory=list)
    on_options: Callable[[sdk.ClaudeAgentOptions], Any] | None = None

    async def __call__(
        self, *, prompt: str, options: sdk.ClaudeAgentOptions | None = None
    ) -> AsyncIterator[Any]:
        assert options is not None
        self.calls.append({"prompt": prompt, "options": options})
        if self.on_options is not None:
            await self.on_options(options)
        for msg in self.messages:
            yield msg
        if self.error is not None:
            raise self.error


def install(monkeypatch: pytest.MonkeyPatch, fake: FakeQuery) -> FakeQuery:
    monkeypatch.setattr(sdk, "query", fake)
    return fake


def test_complete_text_and_isolation(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = install(monkeypatch, FakeQuery([assistant(), result()]))
    resp = ClaudeCodeProvider("sonnet").complete(
        [
            Message(role="user", content="Szia"),
            Message(role="assistant", content="Helló!"),
            Message(role="user", content="Működik?"),
        ],
        system="Rendszer",
    )
    assert resp.text == "Működik." and resp.json is None and resp.tool_calls == ()
    assert resp.model == "claude-sonnet-x" and resp.stop_reason == "end_turn"
    assert resp.usage == {"input_tokens": 7, "output_tokens": 3}
    [call] = fake.calls
    opts: sdk.ClaudeAgentOptions = call["options"]
    assert opts.setting_sources == [] and opts.tools == []
    assert opts.disallowed_tools == list(DISALLOWED_TOOLS)
    assert {"Bash", "Read", "Write", "Edit", "WebFetch", "Agent"} <= set(opts.disallowed_tools)
    assert opts.permission_mode == "dontAsk" and opts.max_turns == 1
    assert opts.system_prompt == "Rendszer" and opts.model == "sonnet"
    assert opts.allowed_tools == [] and not opts.mcp_servers and opts.output_format is None
    assert "wtw-claude-" in str(opts.cwd)
    prompt = call["prompt"]
    assert "[Felhasználó]: Szia" in prompt and "[Asszisztens]: Helló!" in prompt
    assert prompt.rstrip().endswith("Működik?")


def test_default_model_label() -> None:
    assert ClaudeCodeProvider(None).model == "(cli default)"
    p = ClaudeCodeProvider("opus")
    assert p.model == "opus" and p.name == "claude_code" and p.is_local is False
    assert p.embed(["x"]) is None


def test_complete_json_schema(monkeypatch: pytest.MonkeyPatch) -> None:
    out = {"signals": [{"key": "humor"}]}
    fake = install(monkeypatch, FakeQuery([result(result=None, structured_output=out)]))
    schema = {"type": "object", "properties": {"signals": {"type": "array"}}}
    resp = ClaudeCodeProvider(None).complete(
        [Message(role="user", content="x")], json_schema=schema
    )
    assert resp.json == out and resp.model == "(cli default)"
    opts = fake.calls[0]["options"]
    assert opts.output_format == {"type": "json_schema", "schema": schema}
    assert opts.model is None


def test_complete_rejects_tools() -> None:
    spec = ToolSpec("t", "d", {"type": "object", "properties": {}})
    with pytest.raises(LLMError, match="run_agent"):
        ClaudeCodeProvider(None).complete([Message(role="user", content="x")], tools=[spec])


@pytest.mark.parametrize(
    "fake",
    [
        FakeQuery([], error=sdk.CLINotFoundError("Claude Code not found")),
        FakeQuery([], error=sdk.ProcessError("boom", exit_code=1)),
        FakeQuery([result(is_error=True, subtype="error_during_execution", result=None)]),
        FakeQuery([]),
    ],
)
def test_failures_map_to_llm_error(monkeypatch: pytest.MonkeyPatch, fake: FakeQuery) -> None:
    install(monkeypatch, fake)
    with pytest.raises(LLMError, match="nincs bejelentkezve"):
        ClaudeCodeProvider(None).complete([Message(role="user", content="x")])


def test_timeout_maps_to_llm_error(monkeypatch: pytest.MonkeyPatch) -> None:
    async def slow(_: Any) -> None:
        await asyncio.sleep(5)

    install(monkeypatch, FakeQuery([result()], on_options=slow))
    with pytest.raises(LLMError, match="időtúllépés"):
        ClaudeCodeProvider(None, timeout_s=0.05).complete([Message(role="user", content="x")])


SPECS = [
    ToolSpec("search_titles", "Keresés", {"type": "object", "properties": {"q": {}}}),
    ToolSpec("get_watchlist", "Watchlist", {"type": "object", "properties": {}}),
]


def test_run_agent_registers_and_executes_tools(monkeypatch: pytest.MonkeyPatch) -> None:
    executed: list[tuple[str, dict[str, Any]]] = []

    def execute(name: str, args: dict[str, Any]) -> dict[str, Any]:
        executed.append((name, args))
        return {"hits": [{"title_id": 5, "title": "Sötét erdő"}]}

    async def call_tool(options: sdk.ClaudeAgentOptions) -> None:
        server = options.mcp_servers["wtw"]  # type: ignore[index]
        assert server["type"] == "sdk" and server["name"] == "wtw"

    fake = install(
        monkeypatch, FakeQuery([assistant(), result(result="Kész")], on_options=call_tool)
    )
    provider = ClaudeCodeProvider(None)
    # Invoke the generated tool bodies directly, as the CLI would through MCP.
    calls: list[tuple[str, dict[str, Any], Any]] = []
    tools = provider.build_tools(SPECS, execute, calls)
    assert [t.name for t in tools] == ["search_titles", "get_watchlist"]
    assert tools[0].input_schema == SPECS[0].parameters
    out = asyncio.run(tools[0].handler({"q": "erdő"}))
    assert out["content"][0]["type"] == "text" and "Sötét erdő" in out["content"][0]["text"]
    assert calls == [
        ("search_titles", {"q": "erdő"}, {"hits": [{"title_id": 5, "title": "Sötét erdő"}]})
    ]

    run = provider.run_agent(
        [Message(role="user", content="Mit nézzek?")],
        system="S",
        tools=SPECS,
        execute=execute,
        max_tool_rounds=4,
    )
    assert isinstance(provider, AgentCapable)
    assert run.text == "Kész" and run.model == "claude-sonnet-x" and run.tool_calls == []
    opts = fake.calls[0]["options"]
    assert opts.allowed_tools == ["mcp__wtw__search_titles", "mcp__wtw__get_watchlist"]
    assert opts.max_turns == 6 and opts.setting_sources == [] and opts.tools == []
    assert opts.disallowed_tools == list(DISALLOWED_TOOLS)


def test_run_agent_trace_from_tool_bodies(monkeypatch: pytest.MonkeyPatch) -> None:
    """The fake CLI calls the registered tool handler; the call lands in the trace."""
    captured: dict[str, Any] = {}
    real_build = ClaudeCodeProvider.build_tools

    def spy(self: ClaudeCodeProvider, *a: Any) -> Any:
        captured["tools"] = real_build(self, *a)
        return captured["tools"]

    async def call_tool(_: sdk.ClaudeAgentOptions) -> None:
        await captured["tools"][1].handler({})

    monkeypatch.setattr(ClaudeCodeProvider, "build_tools", spy)
    install(monkeypatch, FakeQuery([result(result="Ok")], on_options=call_tool))
    run = ClaudeCodeProvider(None).run_agent(
        [Message(role="user", content="x")],
        system="S",
        tools=SPECS,
        execute=lambda name, args: {"items": [], "total": 0},
        max_tool_rounds=2,
    )
    assert run.tool_calls == [("get_watchlist", {}, {"items": [], "total": 0})]


def test_run_agent_max_turns_is_not_an_error(monkeypatch: pytest.MonkeyPatch) -> None:
    install(
        monkeypatch,
        FakeQuery([result(is_error=True, subtype="error_max_turns", result=None)]),
    )
    run = ClaudeCodeProvider(None).run_agent(
        [Message(role="user", content="x")],
        system="S",
        tools=SPECS,
        execute=lambda *_: {},
        max_tool_rounds=1,
    )
    assert run.stop_reason == "max_turns" and run.text == ""


def test_registry_requires_cli(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ccp.shutil, "which", lambda _: None)
    with pytest.raises(LLMError, match="nincs telepítve"):
        registry.get_provider(Settings(_env_file=None), "claude_code")  # type: ignore[call-arg]
    monkeypatch.setattr(ccp.shutil, "which", lambda _: "/usr/bin/claude")
    s = Settings(_env_file=None, claude_code_model="haiku")  # type: ignore[call-arg]
    p = registry.get_provider(s, "claude_code")
    assert isinstance(p, ClaudeCodeProvider) and p.model == "haiku"
    assert p.cli_path == "/usr/bin/claude"
    p = registry.get_provider(s.model_copy(update={"llm_model": "opus"}), "claude_code")
    assert p.model == "opus"


# --- run_chat with a provider-run tool loop --------------------------------------------


@dataclass
class FakeAgentProvider:
    reply: str
    tool_calls: list[tuple[str, dict[str, Any]]]
    name: str = "fake_agent"
    is_local: bool = False
    model: str = "fake-agent-1"
    seen: dict[str, Any] = field(default_factory=dict)

    def complete(self, *_: Any, **__: Any) -> Any:
        raise AssertionError("run_chat must use run_agent")

    def embed(self, texts: Sequence[str]) -> list[list[float]] | None:
        return None

    def run_agent(
        self,
        messages: Sequence[Message],
        *,
        system: str,
        tools: Sequence[ToolSpec],
        execute: Callable[[str, dict[str, Any]], Any],
        max_tool_rounds: int,
    ) -> AgentRun:
        self.seen.update(messages=list(messages), system=system, tools=list(tools))
        done = [(n, a, execute(n, a)) for n, a in self.tool_calls]
        return AgentRun(
            text=self.reply, tool_calls=done, model="fake-agent-2", usage={"input_tokens": 4}
        )


def test_run_chat_uses_run_agent(recommend_db: Env) -> None:
    session, ids = recommend_db
    provider = FakeAgentProvider(
        reply=f"Ajánlom a Sötét erdőt (Netflix).\n[[ajanlott: {ids[101]}]]",
        tool_calls=[("get_recommendations", {"limit": 2, "genres": ["Thriller"]})],
    )
    result = run_chat(
        [ChatMessage(role="user", content="Valami feszült thrillert kérek")],
        session=session,
        tmdb=StubTmdb(),
        provider=provider,
        config=RecommenderConfig(),
        now=NOW,
    )
    assert result.reply == "Ajánlom a Sötét erdőt (Netflix)."
    assert (result.provider, result.model) == ("fake_agent", "fake-agent-2")
    assert result.usage == {"input_tokens": 4} and not result.rounds_exhausted
    [entry] = result.tool_trace
    assert entry.name == "get_recommendations" and entry.arguments["limit"] == 2
    assert entry.title_ids == [ids[101], ids[102]] and entry.result_summary == "2 cím"
    assert result.recommended_title_ids == [ids[101]]
    assert result.recommended_title_ids_source == "marker"
    assert "Szabályok" in provider.seen["system"] and "[[ajanlott:" in provider.seen["system"]
    assert {t.name for t in provider.seen["tools"]} >= {"get_recommendations", "search_titles"}


# --- settings endpoint -----------------------------------------------------------------


@respx.mock
def test_settings_lists_claude_code_option(api: ApiEnv, monkeypatch: pytest.MonkeyPatch) -> None:
    respx.get("http://localhost:1234/v1/models").mock(return_value=httpx.Response(500))
    monkeypatch.setattr(settings_api.shutil, "which", lambda _: None)
    opt = api.client.get("/api/settings/llm").json()["options"]["claude_code"]
    assert opt == {"available": False, "version": None, "models": ["sonnet", "opus", "haiku"]}

    monkeypatch.setattr(settings_api.shutil, "which", lambda _: "/fake/claude")
    monkeypatch.setattr(settings_api, "_claude_version", lambda cli: "2.1.304 (Claude Code)")
    opt = api.client.get("/api/settings/llm").json()["options"]["claude_code"]
    assert opt["available"] is True and opt["version"] == "2.1.304 (Claude Code)"

    r = api.client.put("/api/settings/llm", json={"provider": "claude_code", "model": "sonnet"})
    assert r.status_code == 200 and r.json()["provider"] == "claude_code"
    assert r.json()["model"] == "sonnet"
