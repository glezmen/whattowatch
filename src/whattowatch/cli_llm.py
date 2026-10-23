"""`wtw llm ...` commands: check the configured LLM provider."""

from __future__ import annotations

from typing import Annotated

import typer

from whattowatch.cli_users import UserOption

app = typer.Typer(help="LLM provider commands", no_args_is_help=True)

TEST_PROMPT = "Válaszolj egyetlen rövid magyar mondatban: mi a kedvenc filmműfajod?"


@app.command("test")
def llm_test(
    provider: Annotated[
        str | None,
        typer.Option(
            help="lmstudio | anthropic | openai | claude_code | copilot "
            "(default: the UI choice, else LLM_PROVIDER)"
        ),
    ] = None,
    user: UserOption = None,
) -> None:
    """Send a one-line Hungarian prompt and print model, reply and token usage."""
    from whattowatch.config import get_settings
    from whattowatch.llm.base import LLMError, Message
    from whattowatch.llm.registry import get_provider, load_llm_config

    settings = get_settings()
    try:
        llm = get_provider(settings, provider, config=load_llm_config(settings, user))
        resp = llm.complete([Message(role="user", content=TEST_PROMPT)], max_tokens=200)
    except LLMError as exc:
        typer.echo(f"Hiba: {exc}", err=True)
        raise typer.Exit(1) from exc
    where = "helyi" if llm.is_local else "felhős"
    typer.echo(f"Provider: {llm.name} ({where}), modell: {resp.model}")
    typer.echo(f"Válasz: {(resp.text or '').strip()}")
    typer.echo(
        f"Tokenek: be {resp.usage.get('input_tokens', 0)}, "
        f"ki {resp.usage.get('output_tokens', 0)} (stop: {resp.stop_reason})"
    )


@app.command("models")
def llm_models() -> None:
    """List the models the LM Studio server offers."""
    from whattowatch.config import get_settings
    from whattowatch.llm.base import LLMError
    from whattowatch.llm.openai_compat import LMStudioProvider

    try:
        models = LMStudioProvider(get_settings()).list_models()
    except LLMError as exc:
        typer.echo(f"Hiba: {exc}", err=True)
        raise typer.Exit(1) from exc
    if not models:
        typer.echo("Az LM Studio nem listázott modellt.")
    for name in models:
        typer.echo(name)
