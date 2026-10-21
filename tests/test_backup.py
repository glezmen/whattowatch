"""Database backup / restore: service, CLI-free round trips and the /api/backup endpoints."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from alembic import command
from fastapi.testclient import TestClient
from sqlalchemy import Engine, func, select, text
from sqlalchemy.orm import Session

from whattowatch.config import Settings
from whattowatch.db.engine import create_engine_from_settings
from whattowatch.db.migrate import alembic_config, head_revision, upgrade_to_head
from whattowatch.db.models import Title
from whattowatch.services.backup import (
    BackupError,
    export_database,
    inspect_backup,
    list_backups,
    restore_database,
)


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        _env_file=None,  # type: ignore[call-arg]
        database_url=f"sqlite:///{tmp_path / 'db' / 'app.db'}",
        backups_dir=str(tmp_path / "backups"),
        frontend_dist=str(tmp_path / "no-dist"),
    )


def _add_title(engine: Engine, tmdb_id: int, name: str) -> None:
    with Session(engine) as s:
        s.add(
            Title(
                media_type="movie",
                tmdb_id=tmdb_id,
                original_title=name,
                original_language="en",
                title_hu=name,
                genres=[],
                fetched_at=datetime(2026, 10, 1, tzinfo=UTC),
            )
        )
        s.commit()


def _titles(engine: Engine) -> set[str]:
    with Session(engine) as s:
        return set(s.scalars(select(Title.original_title)))


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    s = _settings(tmp_path)
    upgrade_to_head(s)
    return s


@pytest.fixture
def engine(settings: Settings) -> Iterator[Engine]:
    eng = create_engine_from_settings(settings)
    yield eng
    eng.dispose()


def test_export_while_engine_open(engine: Engine, settings: Settings, tmp_path: Path) -> None:
    _add_title(engine, 1, "Alpha")
    with Session(engine) as open_session:
        assert open_session.scalar(select(func.count()).select_from(Title)) == 1
        path = export_database(engine, Path(settings.backups_dir))
    assert path.name.startswith("whattowatch-backup-") and path.name.endswith("Z.db")
    info = inspect_backup(path)
    assert info.alembic_revision == head_revision()
    assert info.titles == 1 and info.watch_events == 0 and info.tables > 5
    assert info.size_bytes == path.stat().st_size
    second = export_database(engine, Path(settings.backups_dir))
    assert second != path and second.exists()  # same second: no overwrite
    assert [b.name for b in list_backups(settings)][:2] in (
        [second.name, path.name],
        [path.name, second.name],
    )
    explicit = export_database(engine, tmp_path / "out" / "mine.db")
    assert explicit == tmp_path / "out" / "mine.db" and inspect_backup(explicit).titles == 1


def test_inspect_rejects_random_and_non_alembic(tmp_path: Path) -> None:
    junk = tmp_path / "junk.db"
    junk.write_bytes(b"not a database at all" * 100)
    with pytest.raises(BackupError, match="SQLite"):
        inspect_backup(junk)
    plain = tmp_path / "plain.db"
    conn = sqlite3.connect(plain)
    conn.execute("CREATE TABLE t (x)")
    conn.commit()
    conn.close()
    with pytest.raises(BackupError, match="alembic_version"):
        inspect_backup(plain)
    with pytest.raises(BackupError):
        inspect_backup(tmp_path / "missing.db")


def test_restore_round_trip(engine: Engine, settings: Settings) -> None:
    _add_title(engine, 1, "Alpha")
    backup = export_database(engine, Path(settings.backups_dir))
    with Session(engine) as s:
        s.execute(text("DELETE FROM titles"))
        s.commit()
    _add_title(engine, 2, "Beta")
    result = restore_database(engine, backup, settings=settings)
    assert _titles(engine) == {"Alpha"}
    assert result.previous_backup is not None and result.previous_backup.exists()
    assert inspect_backup(result.previous_backup).titles == 1  # the Beta-only state
    assert result.revision_before == result.revision_after == head_revision()
    assert not Path(f"{settings.database_url.removeprefix('sqlite:///')}-wal").exists()


def test_restore_upgrades_older_revision(
    tmp_path: Path, engine: Engine, settings: Settings
) -> None:
    old_settings = _settings(tmp_path / "old")
    upgrade_to_head(old_settings)
    old_engine = create_engine_from_settings(old_settings)
    _add_title(old_engine, 7, "Old one")
    old_engine.dispose()
    command.downgrade(alembic_config(old_settings), "-1")
    old_engine = create_engine_from_settings(old_settings)
    backup = export_database(old_engine, tmp_path / "old-backup")
    old_engine.dispose()
    info = inspect_backup(backup)
    assert info.alembic_revision != head_revision()

    result = restore_database(engine, backup, settings=settings)
    assert result.revision_before == info.alembic_revision
    assert result.revision_after == head_revision()
    assert _titles(engine) == {"Old one"}
    with Session(engine) as s:  # providers re-seeded with the head schema
        assert s.execute(text("SELECT count(*) FROM providers")).scalar() == 8


def test_restore_rejects_invalid_file(engine: Engine, settings: Settings, tmp_path: Path) -> None:
    junk = tmp_path / "junk.db"
    junk.write_bytes(b"x" * 200)
    with pytest.raises(BackupError):
        restore_database(engine, junk, settings=settings)
    assert list_backups(settings) == []  # nothing touched before validation


# --- API -------------------------------------------------------------------------------


@pytest.fixture
def client(settings: Settings) -> Iterator[TestClient]:
    from conftest import login_as

    from whattowatch.api.app import create_app
    from whattowatch.services.users import default_user_id

    engine = create_engine_from_settings(settings)
    with Session(engine) as s:
        admin = default_user_id(s)
        s.commit()
    engine.dispose()
    with TestClient(create_app(settings)) as c:
        login_as(c, admin)
        yield c


def test_api_export_returns_attachment(client: TestClient, settings: Settings) -> None:
    r = client.get("/api/backup/export")
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/vnd.sqlite3"
    disposition = r.headers["content-disposition"]
    assert disposition.startswith("attachment;") and "whattowatch-backup-" in disposition
    assert r.content.startswith(b"SQLite format 3\x00")
    listed = client.get("/api/backup/list").json()
    assert len(listed["items"]) == 1 and listed["items"][0]["size_bytes"] == len(r.content)
    assert ".env" in listed["note"]


def test_api_inspect(client: TestClient) -> None:
    content = client.get("/api/backup/export").content
    r = client.post("/api/backup/inspect", files={"file": ("b.db", content)})
    assert r.status_code == 200 and r.json()["alembic_revision"] == head_revision()
    bad = client.post("/api/backup/inspect", files={"file": ("b.db", b"nope" * 50)})
    assert bad.status_code == 422 and bad.json()["detail_code"] == "invalid_backup"


def test_api_restore(client: TestClient, settings: Settings) -> None:
    engine = create_engine_from_settings(settings)
    _add_title(engine, 1, "Alpha")
    content = client.get("/api/backup/export").content
    with Session(engine) as s:
        s.execute(text("DELETE FROM titles"))
        s.commit()
    engine.dispose()
    assert client.get("/api/titles", params={"include_unverified": True}).json()["total"] == 0

    no_confirm = client.post("/api/backup/restore", files={"file": ("b.db", content)})
    assert no_confirm.status_code == 422
    wrong = client.post(
        "/api/backup/restore", files={"file": ("b.db", content)}, data={"confirm": "yes"}
    )
    assert wrong.status_code == 422 and wrong.json()["detail_code"] == "confirm_required"
    invalid = client.post(
        "/api/backup/restore", files={"file": ("b.db", b"junk" * 64)}, data={"confirm": "RESTORE"}
    )
    assert invalid.status_code == 422 and invalid.json()["detail_code"] == "invalid_backup"

    r = client.post(
        "/api/backup/restore", files={"file": ("b.db", content)}, data={"confirm": "RESTORE"}
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["revision_after"] == head_revision() and body["restored"]["titles"] == 1
    assert body["previous_backup"] and Path(body["previous_backup"]).exists()
    assert "data/imports" in body["note"]
    titles = client.get("/api/titles", params={"include_unverified": True}).json()
    assert titles["total"] == 1
