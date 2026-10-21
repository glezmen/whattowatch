"""LLM provider layer (docs/llm.md). Providers never act as a source of truth for
catalog, availability or rating data."""

from whattowatch.llm.base import (
    AgentCapable,
    AgentRun,
    LLMError,
    LLMProvider,
    LLMResponse,
    Message,
    Role,
    ToolCall,
    ToolSpec,
)

__all__ = [
    "AgentCapable",
    "AgentRun",
    "LLMError",
    "LLMProvider",
    "LLMResponse",
    "Message",
    "Role",
    "ToolCall",
    "ToolSpec",
]
