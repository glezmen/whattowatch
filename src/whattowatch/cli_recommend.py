"""`wtw recommend ...` commands: generate and show recommendations."""

from __future__ import annotations

from typing import Annotated

import typer

from whattowatch.cli_users import UserOption, lookup_user_id

app = typer.Typer(help="Recommendations", no_args_is_help=True)


@app.command("run")
def recommend_run(
    limit: Annotated[int, typer.Option(help="Number of recommendations")] = 20,
    media_type: Annotated[str | None, typer.Option(help="movie | tv")] = None,
    user: UserOption = None,
) -> None:
    """Generate a recommendation batch for a profile and print it."""
    from whattowatch.services.recommend.engine import run_recommendations

    run_recommendations(user_id=lookup_user_id(user), limit=limit, media_type=media_type)


@app.command("extract-preferences")
def extract_preferences(
    extractor: Annotated[str, typer.Option(help="rules | llm")] = "rules",
    provider: Annotated[
        str | None, typer.Option(help="LLM provider (lmstudio | anthropic | openai | claude_code)")
    ] = None,
    user: UserOption = None,
) -> None:
    """Re-run preference signal extraction over a profile's text feedback."""
    from whattowatch.config import get_settings
    from whattowatch.llm.base import LLMError
    from whattowatch.preferences.extract import run_extraction

    if extractor not in ("rules", "llm"):
        raise typer.BadParameter("rules | llm", param_hint="--extractor")
    settings = get_settings()
    uid = lookup_user_id(user)
    llm = None
    if extractor == "llm":
        from whattowatch.llm.registry import get_provider, load_llm_config

        try:
            llm = get_provider(settings, provider, config=load_llm_config(settings, user))
        except LLMError as exc:
            typer.echo(str(exc), err=True)
            raise typer.Exit(1) from exc
    run_extraction(user_id=uid, extractor=extractor, provider=llm, settings=settings)
