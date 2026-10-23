"""GitHub Copilot (CLI) provider via a faked GitHub Copilot SDK client."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
import respx
from conftest import ApiEnv
from copilot import ToolInvocation, ToolResult
from copilot._jsonrpc import JsonRpcError
from copilot.session_events import AssistantMessageData, AssistantUsageData

from whattowatch.api import settings as settings_api
from whattowatch.config import Settings
from whattowatch.llm import copilot_provider as cp
from whattowatch.llm import registry
from whattowatch.llm.base import AgentCapable, LLMError, Message, ToolSpec
from whattowatch.llm.copilot_provider import CopilotProvider

CLI = "/usr/local/bin/copilot"
# The conftest autouse fixture stubs `cli_status`; keep the real one for its own test.
REAL_CLI_STATUS = cp.cli_status


def message(content: str = "Működik.", model: str | None = "gpt-x") -> Any:
    return SimpleNamespace(data=AssistantMessageData(content=content, message_id="m1", model=model))


def usage(inp: int, out: int) -> Any:
    return SimpleNamespace(
        data=AssistantUsageData(model="gpt-x", input_tokens=inp, output_tokens=out)
    )


@dataclass
class FakeCopilot:
    """Stands in for `CopilotClient`; one instance per test records every call."""

    reply: Any = field(default_factory=message)
    authenticated: bool = True
    error: BaseException | None = None
    # Called with the session kwargs before the reply (e.g. to invoke tool handlers).
    script: Callable[[dict[str, Any]], Any] | None = None
    usage_events: list[Any] = field(default_factory=lambda: [usage(7, 3)])
    clients: list[dict[str, Any]] = field(default_factory=list)
    sessions: list[dict[str, Any]] = field(default_factory=list)
    sends: list[dict[str, Any]] = field(default_factory=list)
    log: list[str] = field(default_factory=list)

    def __call__(self, **kwargs: Any) -> Any:
        self.clients.append(kwargs)
        fake = self

        class Session:
            async def send_and_wait(self, prompt: str, **kw: Any) -> Any:
                fake.sends.append({"prompt": prompt, **kw})
                opts = fake.sessions[-1]
                if fake.script is not None:
                    await fake.script(opts)
                for ev in fake.usage_events:
                    opts["on_event"](ev)
                if fake.error is not None:
                    raise fake.error
                return fake.reply

            async def disconnect(self) -> None:
                fake.log.append("disconnect")

        class Client:
            async def start(self) -> None:
                fake.log.append("start")

            async def stop(self) -> None:
                fake.log.append("stop")

            async def get_auth_status(self) -> Any:
                return SimpleNamespace(isAuthenticated=fake.authenticated, login="norbi")

            async def list_models(self) -> list[Any]:
                return [SimpleNamespace(id="auto", name="Auto")]

            async def create_session(self, **kw: Any) -> Session:
                fake.sessions.append(kw)
                return Session()

        return Client()


def install(monkeypatch: pytest.MonkeyPatch, fake: FakeCopilot) -> FakeCopilot:
    monkeypatch.setattr(cp, "CopilotClient", fake)
    return fake


def provider(model: str | None = None, **kw: Any) -> CopilotProvider:
    return CopilotProvider(model, cli_path=CLI, **kw)


def test_complete_text_and_isolation(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = install(monkeypatch, FakeCopilot())
    resp = provider("gpt-5").complete(
        [
            Message(role="user", content="Szia"),
            Message(role="assistant", content="Helló!"),
            Message(role="user", content="Működik?"),
        ],
        system="Rendszer",
    )
    assert resp.text == "Működik." and resp.json is None and resp.tool_calls == ()
    assert resp.model == "gpt-x" and resp.stop_reason == "end_turn"
    assert resp.usage == {"input_tokens": 7, "output_tokens": 3}
    [client] = fake.clients
    conn = client["connection"]
    assert conn.path == CLI  # never the SDK's own (downloaded) runtime
    assert "wtw-copilot-" in client["working_directory"]
    [opts] = fake.sessions
    assert opts["available_tools"] == ["custom:*"] and opts["tools"] == []
    assert opts["system_message"] == {"mode": "replace", "content": "Rendszer"}
    assert opts["skip_custom_instructions"] is True and opts["enable_skills"] is False
    assert opts["enable_session_store"] is False and opts["enable_session_telemetry"] is False
    assert opts["enable_file_hooks"] is False and opts["enable_host_git_operations"] is False
    assert opts["streaming"] is False and opts["model"] == "gpt-5"
    assert opts["working_directory"] == client["working_directory"]
    [send] = fake.sends
    assert send["response_schema"] is None
    prompt = send["prompt"]
    assert "[Felhasználó]: Szia" in prompt and "[Asszisztens]: Helló!" in prompt
    assert prompt.rstrip().endswith("Működik?")
    assert fake.log == ["start", "disconnect", "stop"]


def test_default_model_label() -> None:
    assert CopilotProvider(None).model == "(cli default)"
    p = CopilotProvider("auto")
    assert p.model == "auto" and p.name == "copilot" and p.is_local is False
    assert p.embed(["x"]) is None


def test_complete_json_schema(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = install(monkeypatch, FakeCopilot(reply=message('{"signals": [{"key": "humor"}]}')))
    schema = {"type": "object", "properties": {"signals": {"type": "array"}}}
    resp = provider().complete([Message(role="user", content="x")], json_schema=schema)
    assert resp.json == {"signals": [{"key": "humor"}]}
    assert fake.sends[0]["response_schema"] == schema and fake.sessions[0]["model"] is None


def test_complete_invalid_json(monkeypatch: pytest.MonkeyPatch) -> None:
    install(monkeypatch, FakeCopilot(reply=message("nem JSON")))
    with pytest.raises(LLMError, match="strukturált"):
        provider().complete([Message(role="user", content="x")], json_schema={"type": "object"})


def test_complete_rejects_tools() -> None:
    spec = ToolSpec("t", "d", {"type": "object", "properties": {}})
    with pytest.raises(LLMError, match="run_agent"):
        provider().complete([Message(role="user", content="x")], tools=[spec])


def test_not_logged_in(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = install(monkeypatch, FakeCopilot(authenticated=False))
    with pytest.raises(LLMError, match="copilot login"):
        provider().complete([Message(role="user", content="x")])
    assert fake.sessions == [] and fake.log == ["start", "stop"]


@pytest.mark.parametrize(
    "error",
    [
        RuntimeError("Session aborted before response completed"),
        JsonRpcError(-32603, "boom"),
        OSError("no such file"),
        TimeoutError("Timeout after 300s waiting for session.idle"),
    ],
)
def test_failures_map_to_llm_error(monkeypatch: pytest.MonkeyPatch, error: Exception) -> None:
    fake = install(monkeypatch, FakeCopilot(error=error))
    with pytest.raises(LLMError, match="copilot login"):
        provider().complete([Message(role="user", content="x")])
    assert fake.log[-2:] == ["disconnect", "stop"]


def test_no_reply_maps_to_llm_error(monkeypatch: pytest.MonkeyPatch) -> None:
    install(monkeypatch, FakeCopilot(reply=None))
    with pytest.raises(LLMError, match="nem érkezett"):
        provider().complete([Message(role="user", content="x")])


def test_timeout_maps_to_llm_error(monkeypatch: pytest.MonkeyPatch) -> None:
    async def slow(_: dict[str, Any]) -> None:
        await asyncio.sleep(5)

    real = cp.run_sync
    monkeypatch.setattr(cp, "run_sync", lambda factory, _t: real(factory, 0.05))
    install(monkeypatch, FakeCopilot(script=slow))
    with pytest.raises(LLMError, match="időtúllépés"):
        provider(timeout_s=0.05).complete([Message(role="user", content="x")])


def test_missing_cli(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cp.shutil, "which", lambda _: None)
    with pytest.raises(LLMError, match="copilot login"):
        CopilotProvider(None).complete([Message(role="user", content="x")])


SPECS = [
    ToolSpec("search_titles", "Keresés", {"type": "object", "properties": {"q": {}}}),
    ToolSpec("get_watchlist", "Watchlist", {"type": "object", "properties": {}}),
]


def invoke(opts: dict[str, Any], name: str, args: Any) -> Any:
    tool = next(t for t in opts["tools"] if t.name == name)
    return tool.handler(ToolInvocation(tool_name=name, arguments=args))


def test_run_agent_registers_and_executes_tools(monkeypatch: pytest.MonkeyPatch) -> None:
    executed: list[tuple[str, dict[str, Any]]] = []
    results: list[ToolResult] = []

    def execute(name: str, args: dict[str, Any]) -> dict[str, Any]:
        executed.append((name, args))
        return {"hits": [{"title_id": 5, "title": "Sötét erdő"}]}

    async def call_tools(opts: dict[str, Any]) -> None:
        results.append(await invoke(opts, "search_titles", {"q": "erdő"}))
        results.append(await invoke(opts, "get_watchlist", None))

    fake = install(monkeypatch, FakeCopilot(reply=message("Kész"), script=call_tools))
    p = provider()
    run = p.run_agent(
        [Message(role="user", content="Mit nézzek?")],
        system="S",
        tools=SPECS,
        execute=execute,
        max_tool_rounds=4,
    )
    assert isinstance(p, AgentCapable)
    assert run.text == "Kész" and run.model == "gpt-x" and run.stop_reason == "end_turn"
    hit = {"hits": [{"title_id": 5, "title": "Sötét erdő"}]}
    assert run.tool_calls == [("search_titles", {"q": "erdő"}, hit), ("get_watchlist", {}, hit)]
    assert executed == [("search_titles", {"q": "erdő"}), ("get_watchlist", {})]
    assert "Sötét erdő" in results[0].text_result_for_llm
    assert results[0].result_type == "success"
    [opts] = fake.sessions
    tools = opts["tools"]
    assert [t.name for t in tools] == ["search_titles", "get_watchlist"]
    assert tools[0].parameters == SPECS[0].parameters and tools[0].description == "Keresés"
    assert all(t.skip_permission for t in tools)
    assert opts["available_tools"] == ["custom:*"]
    assert opts["system_message"] == {"mode": "replace", "content": "S"}


def test_run_agent_tool_limit_is_max_turns(monkeypatch: pytest.MonkeyPatch) -> None:
    results: list[ToolResult] = []

    async def call_tools(opts: dict[str, Any]) -> None:
        for _ in range(3):
            results.append(await invoke(opts, "get_watchlist", {}))

    install(monkeypatch, FakeCopilot(reply=message("Részleges"), script=call_tools))
    run = provider().run_agent(
        [Message(role="user", content="x")],
        system="S",
        tools=SPECS,
        execute=lambda *_: {"items": []},
        max_tool_rounds=2,
    )
    assert run.stop_reason == "max_turns" and run.text == ""
    assert len(run.tool_calls) == 2
    assert [r.result_type for r in results] == ["success", "success", "failure"]
    assert results[2].error == "tool call limit reached"


def test_registry_requires_cli(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cp.shutil, "which", lambda _: None)
    with pytest.raises(LLMError, match="nincs telepítve"):
        registry.get_provider(Settings(_env_file=None), "copilot")  # type: ignore[call-arg]
    monkeypatch.setattr(cp.shutil, "which", lambda _: CLI)
    s = Settings(_env_file=None, copilot_cli_model="auto")  # type: ignore[call-arg]
    p = registry.get_provider(s, "copilot")
    assert isinstance(p, CopilotProvider) and p.model == "auto" and p.cli_path == CLI
    p = registry.get_provider(s.model_copy(update={"llm_model": "gpt-5"}), "copilot")
    assert p.model == "gpt-5"
    assert "copilot" in registry.PROVIDER_NAMES


def test_cli_status(monkeypatch: pytest.MonkeyPatch) -> None:
    install(monkeypatch, FakeCopilot())
    assert REAL_CLI_STATUS(CLI) == cp.CopilotStatus(logged_in=True, login="norbi", models=["auto"])
    install(monkeypatch, FakeCopilot(authenticated=False))
    assert REAL_CLI_STATUS(CLI) == cp.CopilotStatus(logged_in=False, login=None, models=[])

    class Broken:
        def __init__(self, **_: Any) -> None:
            raise OSError("boom")

    monkeypatch.setattr(cp, "CopilotClient", Broken)
    assert REAL_CLI_STATUS(CLI) == cp.CopilotStatus(logged_in=None, login=None, models=[])


# --- settings endpoint -----------------------------------------------------------------


@respx.mock
def test_settings_lists_copilot_option(api: ApiEnv, monkeypatch: pytest.MonkeyPatch) -> None:
    respx.get("http://localhost:1234/v1/models").mock(return_value=httpx.Response(500))
    monkeypatch.setattr(settings_api.shutil, "which", lambda _: None)
    opt = api.client.get("/api/settings/llm").json()["options"]["copilot"]
    assert opt == {
        "available": False,
        "version": None,
        "logged_in": None,
        "login": None,
        "models": [],
    }

    monkeypatch.setattr(settings_api.shutil, "which", lambda _: "/fake/copilot")
    monkeypatch.setattr(settings_api, "_copilot_version", lambda cli: "1.0.100")
    monkeypatch.setattr(settings_api, "_copilot_status", lambda cli: (True, "norbi", ["auto"]))
    opt = api.client.get("/api/settings/llm").json()["options"]["copilot"]
    assert opt == {
        "available": True,
        "version": "1.0.100",
        "logged_in": True,
        "login": "norbi",
        "models": ["auto"],
    }

    r = api.client.put("/api/settings/llm", json={"provider": "copilot", "model": None})
    assert r.status_code == 200 and r.json()["provider"] == "copilot"
    assert r.json()["model"] is None


def test_copilot_version_parsing(monkeypatch: pytest.MonkeyPatch) -> None:
    out = SimpleNamespace(stdout="GitHub Copilot CLI 1.0.100.\nRun 'copilot update'.\n")
    monkeypatch.setattr(settings_api.subprocess, "run", lambda *a, **k: out)
    settings_api._copilot_version.cache_clear()
    try:
        assert settings_api._copilot_version("/x/copilot") == "1.0.100"
    finally:
        settings_api._copilot_version.cache_clear()


def test_copilot_status_is_cached(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    def status(cli: str, *_: Any) -> cp.CopilotStatus:
        calls.append(cli)
        return cp.CopilotStatus(logged_in=True, login="norbi", models=["auto"])

    monkeypatch.setattr(cp, "cli_status", status)
    monkeypatch.setattr(settings_api, "_copilot_status_cache", {})
    assert settings_api._copilot_status("/y/copilot") == (True, "norbi", ["auto"])
    assert settings_api._copilot_status("/y/copilot") == (True, "norbi", ["auto"])
    assert calls == ["/y/copilot"]


def test_llm_test_endpoint_with_copilot(api: ApiEnv, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cp.shutil, "which", lambda _: CLI)
    install(monkeypatch, FakeCopilot(reply=message("Igen.")))
    r = api.client.post("/api/settings/llm/test", json={"provider": "copilot"})
    body = r.json()
    assert r.status_code == 200 and body["ok"] is True and body["reply"] == "Igen."
    assert body["model"] == "gpt-x"
