"""Ziplex GUI's persistent, cross-session settings: currently just where
new packs get saved by default. Lives outside the repo entirely, under the
user's home directory -- unlike config.py's .ziplex.json (per-*target*-
project, lives inside that project, meant to be git-committed alongside
it), this is per-*user*, one file for the whole app, matching what an
installed local program's own settings usually look like rather than a
project file.

Two layers, resolved together by resolve_output_dir(): a global default
(`output_dir`, sensible fallback for any project with nothing more
specific configured) and per-project pins (`project_output_dirs`,
keyed by each project's absolute path) that override it. A pin is only
ever created when a user explicitly types an output path while packing a
given project (see gui/pack_service.py's start_pack_job()) -- never as a
side effect of merely using the global default -- so a project with no
pin of its own keeps tracking whatever the global default currently is,
even if that default changes later. Neither layer being set at all means
"nothing configured anywhere" -- callers (packager.py's `result_dir`
param) fall back to their own built-in default (RESULT_DIR) in that case,
so a fresh install with no settings.json behaves identically to before
this module existed.

Never raises, same spirit as config.py's load_config(): a missing file,
bad JSON, or a non-dict value all fall back to DEFAULT_SETTINGS unchanged,
so a fresh install (or a hand-corrupted settings.json) never blocks a pack.
"""

import json
from pathlib import Path

SETTINGS_PATH = Path.home() / ".ziplex" / "settings.json"

DEFAULT_SETTINGS = {
    "output_dir": "",           # "" = no global override, packager.RESULT_DIR applies
    "project_output_dirs": {},  # {absolute project path: output dir}, explicit per-project pins
    "gemini_api_key": "",       # "" = no override, GEMINI_API_KEY env var / .env applies (see llm.py)
}


def load_settings() -> dict:
    try:
        with open(SETTINGS_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, OSError, UnicodeDecodeError):
        return {"output_dir": "", "project_output_dirs": {}, "gemini_api_key": ""}
    if not isinstance(data, dict):
        return {"output_dir": "", "project_output_dirs": {}, "gemini_api_key": ""}

    project_dirs = data.get("project_output_dirs")
    return {
        "output_dir": data.get("output_dir") or "",
        "project_output_dirs": project_dirs if isinstance(project_dirs, dict) else {},
        "gemini_api_key": data.get("gemini_api_key") or "",
    }


def save_settings(settings: dict) -> None:
    SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(SETTINGS_PATH, "w", encoding="utf-8") as f:
        json.dump(settings, f, ensure_ascii=False, indent=2)


def _abs_key(project_path: str) -> str:
    # Absolute so the same project resolves to the same key regardless of
    # what cwd-relative form it was typed in from; not case-normalized
    # (Windows paths differing only in case are treated as distinct keys,
    # same limitation as elsewhere in this codebase -- not worth the extra
    # complexity for a single-user local settings file).
    return str(Path(project_path).resolve())


def resolve_output_dir(project_path: str, settings: dict | None = None) -> str:
    """The effective output folder for `project_path` -- a per-project pin
    if one exists, else the global default, else "" (meaning: the caller
    should fall back to its own built-in default). `settings`, if not
    given, is loaded fresh -- pass an already-loaded dict when resolving
    for several projects at once to avoid re-reading the file each time.
    """
    settings = settings if settings is not None else load_settings()
    pinned = settings["project_output_dirs"].get(_abs_key(project_path))
    return pinned or settings["output_dir"] or ""


def resolve_output_path(project_path: str, project_name: str, settings: dict | None = None) -> str:
    """The full aif.json path a new pack of `project_path` should default
    to when the user hasn't typed an explicit one -- resolve_output_dir()'s
    folder plus this project's own name, or "" if nothing is configured at
    either layer (packager.py's own RESULT_DIR-based default then applies,
    completely unchanged from before this module existed).
    """
    output_dir = resolve_output_dir(project_path, settings)
    if not output_dir:
        return ""
    return str(Path(output_dir) / f"{project_name}.json")


def set_project_output_dir(project_path: str, output_dir: str) -> None:
    """Pins `project_path` to `output_dir` going forward. Called once a pack
    actually runs with an explicitly-typed output path (gui/pack_service.py's
    start_pack_job()) -- never automatically from merely resolving/using a
    default, since that would silently freeze a project onto whatever the
    global default happened to be at the time of its first pack, instead of
    letting it keep tracking later changes to that default the way an
    unpinned project is supposed to.
    """
    settings = load_settings()
    settings["project_output_dirs"][_abs_key(project_path)] = output_dir
    save_settings(settings)


def resolve_gemini_api_key(settings: dict | None = None) -> str:
    """The GUI-editable override for GEMINI_API_KEY -- "" if nothing's been
    set via the Options page, in which case llm.py's own env var / .env
    fallback applies, completely unchanged from before this setting
    existed. `settings`, if not given, is loaded fresh.

    Unlike output_dir/project_output_dirs, this is read fresh on every
    llm.py request (see GeminiProvider._resolve_api_key()), not once at
    provider-construction time -- llm.py's module-level `_provider` is a
    singleton built once at import and stays alive for the whole GUI
    server process, so a key saved here after that needs to take effect on
    the very next pack, not after a restart.
    """
    settings = settings if settings is not None else load_settings()
    return settings["gemini_api_key"] or ""
