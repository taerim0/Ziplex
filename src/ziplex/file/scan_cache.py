"""Cross-process store for scanner.scan_files() results.

One SQLite file (`$ZIPLEX_HOME/scan_cache.db`, default `~/.ziplex/`) mapping
an opaque key string (built by scanner.py: rule version, file kind, content
hash, ...) to a language-neutral JSON result. Contract: never raises -- any
open/read/write failure disables the store for that call and the caller
just scans. WAL + busy timeout make concurrent CLI/MCP/GUI processes safe.
"""

import json
import os
import sqlite3
from pathlib import Path

# Tests (and callers wanting an explicit location) set this; None means
# resolve from the environment on every open.
SCAN_CACHE_PATH: Path | None = None

_MAX_ROWS = 200_000
_QUERY_CHUNK = 500
_BUSY_TIMEOUT_SECONDS = 10


def cache_path() -> Path:
    if SCAN_CACHE_PATH is not None:
        return Path(SCAN_CACHE_PATH)
    home = os.environ.get("ZIPLEX_HOME")
    base = Path(home) if home else Path.home() / ".ziplex"
    return base / "scan_cache.db"


class ScanStore:
    def __init__(self, conn: sqlite3.Connection):
        self._conn = conn

    def get_many(self, keys: list[str]) -> dict:
        """{key: neutral result} for every key found; {} on any failure."""
        found = {}
        if self._conn is None:
            return found
        try:
            for i in range(0, len(keys), _QUERY_CHUNK):
                part = keys[i:i + _QUERY_CHUNK]
                marks = ",".join("?" * len(part))
                for key, value in self._conn.execute(
                    f"SELECT key, result FROM scan WHERE key IN ({marks})", part
                ):
                    found[key] = json.loads(value)
        except (sqlite3.Error, ValueError):
            self.close()
            return {}
        return found

    def put_many(self, items: dict) -> None:
        if self._conn is None or not items:
            return
        try:
            with self._conn:
                self._conn.executemany(
                    "INSERT OR REPLACE INTO scan(key, result) VALUES (?, ?)",
                    [(k, json.dumps(v, ensure_ascii=False)) for k, v in items.items()],
                )
            self._prune()
        except sqlite3.Error:
            self.close()

    def _prune(self) -> None:
        """Drops the oldest rows (by rowid, i.e. insertion order) once the
        table passes _MAX_ROWS, keeping 3/4 of the cap."""
        (count,) = self._conn.execute("SELECT COUNT(*) FROM scan").fetchone()
        if count <= _MAX_ROWS:
            return
        with self._conn:
            self._conn.execute(
                "DELETE FROM scan WHERE rowid IN (SELECT rowid FROM scan ORDER BY rowid LIMIT ?)",
                (count - _MAX_ROWS * 3 // 4,),
            )

    def close(self) -> None:
        if self._conn is not None:
            try:
                self._conn.close()
            except sqlite3.Error:
                pass
            self._conn = None


def _connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path), timeout=_BUSY_TIMEOUT_SECONDS)
    try:
        try:
            conn.execute("PRAGMA journal_mode=WAL")
        except sqlite3.OperationalError:
            pass  # e.g. a filesystem without shared memory; default journal still works
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("CREATE TABLE IF NOT EXISTS scan (key TEXT PRIMARY KEY, result TEXT NOT NULL)")
        conn.execute("SELECT 1 FROM scan LIMIT 1").fetchall()
    except sqlite3.Error:
        conn.close()
        raise
    return conn


def open_store() -> ScanStore | None:
    """None when the store can't be opened at all (the caller scans
    without it). A corrupt file is deleted and recreated once."""
    path = cache_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            return ScanStore(_connect(path))
        except sqlite3.OperationalError:
            return None  # locked/unavailable: not corruption, leave the file alone
        except sqlite3.DatabaseError:
            for suffix in ("", "-wal", "-shm"):
                Path(str(path) + suffix).unlink(missing_ok=True)
            return ScanStore(_connect(path))
    except (sqlite3.Error, OSError):
        return None
