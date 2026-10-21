"""import_uploads (household export library), sync_runs.upload_id / profile_name

Revision ID: 0014
Revises: 0013

Data: every file already stored under `Settings.imports_dir` (`<source>/<utc>_<name>`)
gets a row, uploaded by the admin (else the first profile); the upload time comes from
the timestamp prefix (else the file's mtime); the export profiles are parsed lazily on
the first listing. Existing import runs are linked to an upload by the whole-file
SHA-256 they recorded. Downgrade drops the table and the columns (stored files stay).
"""

import hashlib
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import sqlalchemy as sa
from alembic import context, op

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None

# Keep in sync with services/import_jobs.py IMPORT_SOURCES.
SOURCES = (
    "imdb_ratings",
    "imdb_watchlist",
    "netflix_export",
    "prime_video_export",
    "disney_plus_export",
    "hbo_max_export",
    "apple_tv_export",
    "skyshowtime_export",
)
STAMP_FORMAT = "%Y%m%dT%H%M%S%fZ"


def _set_foreign_keys(enabled: bool) -> None:
    # Batch mode recreates sync_runs; see 0013 for why foreign keys go off first.
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        bind.exec_driver_sql(f"PRAGMA foreign_keys={'ON' if enabled else 'OFF'}")


def _imports_dir() -> Path:
    from whattowatch.config import Settings

    settings = context.config.attributes.get("settings") or Settings()
    return Path(settings.imports_dir)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _split_name(path: Path) -> tuple[str, datetime]:
    stamp, sep, rest = path.name.partition("_")
    if sep and rest:
        try:
            return rest, datetime.strptime(stamp, STAMP_FORMAT).replace(tzinfo=UTC)
        except ValueError:
            pass
    return path.name, datetime.fromtimestamp(path.stat().st_mtime, UTC)


def _stored_files(root: Path) -> list[tuple[str, Path]]:
    if not root.is_dir():
        return []
    out: list[tuple[str, Path]] = []
    for source in SOURCES:
        folder = root / source
        if folder.is_dir():
            out.extend((source, p) for p in sorted(folder.iterdir()) if p.is_file())
    return out


def _backfill(bind: sa.Connection) -> None:
    owner = bind.execute(
        sa.text("SELECT id FROM users ORDER BY is_admin DESC, id LIMIT 1")
    ).scalar()
    seen: set[tuple[str, str]] = set()
    for source, path in _stored_files(_imports_dir()):
        try:
            sha = _sha256(path)
            file_name, uploaded_at = _split_name(path)
            size = path.stat().st_size
        except OSError:
            continue
        if (source, sha) in seen:  # the same file uploaded twice: keep the first copy
            continue
        seen.add((source, sha))
        row: dict[str, Any] = {
            "source": source,
            "file_name": file_name,
            "stored_path": str(path),
            "size": size,
            "sha": sha,
            "owner": owner,
            "at": uploaded_at,
        }
        bind.execute(
            sa.text(
                "INSERT INTO import_uploads (source, file_name, stored_path, size_bytes,"
                " sha256, uploaded_by, uploaded_at, profiles)"
                " VALUES (:source, :file_name, :stored_path, :size, :sha, :owner, :at, NULL)"
            ),
            row,
        )
    bind.execute(
        sa.text(
            "UPDATE sync_runs SET upload_id = (SELECT u.id FROM import_uploads u"
            "  WHERE u.sha256 = sync_runs.file_sha256 ORDER BY u.id LIMIT 1)"
            " WHERE user_id IS NOT NULL AND file_sha256 IS NOT NULL"
        )
    )


def upgrade() -> None:
    _set_foreign_keys(False)
    op.create_table(
        "import_uploads",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("source", sa.String(), nullable=False),
        sa.Column("file_name", sa.String(), nullable=False),
        sa.Column("stored_path", sa.String(), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("sha256", sa.String(), nullable=False),
        sa.Column("uploaded_by", sa.Integer(), nullable=True),
        sa.Column("uploaded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("profiles", sa.JSON(), nullable=True),
        sa.ForeignKeyConstraint(
            ["uploaded_by"],
            ["users.id"],
            name="fk_import_uploads_uploaded_by",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("source", "sha256", name="uq_import_uploads_source_sha256"),
    )
    op.create_index("ix_import_uploads_uploaded_by", "import_uploads", ["uploaded_by"])
    with op.batch_alter_table("sync_runs", recreate="always") as batch:
        batch.add_column(sa.Column("upload_id", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("profile_name", sa.String(), nullable=True))
        batch.create_foreign_key(
            "fk_sync_runs_upload_id",
            "import_uploads",
            ["upload_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch.create_index("ix_sync_runs_upload_id", ["upload_id"])
    _backfill(op.get_bind())
    _set_foreign_keys(True)


def downgrade() -> None:
    _set_foreign_keys(False)
    with op.batch_alter_table("sync_runs", recreate="always") as batch:
        batch.drop_index("ix_sync_runs_upload_id")
        batch.drop_constraint("fk_sync_runs_upload_id", type_="foreignkey")
        batch.drop_column("profile_name")
        batch.drop_column("upload_id")
    op.drop_index("ix_import_uploads_uploaded_by", table_name="import_uploads")
    op.drop_table("import_uploads")
    _set_foreign_keys(True)
