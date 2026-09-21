"""Disk-boundary translation for `relationships` -- the one place that knows
aif.json's *stored* shape differs from the *in-memory* one.

In memory (build_tree(), edits.py, file/relationship.py, the GUI editor, MCP
queries) every file's entry is `{internal, external, internal_text_refs}`
where `internal_text_refs` is a subset of `internal` -- a prose mention of a
filename (README, config value) tagged apart from a real import. Serialized
as-is, that subset rule means every such edge is written twice, prose
mentions were ~63% of all edges in Ziplex's own self-pack (which also let
them dominate a "most-central files" reading), and an entry with nothing in
it still costs a `{"internal": [], ...}` block -- roughly half of aif.json's
tokens went to `relationships`, the file an AI reads in full by default.

Stored, aif.json carries only the certain edges: `{internal: [...],
external: [...]}` with `internal` *excluding* text-reference edges and an
entry omitted entirely when both lists are empty. The weak (prose-mention)
edges move to `<name>.detail.json`'s per-file `text_refs` -- the file an AI
only fetches on demand -- and are merged back in whenever a Ziplex tool
(MCP/GUI/CLI/skill export) loads the graph, so nothing that consumes the
in-memory shape changes.

Backward compatible both ways: an aif.json written before this existed has
`internal_text_refs` on every entry and is passed through untouched; a
missing/unreadable detail.json just means the weak edges aren't restored (the
graph degrades to certain edges only, never an error).

Every reader/writer of a *saved* aif.json's relationships goes through
`attach_weak_edges()`/`detach_weak_edges()` (or the I/O wrappers below) rather
than each re-deriving this -- the same duplicated-call-site risk
text_references.py's own docstring records for merge_text_references().
"""

import json
from pathlib import Path

WEAK_KEY = "text_refs"


def detach_weak_edges(relationships: dict) -> tuple[dict, dict[str, list[str]]]:
    """In-memory -> stored. Returns (slim relationships, {file: weak targets}).

    Entries already in the slim form (no `internal_text_refs` key) pass
    through unchanged, so calling this on an already-detached graph is a
    no-op rather than an error.
    """
    slim: dict = {}
    weak: dict[str, list[str]] = {}
    for name, entry in relationships.items():
        text_refs = list(entry.get("internal_text_refs", []))
        internal = [t for t in entry.get("internal", []) if t not in text_refs]
        external = list(entry.get("external", []))
        if text_refs:
            weak[name] = text_refs
        if internal or external:
            slim[name] = {"internal": internal, "external": external}
    return slim, weak


def expand_relationships(
    slim: dict, file_names, weak: dict[str, list[str]] | None = None
) -> dict:
    """Stored -> in-memory. Every name in `file_names` (plus any already in
    `slim`) gets a full `{internal, external, internal_text_refs}` entry --
    `build_tree()` gives every packed file one, and query_service's
    `_require_known_file()` relies on "a key means a recognized file", so
    omitted-empty entries must come back.

    An entry that already has `internal_text_refs` (an aif.json from before
    the slim form) is trusted as-is and never merged with `weak`.
    """
    weak = weak or {}
    full: dict = {}
    for name in list(dict.fromkeys([*file_names, *slim])):
        entry = slim.get(name, {})
        if "internal_text_refs" in entry:
            full[name] = entry
            continue
        internal = list(entry.get("internal", []))
        text_refs = [t for t in weak.get(name, []) if t not in internal]
        full[name] = {
            "internal": internal + text_refs,
            "external": list(entry.get("external", [])),
            "internal_text_refs": text_refs,
        }
    return full


def attach_weak_edges(aif: dict, detail: dict | None) -> dict:
    """Returns `aif` with `relationships` expanded to the in-memory shape,
    weak edges read from `detail`'s per-file `text_refs` (None -> none
    restored). Copies the top-level dict only -- callers treat the result as
    read-only or hand it back through detach_weak_edges() before saving.
    """
    if "relationships" not in aif:
        return aif
    weak = {name: entry[WEAK_KEY] for name, entry in (detail or {}).items() if entry.get(WEAK_KEY)}
    return {
        **aif,
        "relationships": expand_relationships(aif["relationships"], aif.get("files", {}), weak),
    }


def detail_path_for(aif_path: str) -> Path:
    p = Path(aif_path)
    return p.with_name(f"{p.stem}.detail.json")


def read_detail_or_none(aif_path: str) -> dict | None:
    try:
        with open(detail_path_for(aif_path), "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None


def load_aif_full(aif_path: str) -> dict:
    """aif.json with its relationships expanded to the in-memory shape.
    Raises the same OSError/json.JSONDecodeError a bare open()/json.load()
    would for aif.json itself; a missing detail.json is never an error.
    """
    with open(aif_path, "r", encoding="utf-8") as f:
        aif = json.load(f)
    return attach_weak_edges(aif, read_detail_or_none(aif_path))


def save_relationships_edit(aif_path: str, edit) -> dict:
    """The one saved-project read-modify-write for `relationships`:
    `edit(relationships) -> relationships` runs against the full in-memory
    shape, then the certain edges are written back to aif.json and the weak
    ones to detail.json's `text_refs`. Everything else in both files is
    written back untouched (a detail.json that's gone is left absent rather
    than recreated with empty `compressed` bodies). Callers needing mutual
    exclusion (gui/pack_service.py) wrap this in their own lock.
    Returns the full-shape relationships.
    """
    with open(aif_path, "r", encoding="utf-8") as f:
        aif = json.load(f)
    detail = read_detail_or_none(aif_path)

    full = attach_weak_edges(aif, detail)
    relationships = edit(full.get("relationships", {}))

    # No detail.json means nowhere to persist the weak edges -- keep the full
    # (duplicated) shape in aif.json rather than silently dropping them.
    if detail is None:
        aif["relationships"] = relationships
        weak = {}
    else:
        aif["relationships"], weak = detach_weak_edges(relationships)
    with open(aif_path, "w", encoding="utf-8") as f:
        json.dump(aif, f, ensure_ascii=False, indent=2)

    if detail is not None:
        for name, entry in detail.items():
            entry.pop(WEAK_KEY, None)
            if name in weak:
                entry[WEAK_KEY] = weak[name]
        with open(detail_path_for(aif_path), "w", encoding="utf-8") as f:
            json.dump(detail, f, ensure_ascii=False, indent=2)

    return relationships
