"""Migration 0014: stored export files are backfilled into `import_uploads`."""

from __future__ import annotations

import hashlib
import io
import zipfile
from datetime import UTC, datetime
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from whattowatch.cli import ROOT
from whattowatch.db.models import ImportUpload
from whattowatch.services import import_uploads


def _config() -> Config:
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "src/whattowatch/db/migrations"))
    return cfg


def _netflix_zip() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(
            "netflix-report/CONTENT_INTERACTION/ViewingActivity.csv",
            "Profile Name,Start Time,Duration,Attributes,Title\n"
            "Anna,2024-01-01 20:00:00,01:50:00,,Inception\n",
        )
        zf.writestr("netflix-report/PROFILES/Profiles.csv", "Profile Name,Primary Lang\nAnna,hu\n")
    return buf.getvalue()


def test_0014_backfills_stored_files(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db_file = tmp_path / "test.db"
    imports = tmp_path / "imports"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_file}")
    monkeypatch.setenv("IMPORTS_DIR", str(imports))
    cfg = _config()
    command.upgrade(cfg, "0013")

    csv_bytes = b"Const,Your Rating\ntt1,8\n"
    (imports / "imdb_ratings").mkdir(parents=True)
    (imports / "imdb_ratings" / "20261001T101500123456Z_ratings.csv").write_bytes(csv_bytes)
    # the same file uploaded twice: one row
    (imports / "imdb_ratings" / "20261002T101500123456Z_ratings.csv").write_bytes(csv_bytes)
    (imports / "netflix_export").mkdir()
    (imports / "netflix_export" / "export.zip").write_bytes(_netflix_zip())
    (imports / "unknown_source").mkdir()
    (imports / "unknown_source" / "x.csv").write_bytes(b"x")

    engine = create_engine(f"sqlite:///{db_file}")
    sha = hashlib.sha256(csv_bytes).hexdigest()
    with engine.begin() as conn:
        # 0013 creates no profile on a fresh database: seed the admin and a member here.
        conn.execute(
            text("INSERT INTO users (name, avatar_color, is_admin) VALUES ('A', '#000', 1)")
        )
        conn.execute(
            text("INSERT INTO users (name, avatar_color, is_admin) VALUES ('B', '#000', 0)")
        )
        admin = conn.execute(text("SELECT id FROM users WHERE is_admin = 1")).scalar_one()
        conn.execute(
            text(
                "INSERT INTO sync_runs (user_id, source, started_at, status, processed, created,"
                " updated, skipped, errors, file_sha256)"
                " VALUES (:uid, 'imdb_ratings', '2026-10-01', 'done', 1, 1, 0, 0, '[]', :sha)"
            ),
            {"uid": admin, "sha": sha},
        )

    command.upgrade(cfg, "head")
    with Session(engine) as session:
        rows = session.query(ImportUpload).order_by(ImportUpload.id).all()
        assert [(r.source, r.file_name) for r in rows] == [
            ("imdb_ratings", "ratings.csv"),
            ("netflix_export", "export.zip"),
        ]
        imdb, netflix = rows
        assert imdb.uploaded_by == admin and imdb.sha256 == sha
        assert imdb.uploaded_at.replace(tzinfo=UTC) == datetime(
            2026, 10, 1, 10, 15, 0, 123456, tzinfo=UTC
        )
        assert imdb.size_bytes == len(csv_bytes) and imdb.profiles is None
        assert netflix.profiles is None  # parsed lazily
        views = {v.upload.source: v for v in import_uploads.list_uploads(session)}
        assert netflix.profiles == [{"name": "Anna", "primary_lang": "hu", "rows": 1}]
        assert [r.user_id for r in views["imdb_ratings"].runs] == [admin]

    command.downgrade(cfg, "0013")
    with engine.connect() as conn:
        tables = {r[0] for r in conn.execute(text("SELECT name FROM sqlite_master"))}
        assert "import_uploads" not in tables
    engine.dispose()
