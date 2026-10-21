"""Read and rewrite the `.env` file that holds secrets (docs/api.md M7b).

`update_env` replaces the file atomically (temp file in the same directory + rename,
mode 0600), keeps comments and unrelated lines, and clears the settings cache. Values
are never logged.
"""

from __future__ import annotations

import os
import re
import tempfile
from collections.abc import Mapping
from pathlib import Path

from whattowatch.config import get_settings

_LINE = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=(.*)$")
_NEEDS_QUOTES = re.compile(r"[\s#\"'\\]")


def _unquote(raw: str) -> str:
    value = raw.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        inner = value[1:-1]
        if value[0] == '"':
            inner = inner.replace('\\"', '"').replace("\\\\", "\\")
        return inner
    # Unquoted: an inline comment starts at " #".
    hash_at = value.find(" #")
    return value[:hash_at].rstrip() if hash_at >= 0 else value


def _quote(value: str) -> str:
    if "\n" in value or "\r" in value:
        raise ValueError("an .env value cannot contain a line break")
    if value and not _NEEDS_QUOTES.search(value):
        return value
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def read_env(path: Path) -> dict[str, str]:
    """`KEY -> value` of a dotenv file (missing file -> {}); later lines win."""
    if not path.is_file():
        return {}
    out: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.lstrip().startswith("#"):
            continue
        match = _LINE.match(line)
        if match:
            out[match.group(1)] = _unquote(match.group(2))
    return out


def update_env(path: Path, values: Mapping[str, str | None]) -> None:
    """Set (`str`) or remove (`None`) keys; other lines are kept verbatim."""
    lines = path.read_text(encoding="utf-8").splitlines() if path.is_file() else []
    pending = dict(values)
    out: list[str] = []
    for line in lines:
        match = None if line.lstrip().startswith("#") else _LINE.match(line)
        if match is None or match.group(1) not in values:
            out.append(line)
            continue
        key = match.group(1)
        if key not in pending:  # duplicate line of a key already written: drop it
            continue
        value = pending.pop(key)
        if value is not None:
            out.append(f"{key}={_quote(value)}")
    for key, value in pending.items():
        if value is not None:
            out.append(f"{key}={_quote(value)}")
    text = "\n".join(out) + ("\n" if out else "")

    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".env.", suffix=".tmp", dir=path.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    os.chmod(path, 0o600)
    get_settings.cache_clear()
