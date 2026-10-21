"""Claude Code (CLI) adapter via the official Claude Agent SDK (`claude-agent-sdk`).

The SDK drives the locally installed, logged-in `claude` CLI; authentication is the CLI
login (Claude subscription), there is no API key. Data goes to Anthropic, so the provider
is a cloud provider (`is_local = False`).

Isolation: every call runs in an empty temporary working directory with no settings
sources (no CLAUDE.md, no project/user settings), all built-in tools disabled and
explicitly disallowed, and `dontAsk` permissions; the only callable tools are the backend
tools passed to `run_agent` (exposed through an in-process MCP server named `wtw`).

The CLI runs its own tool loop, so `complete(tools=...)` is rejected; the chat agent uses
`run_agent` (the optional `AgentCapable` capability) instead.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import json
import shutil
import tempfile
from collections.abc import Callable, Sequence
from typing import Any

import claude_agent_sdk as sdk

from whattowatch.llm.base import AgentRun, LLMError, LLMResponse, Message, ToolSpec

CLI_DEFAULT_MODEL = "(cli default)"
MCP_SERVER = "wtw"
DISALLOWED_TOOLS: tuple[str, ...] = (
    "Bash",
    "Read",
    "Write",
    "Edit",
    "MultiEdit",
    "Glob",
    "Grep",
    "WebSearch",
    "WebFetch",
    "Task",
    "NotebookEdit",
    "TodoWrite",
    "Agent",
)
MAX_TURNS_SUBTYPE = "error_max_turns"
CLI_HINT = "Claude Code CLI nem található vagy nincs bejelentkezve (`claude login`)"
_ROLE_LABEL = {"user": "Felhasználó", "assistant": "Asszisztens", "tool": "Eszközeredmény"}


def mcp_tool_name(name: str) -> str:
    return f"mcp__{MCP_SERVER}__{name}"


def render_prompt(messages: Sequence[Message]) -> tuple[list[str], str]:
    """(extra system texts, prompt): earlier turns as a labeled transcript, the last user
    message last."""
    systems = [m.content for m in messages if m.role == "system" and m.content]
    turns = [m for m in messages if m.role != "system" and (m.content or m.tool_calls)]
    if not turns:
        return systems, ""
    last = turns[-1]
    if last.role == "user":
        prior, final = turns[:-1], last.content
    else:
        prior, final = turns, ""
    if not prior:
        return systems, final
    lines = ["Eddigi beszélgetés:"]
    for m in prior:
        lines.append(f"[{_ROLE_LABEL.get(m.role, m.role)}]: {m.content}")
    lines.append("")
    lines.append(f"Új üzenet:\n{final}" if final else "Folytasd a választ.")
    return systems, "\n".join(lines)


def _usage(raw: dict[str, Any] | None) -> dict[str, int]:
    raw = raw or {}
    out: dict[str, int] = {}
    for key in ("input_tokens", "output_tokens"):
        value = raw.get(key)
        out[key] = int(value) if isinstance(value, (int, float)) else 0
    return out


def _run_sync(coro_factory: Callable[[], Any], timeout_s: float) -> Any:
    """Run the coroutine in a fresh event loop on a dedicated thread (works whether or not
    the caller already has a running loop)."""

    async def guarded() -> Any:
        return await asyncio.wait_for(coro_factory(), timeout=timeout_s)

    with concurrent.futures.ThreadPoolExecutor(1) as pool:
        return pool.submit(asyncio.run, guarded()).result()


class ClaudeCodeProvider:
    name = "claude_code"
    is_local = False

    def __init__(
        self, model: str | None = None, *, timeout_s: float = 300, cli_path: str | None = None
    ) -> None:
        self._model = (model or "").strip() or None  # None -> the CLI's default model
        self.model = self._model or CLI_DEFAULT_MODEL
        self.timeout_s = timeout_s
        self.cli_path = cli_path

    # --- plumbing -------------------------------------------------------------------------

    def _options(
        self, system: str, cwd: str, *, max_turns: int, **extra: Any
    ) -> sdk.ClaudeAgentOptions:
        return sdk.ClaudeAgentOptions(
            system_prompt=system,
            model=self._model,
            setting_sources=[],
            cwd=cwd,
            permission_mode="dontAsk",
            tools=[],
            disallowed_tools=list(DISALLOWED_TOOLS),
            max_turns=max_turns,
            cli_path=self.cli_path,
            **extra,
        )

    def _query(
        self,
        prompt: str,
        build: Callable[[str], sdk.ClaudeAgentOptions],
        *,
        tolerate: tuple[str, ...] = (),
    ) -> tuple[Any, str | None]:
        """Run one `query`; return (its `ResultMessage`, the model seen) or raise
        `LLMError`. Error results whose subtype is in `tolerate` are returned."""

        async def collect() -> tuple[Any, str | None]:
            result: Any = None
            model: str | None = None
            with tempfile.TemporaryDirectory(prefix="wtw-claude-") as cwd:
                try:
                    async for msg in sdk.query(prompt=prompt, options=build(cwd)):
                        if isinstance(msg, sdk.AssistantMessage):
                            model = msg.model or model
                        elif isinstance(msg, sdk.ResultMessage):
                            result = msg
                except sdk.CLINotFoundError as exc:
                    raise LLMError(f"{CLI_HINT}.") from exc
                except sdk.ClaudeSDKError as exc:
                    if result is None or not getattr(result, "is_error", False):
                        raise LLMError(
                            f"Claude Code hiba ({type(exc).__name__}); valószínű ok: {CLI_HINT}."
                        ) from exc
            return result, model

        try:
            result, model = _run_sync(collect, self.timeout_s)
        except TimeoutError as exc:
            raise LLMError(
                f"Claude Code időtúllépés ({self.timeout_s:.0f} s); valószínű ok: {CLI_HINT}."
            ) from exc
        except OSError as exc:
            raise LLMError(
                f"Claude Code nem indítható ({type(exc).__name__}): {CLI_HINT}."
            ) from exc
        if result is None:
            raise LLMError(f"Claude Code: nem érkezett eredmény; valószínű ok: {CLI_HINT}.")
        if result.is_error and result.subtype not in tolerate:
            detail = "; ".join(getattr(result, "errors", None) or []) or result.subtype
            raise LLMError(f"Claude Code hiba ({detail}); valószínű ok: {CLI_HINT}.")
        return result, model

    # --- LLMProvider ----------------------------------------------------------------------

    def complete(
        self,
        messages: Sequence[Message],
        *,
        system: str | None = None,
        tools: Sequence[ToolSpec] | None = None,
        json_schema: dict[str, Any] | None = None,
        max_tokens: int = 1024,  # not configurable through the CLI
        temperature: float = 0.2,  # not configurable through the CLI
    ) -> LLMResponse:
        if tools:
            raise LLMError("claude_code provider runs tools itself; use run_agent")
        systems, prompt = render_prompt(messages)
        full_system = "\n\n".join([s for s in (system, *systems) if s])
        extra: dict[str, Any] = {}
        if json_schema is not None:
            extra["output_format"] = {"type": "json_schema", "schema": json_schema}
        result, model = self._query(
            prompt,
            lambda cwd: self._options(
                full_system, cwd, max_turns=1 if json_schema is None else 3, **extra
            ),
        )
        parsed = result.structured_output if json_schema is not None else None
        if json_schema is not None and parsed is None:
            raise LLMError("Claude Code: a válasz nem tartalmaz strukturált (JSON) kimenetet")
        return LLMResponse(
            text=result.result,
            tool_calls=(),
            json=parsed,
            model=model or self.model,
            usage=_usage(result.usage),
            stop_reason=result.stop_reason,
        )

    def embed(self, texts: Sequence[str]) -> list[list[float]] | None:
        return None

    # --- AgentCapable ---------------------------------------------------------------------

    def build_tools(
        self,
        tools: Sequence[ToolSpec],
        execute: Callable[[str, dict[str, Any]], Any],
        calls: list[tuple[str, dict[str, Any], Any]],
    ) -> list[sdk.SdkMcpTool[Any]]:
        """One SDK MCP tool per spec; each call runs `execute` in a worker thread and is
        recorded in `calls` as (name, args, result)."""

        def make(spec: ToolSpec) -> sdk.SdkMcpTool[Any]:
            async def handler(args: dict[str, Any]) -> dict[str, Any]:
                arguments = dict(args) if isinstance(args, dict) else {}
                result = await asyncio.to_thread(execute, spec.name, arguments)
                calls.append((spec.name, arguments, result))
                text = json.dumps(result, ensure_ascii=False, default=str)
                return {"content": [{"type": "text", "text": text}]}

            return sdk.tool(spec.name, spec.description, spec.parameters)(handler)

        return [make(spec) for spec in tools]

    def run_agent(
        self,
        messages: Sequence[Message],
        *,
        system: str,
        tools: Sequence[ToolSpec],
        execute: Callable[[str, dict[str, Any]], Any],
        max_tool_rounds: int,
    ) -> AgentRun:
        systems, prompt = render_prompt(messages)
        full_system = "\n\n".join([s for s in (system, *systems) if s])
        calls: list[tuple[str, dict[str, Any], Any]] = []
        sdk_tools = self.build_tools(tools, execute, calls)

        def build(cwd: str) -> sdk.ClaudeAgentOptions:
            server = sdk.create_sdk_mcp_server(MCP_SERVER, tools=sdk_tools)
            return self._options(
                full_system,
                cwd,
                max_turns=max_tool_rounds + 2,
                mcp_servers={MCP_SERVER: server},
                allowed_tools=[mcp_tool_name(spec.name) for spec in tools],
            )

        result, model = self._query(prompt, build, tolerate=(MAX_TURNS_SUBTYPE,))
        exhausted = result.subtype == MAX_TURNS_SUBTYPE
        return AgentRun(
            text=(result.result or "") if not exhausted else "",
            tool_calls=calls,
            model=model or self.model,
            usage=_usage(result.usage),
            stop_reason="max_turns" if exhausted else result.stop_reason,
        )


def find_cli() -> str | None:
    return shutil.which("claude")
