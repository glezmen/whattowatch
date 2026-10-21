from pathlib import Path

import typer
import uvicorn

from whattowatch.cli_backup import app as backup_app
from whattowatch.cli_catalog import app as catalog_app
from whattowatch.cli_import import app as import_app
from whattowatch.cli_llm import app as llm_app
from whattowatch.cli_recommend import app as recommend_app
from whattowatch.cli_refresh import app as refresh_app
from whattowatch.cli_resolve import resolve_app
from whattowatch.cli_settings import app as settings_app
from whattowatch.cli_tmdb import tmdb_app
from whattowatch.cli_users import app as users_app
from whattowatch.config import Settings
from whattowatch.db.migrate import upgrade_to_head

app = typer.Typer(help="whattowatch CLI", no_args_is_help=True)
db_app = typer.Typer(help="Database commands", no_args_is_help=True)
app.add_typer(db_app, name="db")
app.add_typer(tmdb_app, name="tmdb")
app.add_typer(import_app, name="import")
app.add_typer(catalog_app, name="catalog")
app.add_typer(recommend_app, name="recommend")
app.add_typer(resolve_app, name="resolve")
app.add_typer(llm_app, name="llm")
app.add_typer(refresh_app, name="refresh")
app.add_typer(settings_app, name="settings")
app.add_typer(backup_app, name="backup")
app.add_typer(users_app, name="users")

ROOT = Path(__file__).resolve().parents[2]


@db_app.command("upgrade")
def db_upgrade() -> None:
    """Apply all Alembic migrations (upgrade head)."""
    upgrade_to_head(Settings())


@app.command()
def serve() -> None:
    """Run the web app on 127.0.0.1:8000."""
    uvicorn.run("whattowatch.api.app:create_app", factory=True, host="127.0.0.1", port=8000)
