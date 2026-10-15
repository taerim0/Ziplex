import json
import random

import pytest

from ziplex.core.graph import build_graph
from ziplex.core.index import GraphIndex


def _write(root, rel, text):
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


BASE = {
    "pkg/__init__.py": "from .core import Server\n",
    "pkg/core.py": "class Server:\n    pass\n",
    "pkg/util.py": "def helper():\n    return 1\n",
    "app.py": "from pkg import Server\nfrom pkg import util\n",
    "tests/test_app.py": "import app\n\ndef test_x():\n    assert app\n",
    "tests/conftest.py": "import pytest\n",
    "README.md": "See app.py and pkg/core.py.\n",
    "tsconfig.json": json.dumps({"compilerOptions": {"baseUrl": ".", "paths": {"@/*": ["web/*"]}}}),
    "web/lib.ts": "export const x = 1\n",
    "web/page.ts": "import { x } from '@/lib'\nexport default x\n",
    "go.mod": "module example.com/m\n",
    "internal/db/db.go": "package db\n\nfunc Open() {}\n",
    "cmd/main.go": 'package main\n\nimport "example.com/m/internal/db"\n\nfunc main() { db.Open() }\n',
}


def _relationships(root, db):
    index = GraphIndex(str(root), db_path=str(db))
    index.update()
    rel = index.relationships()
    index.close()
    return rel


def _normalized(rel):
    return {k: {f: sorted(v[f]) for f in v} for k, v in rel.items()}


def test_index_matches_build_graph(tmp_path):
    for rel, text in BASE.items():
        _write(tmp_path / "repo", rel, text)
    root = tmp_path / "repo"
    from ziplex.file.collector import collect_files

    expected = build_graph(str(root), collect_files(str(root)))
    got = _relationships(root, tmp_path / "a.db")
    assert _normalized(got) == _normalized(expected)
    assert "pkg/__init__.py" in got["app.py"]["internal"]
    assert got["web/page.ts"]["internal"] == ["web/lib.ts"]
    assert got["README.md"]["internal_text_refs"] == sorted(got["README.md"]["internal"])


MUTATIONS = [
    ("modify", "pkg/util.py", "from .core import Server\n\ndef helper():\n    return Server\n"),
    ("modify", "app.py", "import pkg.util\n"),
    ("add", "pkg/extra.py", "from . import util\n"),
    ("add", "web/lib/index.ts", "export const y = 2\n"),
    ("remove", "pkg/util.py", None),
    ("remove", "README.md", None),
    ("modify", "tsconfig.json", json.dumps({"compilerOptions": {"baseUrl": ".", "paths": {"@/*": ["other/*"]}}})),
    ("modify", "go.mod", "module example.com/other\n"),
    ("add", "internal/db/conn.go", "package db\n\nfunc conn() {}\n"),
    ("add", "docs/guide.md", "Mentions app.py and core.py.\n"),
    ("add", "core.py", "x = 1\n"),
    ("modify", "README.md", "Nothing here.\n"),
]


@pytest.mark.parametrize("seed", range(12))
def test_incremental_updates_equal_a_fresh_build(tmp_path, seed):
    rng = random.Random(seed)
    root = tmp_path / "repo"
    for rel, text in BASE.items():
        _write(root, rel, text)
    live = GraphIndex(str(root), db_path=str(tmp_path / "live.db"))
    live.update()
    for step in range(6):
        kind, rel, text = rng.choice(MUTATIONS)
        path = root / rel
        if kind == "remove":
            if path.exists():
                path.unlink()
        else:
            _write(root, rel, text + f"# {step}\n" if rel.endswith((".py", ".md")) else text)
        hint = [str(path)] if rng.random() < 0.5 else None
        live.update(hint)
        fresh = _relationships(root, tmp_path / f"fresh{step}.db")
        assert _normalized(live.relationships()) == _normalized(fresh), (seed, step, kind, rel)
    live.close()


def test_update_reports_nothing_when_nothing_changed(tmp_path):
    root = tmp_path / "repo"
    for rel, text in BASE.items():
        _write(root, rel, text)
    index = GraphIndex(str(root), db_path=str(tmp_path / "i.db"))
    index.update()
    stats = index.update()
    assert (stats["added"], stats["changed"], stats["removed"], stats["full_resolve"]) == (0, 0, 0, False)


def test_default_location_is_gitignored(tmp_path):
    root = tmp_path / "repo"
    _write(root, "a.py", "x = 1\n")
    index = GraphIndex(str(root))
    index.update()
    index.close()
    assert (root / ".ziplex" / "index.db").exists()
    assert "index.db*" in (root / ".ziplex" / ".gitignore").read_text(encoding="utf-8")


def test_same_size_rewrite_with_unchanged_mtime_is_still_seen(tmp_path):
    import os

    root = tmp_path / "repo"
    _write(root, "a.py", "import b\n")
    _write(root, "b.py", "x = 1\n")
    _write(root, "c.py", "x = 2\n")
    index = GraphIndex(str(root), db_path=str(tmp_path / "i.db"))
    index.update()
    stat = os.stat(root / "a.py")
    _write(root, "a.py", "import c\n")  # same size
    os.utime(root / "a.py", ns=(stat.st_atime_ns, stat.st_mtime_ns))
    index.update()
    assert index.relationships()["a.py"]["internal"] == ["c.py"]
