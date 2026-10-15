"""Persistent, incremental dependency-graph index (`<project>/.ziplex/index.db`).

A disposable cache: delete it any time, `update()` rebuilds it. Stores per
file its stat/hash/kind, raw imports with line numbers, and resolved edges.

Invalidation is deliberately coarse (docs/REVIEW-2026-10-14.md §5.3):
- a changed file is re-parsed and its own imports re-resolved;
- when the file *set* changes (add/remove), a resolver context file changes
  (tsconfig, package.json, go.mod, ...) or the scope/version changes, every
  stored raw import is re-resolved and every text file's mentions re-matched.
  No re-parse is needed for that, and on django it costs ~0.2s + ~1.6s --
  cheap enough that fine-grained dependency tracking isn't worth its bugs.
"""
import hashlib
import json
import os
import posixpath
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path

from .. import __version__
from ..config import collection_kwargs, load_config
from ..extract.code.extractor import extract_imports
from ..extract.code.parser import get_parser
from ..file.collector import walk_candidates
from ..file.media import is_media_file
from ..file.relationship import build_stem_map, resolve_dependency
from ..file.scanner import scan_files
from ..file.textutil import cached_reads, relative_key
from .graph import GraphBuilder
from .testmap import is_test_path

SCHEMA_VERSION = 2
INDEX_DIR = ".ziplex"
INDEX_FILE = "index.db"

_CONTEXT_NAMES = {"package.json", "pnpm-workspace.yaml", "go.mod", "jsconfig.json", ".ziplex.json"}
GRAPH_KINDS = ("code", "text", "media")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS files (
    id INTEGER PRIMARY KEY,
    path TEXT NOT NULL UNIQUE,
    size INTEGER NOT NULL,
    mtime_ns INTEGER NOT NULL,
    hash TEXT NOT NULL,
    kind TEXT NOT NULL,            -- code | text | media | binary | excluded
    is_test INTEGER NOT NULL DEFAULT 0,
    flagged INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS imports (
    file_id INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
    raw TEXT NOT NULL,
    line INTEGER NOT NULL,
    ctx TEXT NOT NULL DEFAULT 'module'   -- module | function | typing | main (extractor._import_context)
);
CREATE INDEX IF NOT EXISTS imports_file ON imports(file_id);
CREATE TABLE IF NOT EXISTS edges (
    src_id INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
    dst_id INTEGER REFERENCES files(id) ON DELETE CASCADE,
    external TEXT,
    kind TEXT NOT NULL,            -- import | text
    line INTEGER,
    ctx TEXT
);
CREATE INDEX IF NOT EXISTS edges_src ON edges(src_id);
CREATE INDEX IF NOT EXISTS edges_dst ON edges(dst_id);
"""


def index_path(project_path: str) -> Path:
    return Path(project_path) / INDEX_DIR / INDEX_FILE


def _is_context_file(path: str) -> bool:
    name = posixpath.basename(path)
    return name in _CONTEXT_NAMES or (name.startswith("tsconfig") and name.endswith(".json"))


def _ensure_gitignore(index_dir: Path) -> None:
    ignore = index_dir / ".gitignore"
    line = "index.db*"
    try:
        existing = ignore.read_text(encoding="utf-8") if ignore.exists() else ""
        if line not in existing.split():
            ignore.write_text(existing + ("" if not existing or existing.endswith("\n") else "\n") + line + "\n", encoding="utf-8")
    except OSError:
        pass


def _classify(abs_path: str) -> tuple[str, str]:
    """(kind, content hash) from one read of the file's bytes."""
    with open(abs_path, "rb") as f:
        data = f.read()
    digest = hashlib.blake2b(data, digest_size=16).hexdigest()
    try:
        data.decode("utf-8")
    except UnicodeDecodeError:
        # Same rule as file/media.classify_media_file(): media only when the
        # extension says so AND the content isn't text (a git symlink stub
        # named .png is text).
        return ("media" if is_media_file(abs_path) is not None else "binary"), digest
    return ("code" if get_parser(abs_path) else "text"), digest


class GraphIndex:
    def __init__(self, project_path: str, db_path: str | None = None):
        self.root = str(Path(project_path).resolve())
        path = Path(db_path) if db_path else index_path(self.root)
        path.parent.mkdir(parents=True, exist_ok=True)
        if db_path is None:
            _ensure_gitignore(path.parent)
        self.db_path = path
        self.conn = self._connect()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=30, isolation_level=None, check_same_thread=False)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.executescript(_SCHEMA)
        if self.meta("schema_version", conn) not in (None, str(SCHEMA_VERSION)):
            conn.close()
            self.db_path.unlink(missing_ok=True)
            return self._connect()
        return conn

    def meta(self, key: str, conn: sqlite3.Connection | None = None) -> str | None:
        row = (conn or self.conn).execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return row[0] if row else None

    def close(self) -> None:
        self.conn.close()

    @contextmanager
    def _write(self):
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            yield
            self.conn.execute("COMMIT")
        except BaseException:
            self.conn.execute("ROLLBACK")
            raise

    def _abs(self, rel: str) -> str:
        return str(Path(self.root) / rel)

    @cached_reads
    def update(self, paths: list[str] | None = None) -> dict:
        """Brings the index in line with disk. `paths` limits the check to
        those files (an editor hook); a path the index doesn't know yet
        falls back to a full walk, since its scope can't be judged alone.
        Returns counts of what changed."""
        config = load_config(self.root)
        scope = collection_kwargs(self.root)
        config_fp = json.dumps([scope, bool(config.get("exclude_flagged_from_graph"))], sort_keys=True)

        started_ns = time.time_ns()
        with self._write():
            # A file written within this window of the last update may share
            # its recorded (size, mtime) despite new content -- re-hash it.
            racy_after = int(self.meta("updated_ns") or 0) - 2_000_000_000
            known = {
                row[0]: row[1:]
                for row in self.conn.execute("SELECT path, id, size, mtime_ns, hash, kind FROM files")
            }
            scope_changed = self.meta("config_fp") != config_fp
            if paths is not None and not scope_changed:
                rels = [relative_key(os.path.abspath(p), self.root) for p in paths]
                if all(r in known or not os.path.exists(self._abs(r)) for r in rels):
                    candidates = set(known) - {r for r in rels if not os.path.exists(self._abs(r))}
                    to_check = [r for r in rels if r in candidates]
                else:
                    paths = None
            if paths is None or scope_changed:
                candidates = {relative_key(fp, self.root) for fp in walk_candidates(self.root, **scope)}
                to_check = sorted(candidates)

            removed = sorted(set(known) - candidates)
            added, changed = [], []
            stat_only = []
            for rel in to_check:
                try:
                    st = os.stat(self._abs(rel))
                except OSError:  # vanished, or a dangling symlink
                    if rel in known and rel not in removed:
                        removed.append(rel)
                    continue
                old = known.get(rel)
                if old and old[1] == st.st_size and old[2] == st.st_mtime_ns and st.st_mtime_ns < racy_after:
                    continue
                kind, digest = _classify(self._abs(rel))
                if old and old[3] == digest:
                    stat_only.append((st.st_size, st.st_mtime_ns, rel))
                    continue
                (changed if old else added).append((rel, kind, digest, st.st_size, st.st_mtime_ns))

            fresh = added + changed
            scannable = [self._abs(r) for r, kind, *_ in fresh if kind in ("code", "text")]
            flagged = {
                relative_key(d["file"], self.root) for d in scan_files(scannable, root=self.root)["dangerous"]
            } if scannable else set()
            exclude_flagged = bool(config.get("exclude_flagged_from_graph"))

            for rel in removed:
                self.conn.execute("DELETE FROM files WHERE path = ?", (rel,))
            self.conn.executemany("UPDATE files SET size = ?, mtime_ns = ? WHERE path = ?", stat_only)
            for rel, kind, digest, size, mtime in fresh:
                is_flagged = rel in flagged
                if is_flagged and exclude_flagged:
                    kind = "excluded"
                self.conn.execute(
                    "INSERT INTO files (path, size, mtime_ns, hash, kind, is_test, flagged) VALUES (?, ?, ?, ?, ?, ?, ?) "
                    "ON CONFLICT(path) DO UPDATE SET size=excluded.size, mtime_ns=excluded.mtime_ns, "
                    "hash=excluded.hash, kind=excluded.kind, is_test=excluded.is_test, flagged=excluded.flagged",
                    (rel, size, mtime, digest, kind, int(is_test_path(rel)), int(is_flagged)),
                )
                file_id = self.conn.execute("SELECT id FROM files WHERE path = ?", (rel,)).fetchone()[0]
                self.conn.execute("DELETE FROM imports WHERE file_id = ?", (file_id,))
                if kind == "code":
                    self.conn.executemany(
                        "INSERT INTO imports (file_id, raw, line, ctx) VALUES (?, ?, ?, ?)",
                        [(file_id, raw, line, ctx) for raw, line, ctx in extract_imports(self._abs(rel))],
                    )

            fresh_paths = {r for r, *_ in fresh}
            context_changed = any(_is_context_file(p) for p in fresh_paths | set(removed))
            full = bool(
                added or removed or context_changed or scope_changed
                or self.meta("resolver_version") != __version__
            )
            self._resolve(None if full else fresh_paths)

            generation = int(self.meta("generation") or 0) + 1
            for key, value in (
                ("schema_version", str(SCHEMA_VERSION)), ("ziplex_version", __version__),
                ("resolver_version", __version__), ("config_fp", config_fp), ("generation", str(generation)),
                ("updated_ns", str(started_ns)),
            ):
                self.conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)", (key, value))

        return {
            "added": len(added), "changed": len(changed), "removed": len(removed),
            "full_resolve": full, "generation": generation,
        }

    def _resolve(self, only: set[str] | None) -> None:
        """Recomputes edges for `only` (every graph file when None)."""
        rows = self.conn.execute(
            f"SELECT id, path, kind, flagged FROM files WHERE kind IN ({','.join('?' * len(GRAPH_KINDS))})",
            GRAPH_KINDS,
        ).fetchall()
        ids = {path: fid for fid, path, _, _ in rows}
        names = list(ids)
        builder = GraphBuilder(self.root, [self._abs(n) for n in names])
        stem_map = build_stem_map(names)
        all_names = set(names)
        targets = [r for r in rows if only is None or r[1] in only]
        if only is None:
            self.conn.execute("DELETE FROM edges")
        else:
            self.conn.executemany("DELETE FROM edges WHERE src_id = ?", [(r[0],) for r in targets])

        out = []
        for fid, name, kind, is_flagged in targets:
            abs_path = self._abs(name)
            if kind == "code":
                for raw, line, ctx in self.conn.execute("SELECT raw, line, ctx FROM imports WHERE file_id = ?", (fid,)).fetchall():
                    for dep in builder.code_dependencies(abs_path, [raw]):
                        target = resolve_dependency(dep, stem_map, all_names, source_name=name)
                        if target == name:
                            continue
                        out.append((fid, ids.get(target) if target else None, None if target else dep, "import", line, ctx))
            elif kind == "text" and not is_flagged:
                for ref in builder.text_references(abs_path):
                    if ref != name and ref in ids:
                        out.append((fid, ids[ref], None, "text", None, None))
        self.conn.executemany(
            "INSERT INTO edges (src_id, dst_id, external, kind, line, ctx) VALUES (?, ?, ?, ?, ?, ?)", out,
        )

    def relationships(self) -> dict:
        """build_tree()-shaped view: {path: {internal, external, internal_text_refs}}."""
        rel = {
            path: {"internal": [], "external": [], "internal_text_refs": []}
            for (path,) in self.conn.execute(
                f"SELECT path FROM files WHERE kind IN ({','.join('?' * len(GRAPH_KINDS))}) ORDER BY path", GRAPH_KINDS,
            )
        }
        code_targets: dict[str, set] = {}
        text_targets: dict[str, set] = {}
        query = (
            "SELECT s.path, d.path, e.external, e.kind FROM edges e JOIN files s ON s.id = e.src_id "
            "LEFT JOIN files d ON d.id = e.dst_id ORDER BY e.rowid"
        )
        for src, dst, external, kind in self.conn.execute(query):
            entry = rel[src]
            if dst is None:
                if external not in entry["external"]:
                    entry["external"].append(external)
                continue
            if dst not in entry["internal"]:
                entry["internal"].append(dst)
            (text_targets if kind == "text" else code_targets).setdefault(src, set()).add(dst)
        for src, targets in text_targets.items():
            only_text = targets - code_targets.get(src, set())
            rel[src]["internal_text_refs"] = [t for t in rel[src]["internal"] if t in only_text]
        return rel
