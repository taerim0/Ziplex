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

from .file.media import is_media_file
from .file.textutil import read_text, relative_key

# load_previous_summaries()'s guard against a cross-project cache collision
# (see that function's own docstring): the minimum fraction of the
# *current* file set that must already appear in the previous manifest --
# hash match or not -- before any of it is trusted as "this is the same
# project, incrementally edited" at all.
_MIN_OVERLAP_RATIO = 0.5


def hash_file(file_path: str) -> str | None:
    """SHA-256 of a file's text content, or -- for a recognized media asset
    (file/media.py's is_media_file(), see collect_files()'s own binary-filter
    exception for one) -- its raw bytes instead, since there's no text to
    decode. None only for a file that's neither: matches collect_files()'s
    own binary filter, a file that wouldn't be packed in the first place has
    no meaningful hash to compare.

    is_media_file() (cheap, extension-only) is checked before ever
    attempting read_text() -- a real binary media file's decode attempt is
    guaranteed to fail only after reading the whole file, wasted I/O this
    function used to always pay first regardless of extension, and pays
    at least twice per pack (see the chunked-read comment below). This
    doesn't need the stricter classify_media_file() other call sites use:
    a file that merely carries a media extension but is actually real text
    (a Git LFS pointer) still gets a stable, correct hash either way here
    (raw bytes instead of decoded text) -- the exact bytes hashed only has
    to stay consistent between one pack's manifest and the next's freshness
    check, which this function alone (always called the same way) already
    guarantees regardless of which path a given file takes.
    """
    if is_media_file(file_path) is not None:
        return _hash_bytes(file_path)

    content = read_text(file_path)
    if content is not None:
        return hashlib.sha256(content.encode("utf-8")).hexdigest()
    return None


def _hash_bytes(file_path: str) -> str | None:
    try:
        digest = hashlib.sha256()
        # Streamed in fixed-size chunks, not one f.read() -- a media asset
        # has no size cap (MEDIA_EXTENSIONS includes video/audio), and this
        # function runs at least twice per pack (load_previous_summaries()'s
        # own check_freshness()/build_manifest(), then pack()'s final
        # build_manifest()); loading a large video/audio file whole, twice
        # over, is real avoidable memory pressure a chunked read doesn't pay.
        with open(file_path, "rb") as f:
            for chunk in iter(lambda: f.read(1_048_576), b""):
                digest.update(chunk)
        return digest.hexdigest()
    except OSError:
        return None


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


def load_previous_summaries(root_path: str, selected: list[str], result_dir: Path, lang: str = "en") -> dict[str, str]:
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

    lang (2026-08-26) also returns {} outright when the previous pack's own
    `project.language` doesn't match this run's `lang` -- otherwise an
    unchanged file's summary (written in the *previous* language) would get
    reused verbatim under a `project.language` claiming a different one, a
    real gap caught by code review: packing with `--lang en` then re-packing
    the same unchanged project with `--lang ko` used to keep the English
    summaries while still stamping `project.language: "ko"`. A previous
    aif.json with no `language` field at all (packed before this feature
    existed) is treated as `"en"`, the only language that existed then --
    same default `packager.pack()`/`checkpoint.unpack_snapshot()` already
    use for the same reason.
    """
    # .resolve() so root_path == "." (packing from inside the project's own
    # folder) doesn't collapse to "" -- Path(".").name has no name component
    # at all, which used to make this look up result/.json instead of the
    # real result/<project name>.json, silently missing every cache hit.
    name = Path(root_path).resolve().name
    aif_path = result_dir / f"{name}.json"
    cache_path = result_dir / f"{name}.cache.json"
    if not aif_path.exists() or not cache_path.exists():
        return {}

    try:
        with open(aif_path, "r", encoding="utf-8") as f:
            previous_aif = json.load(f)
        with open(cache_path, "r", encoding="utf-8") as f:
            previous_manifest = json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}

    if previous_aif.get("project", {}).get("language", "en") != lang:
        return {}
    previous_files = previous_aif.get("files", {})

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
