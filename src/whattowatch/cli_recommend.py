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
def extract_preferences() -> None:
    """Re-run preference signal extraction over all text feedback."""
    from whattowatch.preferences.extract import run_extraction

    run_extraction()
