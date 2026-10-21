"""Anthropic Messages API adapter (official `anthropic` SDK).

Model-specific constraints honored here: no `thinking` parameter (adaptive thinking is
the default), no `temperature` (not accepted by the SDK for this model family; the
argument is ignored), no forced `tool_choice` (only `auto`), no assistant prefill.
Cost is controlled with `output_config.effort`: `low` for structured extraction
(`json_schema` given), `medium` otherwise.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

import anthropic

from whattowatch.config import Settings
from whattowatch.llm.base import LLMError, LLMResponse, Message, ToolCall, ToolSpec

ANTHROPIC_DEFAULT_MODEL = "claude-opus-5-5"
SERVER_FALLBACK_BETA = "server-side-fallback-2026-07-01"
TIMEOUT_SECONDS = 120.0
MAX_RETRIES = 2


def _translate(messages: Sequence[Message]) -> tuple[list[str], list[dict[str, Any]]]:
    """Split out system texts and convert turns to Anthropic content blocks.

    Consecutive tool results are merged into one user turn (required by the API).
    """
    systems: list[str] = []
    out: list[dict[str, Any]] = []
    for m in messages:
        if m.role == "system":
            if m.content:
                systems.append(m.content)
            continue
        if m.role == "tool":
            role = "user"
            blocks: list[dict[str, Any]] = [
                {"type": "tool_result", "tool_use_id": m.tool_call_id or "", "content": m.content}
            ]
        elif m.role == "assistant":
            role = "assistant"
            blocks = [{"type": "text", "text": m.content}] if m.content else []
            blocks += [
                {"type": "tool_use", "id": tc.id, "name": tc.name, "input": tc.arguments}
                for tc in m.tool_calls
            ]
        else:
            role = "user"
            blocks = [{"type": "text", "text": m.content}]
        if not blocks:
            continue
        if out and out[-1]["role"] == role:
            out[-1]["content"].extend(blocks)
        else:
            out.append({"role": role, "content": blocks})
    return systems, out


class AnthropicProvider:
    is_local = False

    def __init__(
        self,
        api_key: str,
        model: str = ANTHROPIC_DEFAULT_MODEL,
        *,
        server_fallback: bool = True,
        client: Any | None = None,
    ) -> None:
        self.name = "anthropic"
        self.model = model
        self.server_fallback = server_fallback
        self._client: Any = client or anthropic.Anthropic(
            api_key=api_key, max_retries=MAX_RETRIES, timeout=TIMEOUT_SECONDS
        )

    @classmethod
    def from_settings(cls, settings: Settings, *, client: Any | None = None) -> AnthropicProvider:
        if not settings.anthropic_api_key:
            raise LLMError(
                "Az Anthropic provider nincs beállítva: add meg az ANTHROPIC_API_KEY értékét."
            )
        return cls(
            settings.anthropic_api_key,
            settings.llm_model or ANTHROPIC_DEFAULT_MODEL,
            server_fallback=settings.anthropic_server_fallback,
            client=client,
        )

    def complete(
        self,
        messages: Sequence[Message],
        *,
        system: str | None = None,
        tools: Sequence[ToolSpec] | None = None,
        json_schema: dict[str, Any] | None = None,
        max_tokens: int = 1024,
        temperature: float = 0.2,  # ignored, see module docstring
    ) -> LLMResponse:
        systems, converted = _translate(messages)
        if system:
            systems.insert(0, system)
        output_config: dict[str, Any] = {"effort": "low" if json_schema is not None else "medium"}
        if json_schema is not None:
            output_config["format"] = {"type": "json_schema", "schema": json_schema}
        kwargs: dict[str, Any] = {
            "model": self.model,
            "max_tokens": max_tokens,
            "messages": converted,
            "output_config": output_config,
        }
        if systems:
            kwargs["system"] = "\n\n".join(systems)
        if tools:
            kwargs["tools"] = [
                {"name": t.name, "description": t.description, "input_schema": t.parameters}
                for t in tools
            ]
            kwargs["tool_choice"] = {"type": "auto"}
        try:
            if self.server_fallback:
                resp = self._client.beta.messages.create(
                    **kwargs, betas=[SERVER_FALLBACK_BETA], fallbacks="default"
                )
            else:
                resp = self._client.messages.create(**kwargs)
        except anthropic.APIStatusError as exc:
            raise LLMError(f"Anthropic API hiba (HTTP {exc.status_code}): {exc.message}") from exc
        except anthropic.APIConnectionError as exc:
            raise LLMError(f"Anthropic API kapcsolódási hiba: {type(exc).__name__}") from exc

        stop_reason = getattr(resp, "stop_reason", None)
        if stop_reason == "refusal":
            details = getattr(resp, "stop_details", None)
            category = getattr(details, "category", None) or "ismeretlen"
            raise LLMError(f"Az Anthropic modell elutasította a kérést (kategória: {category}).")

        texts: list[str] = []
        calls: list[ToolCall] = []
        for block in resp.content:
            if block.type == "text":
                texts.append(block.text)
            elif block.type == "tool_use":
                calls.append(ToolCall(id=block.id, name=block.name, arguments=dict(block.input)))
        text = "".join(texts) if texts else None
        parsed: Any | None = None
        if json_schema is not None and text:
            try:
                parsed = json.loads(text)
            except json.JSONDecodeError as exc:
                raise LLMError("Anthropic: a válasz nem érvényes JSON") from exc
        usage = getattr(resp, "usage", None)
        return LLMResponse(
            text=text,
            tool_calls=tuple(calls),
            json=parsed,
            model=str(getattr(resp, "model", None) or self.model),
            usage={
                "input_tokens": int(getattr(usage, "input_tokens", 0) or 0),
                "output_tokens": int(getattr(usage, "output_tokens", 0) or 0),
            },
            stop_reason=stop_reason,
        )

    def embed(self, texts: Sequence[str]) -> list[list[float]] | None:
        return None  # Anthropic does not offer an embeddings endpoint
