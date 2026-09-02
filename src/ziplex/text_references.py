"""Detects references to other collected project files inside non-code
text content (JSON, Markdown, plain text, Godot .tscn/.tres scenes, etc.)
-- files with no Tree-sitter grammar, so extract_dependencies() always
returns [] for them, meaning they never showed up as anything but leaves
in `relationships` even when they obviously reference other project files
(a Godot scene's `[ext_resource path="res://player.gd"]`, a Markdown link,
a config value naming another file).

Deliberately not LLM-based: matching against the *actual* list of already-
collected file paths (not a generic "looks like a path" regex) makes this
free and precise -- unlike extract_dependencies()'s raw import strings,
which need resolve_dependency() to check later whether they match a real
file, a match found here already IS a real collected file, so it can go
straight into `dependencies` the same way a resolved import would (see
packager.py's per-file loop -- it's appended there, not routed through any
separate resolve step).

A Godot scene's ext_resource path is a genuine structural coupling (the
scene can't load without it), while a README mentioning a filename in prose
is a much weaker signal -- both still land in the same `dependencies` list
here (packager.py's per-file loop appends this module's matches onto
whatever extract_dependencies() already found), but are no longer
indistinguishable once they reach `relationships`: packager.py also records
this module's own matches separately as each file's `text_dependencies`, and
file/relationship.py's `build_tree()` uses that to tag every text-reference-
derived edge as `internal_text_refs`, a subset of `internal` -- a target
also reached by a real import stays a plain, fully certain edge.
`get_dependents()`/`get_blast_radius()` take an `include_text_refs` param
for a caller that wants to exclude the weaker signal entirely, not just see
it flagged.

Every caller of find_text_references_for_file()/find_text_references() must
mirror this same pair of steps -- merge the match into `dependencies` *and*
record it separately as `text_dependencies` -- or `internal_text_refs`
silently comes back empty for that caller's files, with no error to catch
it (a real bug caught by code review the same day this was added: cli.py's
`tree` subcommand has its own independent copy of this merge loop and had
been updated for the first half only). `packager.py`'s per-file loop and
`cli.py`'s `tree` subcommand are today's only two callers -- both do this
correctly now; keep it that way in a third.
"""

import re
from pathlib import Path

from .extract.code.languages import get_language_config
from .file.textutil import read_text


def find_text_references_for_file(file_path: str, name: str, all_names: list[str]) -> list[str]:
    """File-level convenience wrapper around find_text_references(): reads
    file_path itself and skips it entirely (empty list, no error) when it
    has a Tree-sitter grammar (get_language_config(ext) is not None -- that
    file's own dependency_handler already covers it, and re-scanning its
    text on top would risk noisy incidental matches inside comments/
    strings) or isn't readable as text at all.

    Exists so this "which files get scanned, and how" decision lives in
    exactly one place -- packager.py's pack() and cli.py's `tree` subcommand
    both call this instead of each re-implementing the same
    get_language_config/read_text guard independently and risking drift.
    """
    if get_language_config(Path(file_path).suffix) is not None:
        return []
    content = read_text(file_path)
    if content is None:
        return []
    return find_text_references(content, name, all_names)


def find_text_references(content: str, self_path: str, other_paths: list[str]) -> list[str]:
    """Returns the subset of other_paths (relative, POSIX-style keys -- see
    file/textutil.py's relative_key(), which is what produces them) that
    appear referenced inside content. self_path is excluded even if
    pathologically self-referential.

    Two match forms per candidate, both word-boundary anchored (so
    "player.gd" doesn't match inside "multiplayer.gd" or "player.gdx"):
    - the full relative path ("scenes/player.gd") -- how Godot's res://
      scheme and a relative Markdown link actually write it.
    - just the filename+extension ("player.gd") -- the common case of a
      reference that omits the directory (a config value, "see player.gd"
      in a doc).

    Bare stems (no extension, e.g. "player") are deliberately never
    matched -- "config"/"main"/"index"/"utils" are common enough words that
    matching them against arbitrary prose would produce far more noise than
    signal; requiring the extension is most of what keeps this precise.
    """
    found = []
    for other in other_paths:
        if other == self_path:
            continue
        filename = other.rsplit("/", 1)[-1]
        if _contains_token(content, other) or _contains_token(content, filename):
            found.append(other)
    return found


def _contains_token(content: str, token: str) -> bool:
    """Word-boundary-anchored substring search. re.escape(token) so path
    separators/dots in it are matched literally, not as regex metacharacters.
    (?<!\\w)/(?!\\w) rather than plain \\b: \\b only fires at a \\w/\\W
    transition, and "/"/"." are already \\W, so a plain \\b placed right
    before a leading path separator or after a trailing one doesn't reliably
    require what we actually want -- only an *adjacent letter/digit/
    underscore* should disqualify a match as "part of a different, larger
    token" (a leading "/" or "." is a normal, expected path delimiter, not a
    sign of a false positive).
    """
    return re.search(rf"(?<!\w){re.escape(token)}(?!\w)", content) is not None
