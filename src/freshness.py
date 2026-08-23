"""Detects whether a project's aif.json/detail.json output has drifted from
the actual files on disk -- without re-running any part of pack(). No
Tree-sitter parsing, no LLM calls, just a hash comparison.

aif.json/detail.json are snapshots from the last pack() run, and every MCP
tool except search_project() (which always re-reads files live) trusts that
snapshot as-is. This is the tool to check whether that trust is still
warranted before relying on (or re-packing) a project.

This module only detects drift -- it doesn't refresh anything itself, with
one exception: load_previous_summaries() (staleness stage 2) is the actual
cache-reuse lookup pack() calls to decide which files' summaries are safe
to reuse instead of paying for another LLM call, built directly on
check_freshness()'s `unchanged` list. It lives here rather than in
summarizer.py (which requests a summary, but has no reason to know how a
previous pack's output is found or what "still fresh" means) since the
decision itself -- is this file's content still what it was last packed --
is exactly this module's own subject.
"""

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path

from file.textutil import read_text, relative_key

# load_previous_summaries()'s guard against a cross-project cache collision
# (see that function's own docstring): the minimum fraction of the
# *current* file set that must already appear in the previous manifest --
# hash match or not -- before any of it is trusted as "this is the same
# project, incrementally edited" at all.
_MIN_OVERLAP_RATIO = 0.5


def hash_file(file_path: str) -> str | None:
    """SHA-256 of a file's text content, or None if it can't be read as text.

    Matches collect_files()'s own binary filter -- a file that wouldn't be
    packed in the first place has no meaningful hash to compare.
    """
    content = read_text(file_path)
    if content is None:
        return None
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def build_manifest(file_paths: list[str], root: str) -> dict[str, str]:
    """{relative path: content hash} for every file_paths entry that's
    actually readable as text -- the shape pack() persists alongside its
    output (as <name>.cache.json, via save_aif()) so a later
    check_freshness() call has something to compare against.
    """
    manifest = {}
    for file_path in file_paths:
        digest = hash_file(file_path)
        if digest is not None:
            manifest[relative_key(file_path, root)] = digest
    return manifest


@dataclass(frozen=True)
class FreshnessReport:
    changed: list[str] = field(default_factory=list)
    added: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)

    @property
    def is_stale(self) -> bool:
        return bool(self.changed or self.added or self.removed)


def check_freshness(file_paths: list[str], root: str, manifest: dict[str, str]) -> FreshnessReport:
    """Compares file_paths' current content hashes against a previously
    saved manifest (build_manifest()'s output, e.g. loaded from a
    <name>.cache.json). Doesn't touch aif.json/detail.json itself -- just
    reports whether they're still trustworthy.

    file_paths should be the project's current safe/selected file list
    (e.g. from collect_files() + scan_files()), not just whatever's already
    in `manifest` -- that's what makes added/removed files detectable, not
    only changed ones.
    """
    current = build_manifest(file_paths, root)

    changed = sorted(
        name for name, digest in current.items()
        if name in manifest and manifest[name] != digest
    )
    added = sorted(name for name in current if name not in manifest)
    removed = sorted(name for name in manifest if name not in current)
    unchanged = sorted(
        name for name, digest in current.items()
        if name in manifest and manifest[name] == digest
    )

    return FreshnessReport(changed=changed, added=added, removed=removed, unchanged=unchanged)


def load_previous_summaries(root_path: str, selected: list[str], result_dir: Path) -> dict[str, str]:
    """{relative key: summary} for files in `selected` whose content hash
    matches the last successful pack's manifest, at result_dir (the
    conventional path save_aif() writes to by default: <name>.json +
    <name>.cache.json). This is what pack() checks before spending an LLM
    call on a file's summary again.

    Returns {} on any cache miss -- no previous pack there, or its output
    files are missing/unreadable/corrupt -- rather than raising. A miss just
    means every file gets summarized fresh, same as before this existed.

    Also returns {} if the previous manifest doesn't look like it actually
    belongs to this project at all (see the overlap check below) -- e.g.
    two different projects that happen to share a result-directory basename
    (both named "backend", both packed to the same default result/ folder,
    or the same GUI-configured output folder). Per-file content-hash
    matching alone can't tell that apart from a genuine re-pack of the same
    project: a boilerplate .gitignore or an empty __init__.py can coincide
    by content hash across two wholly unrelated projects. Deliberately not
    fixed with a path fingerprint the way checkpoint.py's own version of
    this bug is (see that module's _checkpoint_path() docstring) -- unlike
    checkpoint.json, this file's aif.json/cache.json are meant to be
    committed and shared across machines (README's Team use section), so a
    teammate re-packing the *same* project from a different absolute path
    must still get cache hits.
    """
    name = Path(root_path).name
    aif_path = result_dir / f"{name}.json"
    cache_path = result_dir / f"{name}.cache.json"
    if not aif_path.exists() or not cache_path.exists():
        return {}

    try:
        with open(aif_path, "r", encoding="utf-8") as f:
            previous_files = json.load(f).get("files", {})
        with open(cache_path, "r", encoding="utf-8") as f:
            previous_manifest = json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}

    report = check_freshness(selected, root_path, previous_manifest)

    # "changed" + "unchanged" = current files the previous manifest also
    # knew about at all (regardless of hash match); "added" = current files
    # it had never seen. A low ratio means most of the current project is
    # unrecognized by the previous manifest -- not "this project changed a
    # lot since last pack", but "this probably isn't the same project".
    total_current = len(report.changed) + len(report.unchanged) + len(report.added)
    recognized = len(report.changed) + len(report.unchanged)
    if total_current and recognized / total_current < _MIN_OVERLAP_RATIO:
        return {}

    return {
        rel: previous_files[rel]["summary"]
        for rel in report.unchanged
        if rel in previous_files and previous_files[rel].get("summary")
    }
