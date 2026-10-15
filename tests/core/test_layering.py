"""core/ is the graph engine: it must stay importable without the LLM layer,
the GUI, the CLI, user settings or progress i18n (docs/REVIEW-2026-10-14.md
§5.1). Checked transitively, in a fresh interpreter."""
import json
import subprocess
import sys
from pathlib import Path

FORBIDDEN = [
    "ziplex.llm", "ziplex.summarizer", "ziplex.packager", "ziplex.cli", "ziplex.settings",
    "ziplex.progress_i18n", "ziplex.gui", "ziplex.corrector", "ziplex.checkpoint", "ziplex.mcp_server",
    "requests", "flask",
]


def test_core_does_not_pull_in_frontends_or_the_llm_layer():
    src = Path(__file__).resolve().parents[2] / "src"
    code = (
        "import sys, json; sys.path.insert(0, %r)\n"
        "import ziplex.core.graph, ziplex.core.index, ziplex.core.api\n"
        "print(json.dumps(sorted(sys.modules)))" % str(src)
    )
    loaded = json.loads(subprocess.check_output([sys.executable, "-c", code], text=True))
    leaked = [m for m in loaded if any(m == f or m.startswith(f + ".") for f in FORBIDDEN)]
    assert leaked == []
