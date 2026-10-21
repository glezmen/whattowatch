from pathlib import Path

import typer
import uvicorn
from alembic import command
from alembic.config import Config

app = typer.Typer(help="whattowatch CLI", no_args_is_help=True)
db_app = typer.Typer(help="Database commands", no_args_is_help=True)
app.add_typer(db_app, name="db")

ROOT = Path(__file__).resolve().parents[2]


@db_app.command("upgrade")
def db_upgrade() -> None:
    """Apply all Alembic migrations (upgrade head)."""
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "src/whattowatch/db/migrations"))
    command.upgrade(cfg, "head")


@app.command()
def serve() -> None:
    """Run the web app on 127.0.0.1:8000."""
    uvicorn.run("whattowatch.api.app:create_app", factory=True, host="127.0.0.1", port=8000)
