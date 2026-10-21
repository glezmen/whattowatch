"""Provider-neutral LLM types and the `LLMProvider` protocol.

`LLMResponse.stop_reason` is normalized across providers to `end_turn`, `tool_use`,
`max_tokens` (other values pass through); `usage` uses `input_tokens`/`output_tokens`.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Literal, Protocol

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
