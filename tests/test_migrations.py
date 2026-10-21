from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect

from whattowatch.cli import db_upgrade


def test_upgrade_head_creates_version_table(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_file = tmp_path / "nested" / "test.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_file}")
    db_upgrade()
    assert "alembic_version" in inspect(create_engine(f"sqlite:///{db_file}")).get_table_names()
