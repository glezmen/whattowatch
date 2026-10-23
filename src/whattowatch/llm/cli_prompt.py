"""Helpers shared by the CLI-backed providers (Claude Code, GitHub Copilot): the chat
history rendered into one prompt, and running an SDK coroutine from sync code."""

from __future__ import annotations

import asyncio
import concurrent.futures
from collections.abc import Callable, Sequence
from typing import Any

from whattowatch.llm.base import Message

CLI_DEFAULT_MODEL = "(cli default)"
_ROLE_LABEL = {"user": "Felhasználó", "assistant": "Asszisztens", "tool": "Eszközeredmény"}


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


def run_sync(coro_factory: Callable[[], Any], timeout_s: float) -> Any:
    """Run the coroutine in a fresh event loop on a dedicated thread (works whether or not
    the caller already has a running loop)."""

    async def guarded() -> Any:
        return await asyncio.wait_for(coro_factory(), timeout=timeout_s)

    with concurrent.futures.ThreadPoolExecutor(1) as pool:
        return pool.submit(asyncio.run, guarded()).result()
