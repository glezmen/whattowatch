"""LLM provider layer (docs/llm.md). Providers never act as a source of truth for
catalog, availability or rating data."""

from whattowatch.llm.base import (
    LLMError,
    LLMProvider,
    LLMResponse,
    Message,
    Role,
    ToolCall,
    ToolSpec,
)

__all__ = ["LLMError", "LLMProvider", "LLMResponse", "Message", "Role", "ToolCall", "ToolSpec"]
