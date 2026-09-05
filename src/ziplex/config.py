"""Per-project Ziplex configuration -- an optional `.ziplex.json` living in
the *target* project's own root (not Ziplex's own repo), analogous to
repomix's repomix.config.json but always optional: every setting has a
safe default, and pack() behaves identically to today with no config file
at all.

Deliberately small in scope for now: include/ignore glob patterns only --
the gap flagged when comparing against repomix's CLI (file selection used
to be all-or-nothing: everything safe via --auto, or one file at a time via
the interactive picker, with no way to scope a pack to e.g. just `src/**`
up front without clicking through everything else to exclude it).

Living in the target project (not Ziplex's own repo) means it's committable
there the same way a team already commits aif.json/detail.json/cache.json
(see the README's "Team use" section) -- it documents "how this project
gets packed" alongside the project itself, not as Ziplex-side state keyed
by a path that could move.
"""
import json
from pathlib import Path

from .file.collector import collect_files
from .file.scanner import scan_files

CONFIG_FILENAME = ".ziplex.json"

DEFAULT_CONFIG = {
    "include": [],
    "ignore": [],
}


def load_config(project_path: str) -> dict:
    """Reads <project_path>/.ziplex.json if it exists, merged over
    DEFAULT_CONFIG so a partial file (or no file at all) still yields every
    key `pack()`/`collect_files()` expect. Never raises: a missing file, an
    unreadable file, invalid JSON, or a JSON value that isn't an object all
    fall back to DEFAULT_CONFIG unchanged -- a broken config file shouldn't
    be able to block a pack, only fail to customize it.

    Each of "include"/"ignore" is only accepted from the loaded JSON when
    it's actually a list (of strings) -- a plausible typo like
    `{"include": "src/**"}` (a bare string instead of a one-item list)
    silently keeps that key's DEFAULT_CONFIG value instead of surviving
    into collection_kwargs(), which concatenates it onto a list and would
    otherwise raise TypeError on the very next pack/collect/tokens/tree
    call for this project.
    """
    config = dict(DEFAULT_CONFIG)
    config_path = Path(project_path) / CONFIG_FILENAME
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            loaded = json.load(f)
    except (OSError, json.JSONDecodeError):
        return config

    if isinstance(loaded, dict):
        for key in DEFAULT_CONFIG:
            value = loaded.get(key)
            if isinstance(value, list) and all(isinstance(item, str) for item in value):
                config[key] = value
    return config


def collection_kwargs(project_path: str, extra_include: list[str] | None = None, extra_ignore: list[str] | None = None) -> dict:
    """Loads project_path's .ziplex.json and returns {"include": ..., "ignore":
    ...} ready to splat straight into collect_files(project_path,
    **collection_kwargs(project_path)) -- the one place the "config file
    unioned with any extra CLI-flag-sourced patterns" merge happens, instead
    of every caller (cli.py, packager.py, gui/pack_service.py,
    query_service.py) re-implementing it and risking drift between what each
    one considers "the project's files" for the same project.

    extra_include/extra_ignore are for a caller with its own additional
    patterns beyond the config file (pack()'s --include/--ignore CLI flags);
    omit them entirely for a caller that just wants to respect whatever
    .ziplex.json already says, which is every other caller.
    """
    cfg = load_config(project_path)
    include = (cfg["include"] or []) + (extra_include or [])
    ignore = (cfg["ignore"] or []) + (extra_ignore or [])
    return {"include": include or None, "ignore": ignore or None}


def collect_and_scan(
    project_path: str, extra_include: list[str] | None = None, extra_ignore: list[str] | None = None
) -> dict:
    """collect_files() + scan_files(), scoped the same way collection_kwargs()
    itself is -- the actual pairing every caller collection_kwargs()'s own
    docstring names (cli.py, packager.py, gui/pack_service.py,
    query_service.py) used to independently re-implement as
    `collect_files(path, **collection_kwargs(path))` followed by a
    `scan_files(...)` call. Centralizing the pairing itself here, not just
    the kwargs it's built from, closes the same drift risk one level
    further -- a future change to how collection and scanning fit together
    now only has to be made in one place.

    Returns the raw {"safe": [...], "dangerous": [...]} scan_files() dict.
    """
    files = collect_files(project_path, **collection_kwargs(project_path, extra_include, extra_ignore))
    return scan_files(files)


def init_config(project_path: str) -> str:
    """Writes a starter .ziplex.json (DEFAULT_CONFIG's empty include/ignore
    -- JSON has no comments, so example patterns can't live inline the way
    a repomix.config.json's generated comments do; the README documents the
    syntax instead) to project_path, unless one already exists there.

    Idempotent and non-destructive: an existing config is left untouched
    and its path is returned as-is, exactly like a fresh write would --
    callers can't tell from the return value alone whether a file was
    created or already existed (see the CLI's own message for that).
    """
    config_path = Path(project_path) / CONFIG_FILENAME
    if not config_path.exists():
        config_path.write_text(
            json.dumps(DEFAULT_CONFIG, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
    return str(config_path)
