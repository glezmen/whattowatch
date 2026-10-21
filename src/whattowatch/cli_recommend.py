"""`wtw recommend ...` commands: generate and show recommendations."""

from __future__ import annotations

from typing import Annotated

import typer

app = typer.Typer(help="Recommendations", no_args_is_help=True)


@app.command("run")
def recommend_run(
    limit: Annotated[int, typer.Option(help="Number of recommendations")] = 20,
    media_type: Annotated[str | None, typer.Option(help="movie | tv")] = None,
) -> None:
    """Generate a recommendation batch and print it."""
    from whattowatch.services.recommend.engine import run_recommendations

    run_recommendations(limit=limit, media_type=media_type)


@app.command("extract-preferences")
def extract_preferences(
    extractor: Annotated[str, typer.Option(help="rules | llm")] = "rules",
    provider: Annotated[
        str | None, typer.Option(help="LLM provider (lmstudio | anthropic | openai)")
    ] = None,
) -> None:
    """Re-run preference signal extraction over all text feedback."""
    from whattowatch.config import get_settings
    from whattowatch.llm.base import LLMError
    from whattowatch.preferences.extract import run_extraction

    if extractor not in ("rules", "llm"):
        raise typer.BadParameter("rules | llm", param_hint="--extractor")
    settings = get_settings()
    llm = None
    if extractor == "llm":
        from whattowatch.llm.registry import get_provider

        try:
            llm = get_provider(settings, provider)
        except LLMError as exc:
            typer.echo(str(exc), err=True)
            raise typer.Exit(1) from exc
    run_extraction(extractor=extractor, provider=llm, settings=settings)
