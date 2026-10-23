"""GitHub Copilot (CLI) adapter via the official GitHub Copilot SDK (`github-copilot-sdk`).

The SDK drives the locally installed, logged-in `copilot` CLI over stdio; authentication
is the CLI login (Copilot subscription), there is no API key. The CLI path is always
passed explicitly, so the SDK never downloads a runtime of its own. Data goes to GitHub
and the selected model's vendor, so the provider is a cloud provider (`is_local = False`).

Isolation: every call runs in an empty temporary working directory; Copilot's own system
prompt is replaced with ours; custom instructions, skills, the session store, telemetry,
file hooks and host git operations are off; `available_tools=["custom:*"]` hides every
built-in and MCP tool, so the only callable tools are the backend tools passed to
`run_agent` (none for `complete`).

The CLI runs its own tool loop, so `complete(tools=...)` is rejected; the chat agent uses
`run_agent` (the optional `AgentCapable` capability) instead.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import shutil
import tempfile
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

from copilot import CopilotClient, RuntimeConnection, Tool, ToolInvocation, ToolResult
from copilot._jsonrpc import JsonRpcError
from copilot.rpc import PermissionDecisionApproveOnce
from copilot.session_events import AssistantMessageData, AssistantUsageData, SessionEvent

from whattowatch.llm.base import AgentRun, LLMError, LLMResponse, Message, ToolSpec
from whattowatch.llm.cli_prompt import CLI_DEFAULT_MODEL, render_prompt, run_sync

# The SDK logs version-mismatch fallbacks (with tracebacks) at WARNING; keep them out of
# the server log.
logging.getLogger("copilot").setLevel(logging.ERROR)

CLI_HINT = "GitHub Copilot CLI nem található vagy nincs bejelentkezve (`copilot login`)"
TEMP_PREFIX = "wtw-copilot-"
ONLY_CUSTOM_TOOLS = ["custom:*"]
TOOL_LIMIT_ERROR = "tool call limit reached"
STATUS_TIMEOUT = 15.0


def _approve(request: Any, invocation: Any) -> PermissionDecisionApproveOnce:
    """Only the backend's own tools are reachable (`available_tools=["custom:*"]`)."""
    return PermissionDecisionApproveOnce()


def _client(cli_path: str, cwd: str) -> CopilotClient:
    return CopilotClient(
        connection=RuntimeConnection.for_stdio(path=cli_path),
        working_directory=cwd,
        log_level="error",
    )


@dataclass
class _Outcome:
    text: str
    model: str | None
    usage: dict[str, int]
    calls: list[tuple[str, dict[str, Any], Any]] = field(default_factory=list)
    exhausted: bool = False


@dataclass(frozen=True)
class CopilotStatus:
    """Login state of the installed CLI (`logged_in` None: unknown)."""

    logged_in: bool | None
    login: str | None
    models: list[str]


class CopilotProvider:
    name = "copilot"
    is_local = False

    def __init__(
        self, model: str | None = None, *, timeout_s: float = 300, cli_path: str | None = None
    ) -> None:
        self._model = (model or "").strip() or None  # None -> the CLI's default model
        self.model = self._model or CLI_DEFAULT_MODEL
        self.timeout_s = timeout_s
        self.cli_path = cli_path

    # --- plumbing -------------------------------------------------------------------------

    def build_tools(
        self,
        tools: Sequence[ToolSpec],
        execute: Callable[[str, dict[str, Any]], Any],
        outcome: _Outcome,
        max_calls: int | None = None,
    ) -> list[Tool]:
        """One SDK tool per spec; each call runs `execute` in a worker thread and is
        recorded in `outcome.calls` as (name, args, result). After `max_calls` calls every
        further call fails and the run is marked exhausted."""

        def make(spec: ToolSpec) -> Tool:
            async def handler(invocation: ToolInvocation) -> ToolResult:
                if max_calls is not None and len(outcome.calls) >= max_calls:
                    outcome.exhausted = True
                    return ToolResult(
                        result_type="failure",
                        error=TOOL_LIMIT_ERROR,
                        text_result_for_llm="Elérted az eszközhívások korlátját; "
                        "válaszolj a már ismert adatokból.",
                    )
                args = invocation.arguments
                arguments = dict(args) if isinstance(args, dict) else {}
                result = await asyncio.to_thread(execute, spec.name, arguments)
                outcome.calls.append((spec.name, arguments, result))
                text = json.dumps(result, ensure_ascii=False, default=str)
                return ToolResult(text_result_for_llm=text)

            return Tool(
                name=spec.name,
                description=spec.description,
                parameters=spec.parameters,
                handler=handler,
                skip_permission=True,
            )

        return [make(spec) for spec in tools]

    def _send(
        self,
        prompt: str,
        system: str,
        *,
        response_schema: dict[str, Any] | None = None,
        tools: Callable[[_Outcome], list[Tool]] | None = None,
    ) -> _Outcome:
        """One isolated session: start the CLI, check the login, send `prompt`, wait for
        the final assistant message. Raises `LLMError` on any failure."""
        cli = self.cli_path or find_cli()
        if cli is None:
            raise LLMError(f"{CLI_HINT}.")

        async def run() -> _Outcome:
            outcome = _Outcome(text="", model=None, usage={"input_tokens": 0, "output_tokens": 0})

            def on_event(event: SessionEvent) -> None:
                data = event.data
                if isinstance(data, AssistantUsageData):
                    outcome.model = data.model or outcome.model
                    outcome.usage["input_tokens"] += data.input_tokens or 0
                    outcome.usage["output_tokens"] += data.output_tokens or 0

            with tempfile.TemporaryDirectory(prefix=TEMP_PREFIX) as cwd:
                client = _client(cli, cwd)
                try:
                    await client.start()
                    auth = await client.get_auth_status()
                    if not auth.isAuthenticated:
                        raise LLMError(
                            "A GitHub Copilot CLI nincs bejelentkezve: "
                            "futtasd a `copilot login` parancsot."
                        )
                    session = await client.create_session(
                        model=self._model,
                        tools=tools(outcome) if tools is not None else [],
                        system_message={"mode": "replace", "content": system},
                        available_tools=list(ONLY_CUSTOM_TOOLS),
                        working_directory=cwd,
                        streaming=False,
                        skip_custom_instructions=True,
                        enable_skills=False,
                        enable_session_store=False,
                        enable_session_telemetry=False,
                        enable_file_hooks=False,
                        enable_host_git_operations=False,
                        on_permission_request=_approve,
                        on_event=on_event,
                    )
                    try:
                        event = await session.send_and_wait(
                            prompt, response_schema=response_schema, timeout=self.timeout_s
                        )
                    finally:
                        with contextlib.suppress(Exception):
                            await session.disconnect()
                finally:
                    with contextlib.suppress(Exception):
                        await client.stop()
            data = event.data if event is not None else None
            if isinstance(data, AssistantMessageData):
                outcome.text = data.content or ""
                outcome.model = data.model or outcome.model
            elif not outcome.exhausted:
                raise LLMError(f"Copilot: nem érkezett válasz; valószínű ok: {CLI_HINT}.")
            return outcome

        try:
            result: _Outcome = run_sync(run, self.timeout_s + 10)
        except LLMError:
            raise
        except TimeoutError as exc:
            raise LLMError(
                f"Copilot időtúllépés ({self.timeout_s:.0f} s); valószínű ok: {CLI_HINT}."
            ) from exc
        except OSError as exc:
            raise LLMError(f"Copilot nem indítható ({type(exc).__name__}): {CLI_HINT}.") from exc
        except (RuntimeError, JsonRpcError) as exc:
            raise LLMError(
                f"Copilot hiba ({type(exc).__name__}); valószínű ok: {CLI_HINT}."
            ) from exc
        return result

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
            raise LLMError("copilot provider runs tools itself; use run_agent")
        systems, prompt = render_prompt(messages)
        full_system = "\n\n".join([s for s in (system, *systems) if s])
        out = self._send(prompt, full_system, response_schema=json_schema)
        parsed: Any = None
        if json_schema is not None:
            try:
                parsed = json.loads(out.text)
            except ValueError:
                parsed = None
            if parsed is None:
                raise LLMError("Copilot: a válasz nem tartalmaz strukturált (JSON) kimenetet")
        return LLMResponse(
            text=out.text,
            tool_calls=(),
            json=parsed,
            model=out.model or self.model,
            usage=out.usage,
            stop_reason="end_turn",
        )

    def embed(self, texts: Sequence[str]) -> list[list[float]] | None:
        return None

    # --- AgentCapable ---------------------------------------------------------------------

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
        out = self._send(
            prompt,
            full_system,
            tools=lambda outcome: self.build_tools(tools, execute, outcome, max_tool_rounds),
        )
        return AgentRun(
            text="" if out.exhausted else out.text,
            tool_calls=out.calls,
            model=out.model or self.model,
            usage=out.usage,
            stop_reason="max_turns" if out.exhausted else "end_turn",
        )


def find_cli() -> str | None:
    return shutil.which("copilot")


def cli_status(cli_path: str, timeout_s: float = STATUS_TIMEOUT) -> CopilotStatus:
    """Login state and model ids of the installed CLI; any failure -> all unknown."""

    async def probe() -> CopilotStatus:
        with tempfile.TemporaryDirectory(prefix=TEMP_PREFIX) as cwd:
            client = _client(cli_path, cwd)
            try:
                await client.start()
                auth = await client.get_auth_status()
                if not auth.isAuthenticated:
                    return CopilotStatus(logged_in=False, login=None, models=[])
                models = [m.id for m in await client.list_models() if m.id]
                return CopilotStatus(logged_in=True, login=auth.login, models=models)
            finally:
                with contextlib.suppress(Exception):
                    await client.stop()

    try:
        status: CopilotStatus = run_sync(probe, timeout_s)
    except Exception:  # noqa: BLE001 - the status is informational only
        return CopilotStatus(logged_in=None, login=None, models=[])
    return status
