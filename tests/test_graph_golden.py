"""Golden dependency-graph fixtures: small synthetic repos whose exact
certain-edge set is written down by hand. Any resolver/pipeline refactor
must keep these identical (or update them deliberately)."""
import json

import pytest

from ziplex import packager
def _graph(tmp_path, monkeypatch, files: dict[str, str]) -> dict[str, list[str]]:
    root = tmp_path / "repo"
    for rel, content in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    aif = (packager.pack(str(root), auto=True, interactive=False, use_llm=False, use_cache=False))
    return {
        name: sorted(set(rel["internal"]) - set(rel.get("internal_text_refs", [])))
        for name, rel in aif["relationships"].items()
        if set(rel["internal"]) - set(rel.get("internal_text_refs", []))
    }


CASES = {
    "python_reexport_and_submodule_in_one_file": (
        {
            "pkg/__init__.py": "from .core import Server\n",
            "pkg/core.py": "class Server:\n    pass\n",
            "pkg/session.py": "def open():\n    pass\n",
            "app.py": "from pkg import Server\nfrom pkg import session\n",
        },
        {
            "pkg/__init__.py": ["pkg/core.py"],
            "app.py": ["pkg/__init__.py", "pkg/session.py"],
        },
    ),
    "python_src_layout_tests": (
        {
            "src/lib/__init__.py": "",
            "src/lib/util.py": "def f():\n    return 1\n",
            "tests/test_util.py": "from lib.util import f\n\ndef test_f():\n    assert f()\n",
        },
        {"tests/test_util.py": ["src/lib/util.py"]},
    ),
    "python_flagged_test_keeps_its_import_edge": (
        {
            "app.py": "def run():\n    return 1\n",
            "test_app.py": 'import app\nAPI_KEY = "abc123def456"\n',
        },
        {"test_app.py": ["app.py"]},
    ),
    "ts_tsconfig_alias": (
        {
            "tsconfig.json": json.dumps({"compilerOptions": {"baseUrl": ".", "paths": {"@/*": ["src/*"]}}}),
            "src/lib/x.ts": "export const x = 1\n",
            "src/app.ts": "import { x } from '@/lib/x'\nexport default x\n",
        },
        {"src/app.ts": ["src/lib/x.ts"]},
    ),
    "ts_pnpm_workspace_exports": (
        {
            "pnpm-workspace.yaml": "packages:\n  - 'packages/*'\n  - 'apps/*'\n",
            "package.json": json.dumps({"name": "root", "private": True}),
            "packages/ui/package.json": json.dumps({
                "name": "@acme/ui", "exports": {".": {"types": "./dist/index.d.ts", "default": "./src/index.ts"}},
            }),
            "packages/ui/src/index.ts": "export const Button = 1\n",
            "apps/web/package.json": json.dumps({"name": "web"}),
            "apps/web/src/page.ts": "import { Button } from '@acme/ui'\nexport default Button\n",
        },
        {"apps/web/src/page.ts": ["packages/ui/src/index.ts"]},
    ),
    "go_internal_package": (
        {
            "go.mod": "module example.com/m\n\ngo 1.22\n",
            "internal/db/db.go": "package db\n\nfunc Open() {}\n",
            "internal/db/conn.go": "package db\n\nfunc conn() {}\n",
            "cmd/main.go": 'package main\n\nimport "example.com/m/internal/db"\n\nfunc main() { db.Open() }\n',
        },
        {"cmd/main.go": ["internal/db/conn.go", "internal/db/db.go"]},
    ),
}


@pytest.mark.parametrize("case", sorted(CASES))
def test_golden_graph(case, tmp_path, monkeypatch):
    files, expected = CASES[case]
    assert _graph(tmp_path, monkeypatch, files) == expected
