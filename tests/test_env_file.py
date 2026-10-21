"""services/env_file.py: atomic .env rewrite (docs/api.md M7b)."""

from __future__ import annotations

import stat
from pathlib import Path

import pytest

from whattowatch.services.env_file import read_env, update_env


def test_roundtrip_preserves_comments_and_unrelated_lines(tmp_path: Path) -> None:
    path = tmp_path / ".env"
    path.write_text("# header\nDATABASE_URL=sqlite:///x.db\n\nTMDB_API_KEY=old\nOTHER=1\n")
    update_env(path, {"TMDB_API_KEY": "new-value", "OPENAI_API_KEY": "sk-1"})
    text = path.read_text()
    assert text.startswith("# header\nDATABASE_URL=sqlite:///x.db\n\nTMDB_API_KEY=new-value\n")
    assert read_env(path) == {
        "DATABASE_URL": "sqlite:///x.db",
        "TMDB_API_KEY": "new-value",
        "OTHER": "1",
        "OPENAI_API_KEY": "sk-1",
    }


def test_none_removes_key_and_duplicates(tmp_path: Path) -> None:
    path = tmp_path / ".env"
    path.write_text("A=1\nB=2\nA=3\n# A=commented\n")
    update_env(path, {"A": None, "MISSING": None})
    assert path.read_text() == "B=2\n# A=commented\n"


def test_quoting(tmp_path: Path) -> None:
    path = tmp_path / ".env"
    update_env(path, {"A": "has space", "B": "x#y", "C": 'q"uote', "D": "plain"})
    text = path.read_text()
    assert 'A="has space"' in text and 'B="x#y"' in text and "D=plain" in text
    assert read_env(path) == {"A": "has space", "B": "x#y", "C": 'q"uote', "D": "plain"}


def test_file_mode_is_0600(tmp_path: Path) -> None:
    path = tmp_path / ".env"
    path.write_text("A=1\n")
    path.chmod(0o644)
    update_env(path, {"B": "2"})
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert not [p for p in tmp_path.iterdir() if p.name != ".env"]  # no temp left over


def test_wtw_env_file_overrides_location(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from whattowatch.config import Settings, env_file_path

    custom = tmp_path / "data" / ".env"
    custom.parent.mkdir()
    custom.write_text("TMDB_API_KEY=from-custom\n")
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("TMDB_API_KEY", raising=False)

    monkeypatch.delenv("WTW_ENV_FILE", raising=False)
    assert env_file_path() == (tmp_path / ".env").resolve()
    assert Settings().tmdb_api_key is None

    monkeypatch.setenv("WTW_ENV_FILE", str(custom))
    assert env_file_path() == custom.resolve()
    assert Settings().tmdb_api_key == "from-custom"
    # An explicit `_env_file` still wins (tests rely on `_env_file=None`).
    assert Settings(_env_file=None).tmdb_api_key is None  # type: ignore[call-arg]
