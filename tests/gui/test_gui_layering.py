"""The GUI is a read-only viewer: importing it must not pull in the pack
pipeline, the LLM layer, or any editing/review module
(docs/REVIEW-2026-10-14.md §5.1, §8). Checked transitively, in a fresh
interpreter, like tests/core/test_layering.py."""
import json
import subprocess
import sys
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[2] / "src"

FORBIDDEN = [
    "ziplex.llm", "ziplex.summarizer", "ziplex.checkpoint", "ziplex.confidence", "ziplex.corrector",
    "ziplex.edits", "ziplex.folder_summary", "ziplex.packager", "ziplex.progress_i18n",
    "ziplex.enrich", "ziplex.gui.pack_service",
]

# Transitional: query_service (not GUI code) still imports confidence.py,
# which drags in summarizer/llm. While that file exists, the strict test
# stubs it so it checks everything else; the xfail test below turns into a
# pass once confidence.py is deleted.
_CONFIDENCE_STUB = (
    "import types\n"
    "_m = types.ModuleType('ziplex.confidence')\n"
    "_m.project_confidence_summary = lambda files: {}\n"
    "sys.modules['ziplex.confidence'] = _m\n"
)


def _loaded_modules(stub_confidence: bool):
    code = "import sys, json; sys.path.insert(0, %r)\n" % str(SRC)
    if stub_confidence and (SRC / "ziplex" / "confidence.py").exists():
        code += _CONFIDENCE_STUB
    code += "import ziplex.gui.gui_server\nprint(json.dumps(sorted(sys.modules)))"
    loaded = json.loads(subprocess.check_output([sys.executable, "-c", code], text=True))
    if stub_confidence:
        loaded = [m for m in loaded if m != "ziplex.confidence"]
    return loaded


def _leaked(loaded):
    return [m for m in loaded if any(m == f or m.startswith(f + ".") for f in FORBIDDEN)]


def test_gui_does_not_pull_in_the_pack_pipeline_or_editing():
    assert _leaked(_loaded_modules(stub_confidence=True)) == []


@pytest.mark.xfail(
    (SRC / "ziplex" / "confidence.py").exists(),
    reason="query_service still imports confidence.py", strict=True,
)
def test_gui_import_is_clean_without_any_stub():
    assert _leaked(_loaded_modules(stub_confidence=False)) == []
