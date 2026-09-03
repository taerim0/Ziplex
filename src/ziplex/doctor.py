"""`ziplex doctor` -- a network-free environment sanity check: confirms the
pieces a real `pack` run depends on are actually present and configured,
without spending an LLM call or requiring a project to already be packed.
Exists for the same "why is X not working" troubleshooting reason
llm.py's retry-log/thinking-budget fixes do -- 429/503s, a silently
misconfigured API key, or a Windows-only secretlint gap are all easier to
diagnose with one command than by re-reading llm.py's/scanner.py's own
docstrings each time.

Pure/testable: run_diagnostics() returns a plain dict, no printing --
cli.py's `_print_doctor()` is the only renderer. The one bit of I/O beyond
env-var/settings-file reads is a `secretlint --version` subprocess probe,
mirroring scanner.py's own real invocation (not just `shutil.which()` --
see `_secretlint_available()`'s own docstring for why that alone isn't
equivalent on Windows).
"""

import re
import subprocess
import sys
from pathlib import Path

from . import __version__
from . import checkpoint as _checkpoint
from . import llm
from . import settings as app_settings

try:
    import tomllib
except ImportError:  # Python <3.11: no stdlib tomllib -- same tomli
    try:              # fallback tech_stack.py's own _load_toml() uses.
        import tomli as tomllib
    except ImportError:
        tomllib = None

# Same repo-root derivation checkpoint.py's own CHECKPOINT_DIR uses --
# __file__ is src/ziplex/doctor.py, so .parent.parent.parent is the repo
# root (or, for a real installed package, wherever pyproject.toml would be
# if it were bundled, which it usually isn't -- _min_python() below
# degrades to _FALLBACK_MIN_PYTHON in exactly that case).
_PYPROJECT_PATH = Path(__file__).parent.parent.parent / "pyproject.toml"
_FALLBACK_MIN_PYTHON = (3, 10)  # used only when pyproject.toml can't be read -- see _min_python()


def _min_python() -> tuple[int, int]:
    """Reads pyproject.toml's own `requires-python` (e.g. ">=3.10" ->
    (3, 10)) instead of a second hardcoded constant -- a code-review
    finding: a hardcoded MIN_PYTHON here could silently drift the moment
    pyproject.toml's own value changes, the same duplicated-default risk
    already fixed once for llm.py's DEFAULT_PROVIDER_NAME. Falls back to
    _FALLBACK_MIN_PYTHON (never raises) when pyproject.toml isn't
    reachable -- a real end-user pip/PyPI install doesn't bundle it -- or
    when neither tomllib nor tomli is available, or the field doesn't
    parse as expected.
    """
    if tomllib is None or not _PYPROJECT_PATH.is_file():
        return _FALLBACK_MIN_PYTHON
    try:
        with open(_PYPROJECT_PATH, "rb") as f:
            data = tomllib.load(f)
        spec = data["project"]["requires-python"]  # e.g. ">=3.10" or ">=3.10,<4.0"
        match = re.search(r"(\d+)\.(\d+)", spec)
        if not match:
            return _FALLBACK_MIN_PYTHON
        return (int(match.group(1)), int(match.group(2)))
    except (OSError, tomllib.TOMLDecodeError, KeyError, TypeError):
        return _FALLBACK_MIN_PYTHON


MIN_PYTHON = _min_python()


def _secretlint_available() -> bool:
    """Mirrors scanner.py's own `_scan_with_secretlint()` call shape --
    `secretlint --version`, not just `shutil.which("secretlint")` -- since
    on Windows a global npm install is a `.cmd` shim `subprocess.run()`
    can't resolve without `shell=True`/`shutil.which()` first (a known,
    documented gap: scanner.py's own module docstring). Any failure here
    (not installed, not on PATH, the exact Windows shim gap) reports
    unavailable rather than raising -- this is purely informational,
    scan_file() already has a working regex fallback either way.
    """
    try:
        subprocess.run(["secretlint", "--version"], capture_output=True, timeout=5, check=False)
        return True
    except (OSError, subprocess.TimeoutExpired):
        return False


def run_diagnostics(project_path: str | None = None) -> dict:
    """Everything `ziplex doctor` reports, as one dict. `project_path` is
    optional -- most of this is process/environment-wide, not tied to any
    one project -- but when given, adds a second block of project-scoped
    checks (.env presence, git repo) a human packing that specific project
    would also want to see in the same pass.
    """
    py = sys.version_info
    provider = llm.describe_active_provider()
    result = {
        "ziplex_version": __version__,
        "python_version": f"{py.major}.{py.minor}.{py.micro}",
        "python_ok": (py.major, py.minor) >= MIN_PYTHON,
        "python_min": f"{MIN_PYTHON[0]}.{MIN_PYTHON[1]}",
        "llm_provider": provider["name"],
        "llm_model": provider["model"],
        "llm_api_key_present": provider["api_key_present"],
        "secretlint_available": _secretlint_available(),
        "settings_file_present": app_settings.SETTINGS_PATH.exists(),
        "checkpoint_count": len(_checkpoint.list_checkpoints()),
    }
    if project_path:
        root = Path(project_path)
        result["project_path"] = str(root)
        result["project_is_dir"] = root.is_dir()
        result["project_has_env_file"] = (root / ".env").is_file()
        result["project_is_git_repo"] = (root / ".git").exists()
    return result
