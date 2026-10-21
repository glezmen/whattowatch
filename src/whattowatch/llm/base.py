"""Provider-neutral LLM types and the `LLMProvider` protocol.

`LLMResponse.stop_reason` is normalized across providers to `end_turn`, `tool_use`,
`max_tokens` (other values pass through); `usage` uses `input_tokens`/`output_tokens`.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol, runtime_checkable

Role = Literal["system", "user", "assistant", "tool"]


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True)
class Message:
    """A chat turn. `role="tool"` carries a tool result in `content` with `tool_call_id`."""

    role: Role
    content: str = ""
    tool_calls: tuple[ToolCall, ...] = ()
    tool_call_id: str | None = None


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    parameters: dict[str, Any]  # JSON Schema object


@dataclass(frozen=True)
class LLMResponse:
    text: str | None
    tool_calls: tuple[ToolCall, ...]
    json: Any | None
    model: str
    usage: dict[str, int]
    stop_reason: str | None = None


class LLMError(Exception):
    """Any provider failure (configuration, transport, HTTP status, refusal, bad output)."""


class LLMProvider(Protocol):
    name: str
    is_local: bool
    model: str

    def complete(
        self,
        messages: Sequence[Message],
        *,
        system: str | None = None,
        tools: Sequence[ToolSpec] | None = None,
        json_schema: dict[str, Any] | None = None,
        max_tokens: int = 1024,
        temperature: float = 0.2,
    ) -> LLMResponse: ...

    def embed(self, texts: Sequence[str]) -> list[list[float]] | None: ...


@dataclass
class AgentRun:
    """Result of a provider-run tool loop. `tool_calls`: (name, arguments, result) in call
    order; `stop_reason` is `max_turns` when the provider ran out of rounds."""

    text: str
    tool_calls: list[tuple[str, dict[str, Any], Any]] = field(default_factory=list)
    model: str = ""
    usage: dict[str, int] = field(default_factory=dict)
    stop_reason: str | None = None


@runtime_checkable
class AgentCapable(Protocol):
    """Optional capability: the provider runs the tool loop itself (e.g. Claude Code),
    calling `execute(name, arguments)` for each backend tool call."""

    def run_agent(
        self,
        messages: Sequence[Message],
        *,
        system: str,
        tools: Sequence[ToolSpec],
        execute: Callable[[str, dict[str, Any]], Any],
        max_tool_rounds: int,
    ) -> AgentRun: ...
