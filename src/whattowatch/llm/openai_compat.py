"""OpenAI-compatible chat/embeddings adapter (LM Studio, OpenAI) over sync httpx."""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

import httpx

from whattowatch.config import Settings
from whattowatch.llm.base import LLMError, LLMResponse, Message, ToolCall, ToolSpec

OPENAI_BASE_URL = "https://api.openai.com/v1"
# Placeholder default only; availability on the user's account is not verified.
OPENAI_DEFAULT_MODEL = "gpt-4o-mini"

_STOP_REASONS = {"stop": "end_turn", "tool_calls": "tool_use", "length": "max_tokens"}


class OpenAICompatProvider:
    def __init__(
        self,
        base_url: str,
        api_key: str | None,
        model: str | None,
        name: str,
        is_local: bool,
        client: httpx.Client | None = None,
        timeout: float = 60,
        *,
        embedding_model: str | None = None,
        max_tokens_field: str = "max_tokens",
    ) -> None:
        self.name = name
        self.is_local = is_local
        self._model = model
        self.embedding_model = embedding_model
        self._max_tokens_field = max_tokens_field
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        if client is None:
            client = httpx.Client(base_url=base_url.rstrip("/"), timeout=timeout)
        client.headers.update(headers)
        self._client = client

    # --- model ---------------------------------------------------------------------------
    @property
    def model(self) -> str:
        """Configured model, or (lazily) the first model the server lists."""
        if self._model is None:
            models = self.list_models()
            if not models:
                raise LLMError(f"{self.name}: nincs betöltött modell (GET /models üres).")
            self._model = models[0]
        return self._model

    @model.setter
    def model(self, value: str) -> None:
        self._model = value

    def list_models(self) -> list[str]:
        data = self._request("GET", "/models")
        return [str(m["id"]) for m in data.get("data", []) if isinstance(m, dict) and "id" in m]

    # --- transport -----------------------------------------------------------------------
    def _request(self, method: str, path: str, body: dict[str, Any] | None = None) -> Any:
        try:
            r = self._client.request(method, path, json=body)
        except httpx.HTTPError as exc:
            raise LLMError(f"{self.name}: kapcsolódási hiba ({type(exc).__name__})") from exc
        if r.status_code >= 400:
            raise LLMError(f"{self.name}: HTTP {r.status_code} hiba ({path}): {r.text[:300]}")
        try:
            return r.json()
        except ValueError as exc:
            raise LLMError(f"{self.name}: érvénytelen JSON-válasz ({path})") from exc

    # --- chat ----------------------------------------------------------------------------
    @staticmethod
    def _messages(messages: Sequence[Message], system: str | None) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        if system:
            out.append({"role": "system", "content": system})
        for m in messages:
            if m.role == "tool":
                out.append({"role": "tool", "tool_call_id": m.tool_call_id, "content": m.content})
            elif m.role == "assistant" and m.tool_calls:
                out.append(
                    {
                        "role": "assistant",
                        "content": m.content or None,
                        "tool_calls": [
                            {
                                "id": tc.id,
                                "type": "function",
                                "function": {
                                    "name": tc.name,
                                    "arguments": json.dumps(tc.arguments, ensure_ascii=False),
                                },
                            }
                            for tc in m.tool_calls
                        ],
                    }
                )
            else:
                out.append({"role": m.role, "content": m.content})
        return out

    def complete(
        self,
        messages: Sequence[Message],
        *,
        system: str | None = None,
        tools: Sequence[ToolSpec] | None = None,
        json_schema: dict[str, Any] | None = None,
        max_tokens: int = 1024,
        temperature: float = 0.2,
    ) -> LLMResponse:
        body: dict[str, Any] = {
            "model": self.model,
            "messages": self._messages(messages, system),
            self._max_tokens_field: max_tokens,
            "temperature": temperature,
        }
        if tools:
            body["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": t.name,
                        "description": t.description,
                        "parameters": t.parameters,
                    },
                }
                for t in tools
            ]
        if json_schema is not None:
            body["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": "response", "schema": json_schema, "strict": True},
            }
        data = self._request("POST", "/chat/completions", body)
        try:
            choice = data["choices"][0]
            msg = choice["message"]
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMError(f"{self.name}: váratlan válaszformátum") from exc

        calls: list[ToolCall] = []
        for raw in msg.get("tool_calls") or []:
            fn = raw.get("function") or {}
            args_text = fn.get("arguments") or "{}"
            try:
                args = json.loads(args_text) if isinstance(args_text, str) else args_text
            except json.JSONDecodeError as exc:
                raise LLMError(f"{self.name}: érvénytelen eszközhívás-argumentum") from exc
            if not isinstance(args, dict):
                raise LLMError(f"{self.name}: az eszközhívás argumentuma nem objektum")
            calls.append(
                ToolCall(id=str(raw.get("id", "")), name=str(fn.get("name")), arguments=args)
            )

        text = msg.get("content")
        parsed: Any | None = None
        if json_schema is not None and text:
            try:
                parsed = json.loads(text)
            except json.JSONDecodeError as exc:
                raise LLMError(f"{self.name}: a válasz nem érvényes JSON") from exc
        usage = data.get("usage") or {}
        finish = choice.get("finish_reason")
        return LLMResponse(
            text=text,
            tool_calls=tuple(calls),
            json=parsed,
            model=str(data.get("model") or self.model),
            usage={
                "input_tokens": int(usage.get("prompt_tokens") or 0),
                "output_tokens": int(usage.get("completion_tokens") or 0),
            },
            stop_reason=_STOP_REASONS.get(finish, finish) if finish else None,
        )

    # --- embeddings ----------------------------------------------------------------------
    def embed(self, texts: Sequence[str]) -> list[list[float]] | None:
        if not self.embedding_model:
            return None
        try:
            r = self._client.post(
                "/embeddings", json={"model": self.embedding_model, "input": list(texts)}
            )
        except httpx.HTTPError as exc:
            raise LLMError(f"{self.name}: kapcsolódási hiba ({type(exc).__name__})") from exc
        if r.status_code == 404:
            return None
        if r.status_code >= 400:
            raise LLMError(f"{self.name}: HTTP {r.status_code} hiba (/embeddings)")
        items = sorted(r.json().get("data", []), key=lambda d: d.get("index", 0))
        return [[float(x) for x in item["embedding"]] for item in items]


class LMStudioProvider(OpenAICompatProvider):
    """Local LM Studio server (no key). Model defaults to the first loaded model."""

    def __init__(self, settings: Settings, *, client: httpx.Client | None = None) -> None:
        if not settings.lm_studio_base_url:
            raise LLMError("Az LM Studio nincs beállítva: add meg az LM_STUDIO_BASE_URL értékét.")
        super().__init__(
            base_url=settings.lm_studio_base_url,
            api_key=None,
            model=settings.llm_model,
            name="lmstudio",
            is_local=True,
            client=client,
            embedding_model=settings.llm_embedding_model,
        )


class OpenAIProvider(OpenAICompatProvider):
    """OpenAI API (separately billed; a ChatGPT subscription is not API access)."""

    def __init__(self, settings: Settings, *, client: httpx.Client | None = None) -> None:
        if not settings.openai_api_key:
            raise LLMError("Az OpenAI provider nincs beállítva: add meg az OPENAI_API_KEY értékét.")
        super().__init__(
            base_url=OPENAI_BASE_URL,
            api_key=settings.openai_api_key,
            model=settings.llm_model or OPENAI_DEFAULT_MODEL,
            name="openai",
            is_local=False,
            client=client,
            embedding_model=settings.llm_embedding_model,
            max_tokens_field="max_completion_tokens",
        )
