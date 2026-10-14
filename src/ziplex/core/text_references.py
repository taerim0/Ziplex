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
also merge the match into `dependencies` *and* record it separately as
`text_dependencies`, or `internal_text_refs` silently comes back empty for
that caller's files with no error to catch it -- a real bug caught by code
review the same day this was added: cli.py's `tree` subcommand had its own
independent copy of this merge loop and had been updated for the first half
only. `merge_text_references()` below is that pair of steps, factored out
after the same duplication bit a second time (packager.py's loop and
cli.py's `tree` subcommand had quietly drifted into two non-identical
copies of it) -- both callers now go through it instead of each inlining
the merge, so a third caller (or a future change to the merge contract)
has one place to update instead of a reviewer re-auditing every call site
by hand again.
"""

import re
from pathlib import Path

from ..extract.code.languages import get_language_config
from ..file.textutil import read_text


def find_text_references_for_file(
    file_path: str, name: str, all_names: list[str], index: "TextRefIndex | None" = None,
) -> list[str]:
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

    `index` is a TextRefIndex built once from `all_names`; a per-file loop
    should pass it (building it per call costs O(len(all_names)) each time).
    """
    if get_language_config(Path(file_path).suffix) is not None:
        return []
    content = read_text(file_path)
    if content is None:
        return []
    return find_text_references(content, name, all_names, index=index)


def merge_text_references(dependencies: list[str], text_refs: list[str]) -> tuple[list[str], list[str]]:
    """The two-field merge every caller of find_text_references_for_file()/
    find_text_references() must perform -- fold `text_refs` into
    `dependencies` (same as a resolved import) *and* return them again
    separately as `text_dependencies`, so build_tree() (file/relationship.py)
    can later tag a text-reference-derived edge apart from a real import as
    `internal_text_refs` once both reach `relationships`. See this module's
    own docstring for why skipping the second half silently leaves
    `internal_text_refs` empty with no error to catch it -- a real bug this
    exact merge already hit once, between packager.py's per-file loop and
    cli.py's `tree` subcommand independently reimplementing it. Both now
    call this instead of each inlining the pair of assignments, so a future
    change to the merge contract has one place to update.

    A plain concatenation, not a dedup -- callers merging into an already-
    merged `dependencies` (packager.py's post-summary-generation merge can
    re-run against a checkpoint-restored dependencies list already carrying
    a prior merge) get a harmless duplicate, which build_tree() already
    dedupes when it builds `internal`/`external`.
    """
    return dependencies + text_refs, list(text_refs)


def find_text_references(
    content: str, self_path: str, other_paths: list[str], index: "TextRefIndex | None" = None,
) -> list[str]:
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

    A bare filename+extension match additionally requires that exactly one
    *other* collected file actually has that basename -- a project with
    several same-named files in different folders (AGENTS.md, __init__.py,
    and README.md are all common) otherwise fans a single directory-less
    mention out to every one of them, most of which the text almost never
    actually meant. Verified directly dogfooding Ziplex on its own repo: a
    single ".github/workflows/test.yml" comment naming "the root AGENTS.md"
    used to also link it to 5 unrelated nested AGENTS.md files purely
    because they share a basename. Same "an ambiguous mention is worse than
    a missed one" reasoning already applied to bare stems above -- this
    doesn't reduce to that case (an ambiguous basename here is still
    genuinely a real filename, just one this project happens to reuse) so
    it needed its own check rather than following automatically from it.
    The full-path form is unaffected either way -- it already names one
    specific candidate unambiguously, regardless of how many other files
    elsewhere share its basename.

    `index` must have been built from exactly `other_paths`; when omitted
    one is built here. Matching semantics are identical either way -- the
    index only narrows which candidates reach _contains_token().
    """
    if index is None:
        index = TextRefIndex(other_paths)
    found = set()

    for token in index.candidate_tokens(content):
        if token in index.paths and token != self_path and _contains_token(content, token, strict_path_boundary=True):
            found.add(token)
        owners = index.by_basename.get(token)
        if owners is not None:
            # Ambiguity counts *other* files only -- self never competes.
            others = [p for p in owners if p != self_path]
            if len(others) == 1 and _contains_token(content, token):
                found.add(others[0])

    return index.ordered(found)


class TextRefIndex:
    """Once-per-run lookup structure over the collected file keys, shared by
    every find_text_references() call in a run.

    Candidate discovery is exact, not heuristic: every match
    _contains_token() can accept is bounded by a non-word char (or content
    edge) on both sides. A token made only of _RUN_CHARS therefore sits
    inside one maximal _RUN_RE run of content, starting at the run start or
    right after a non-word char and ending at the run end or right before
    one -- candidate_tokens() enumerates exactly those spans and looks them
    up. Tokens containing a char outside _RUN_CHARS (whitespace, quotes,
    brackets, ...) can't be found that way and are checked directly on every
    call (`fallback`). Every hit is still re-verified by _contains_token().
    """

    _MEMO_LIMIT = 500_000

    def __init__(self, all_names: list[str]):
        self.names = list(all_names)
        self.paths = set(self.names)
        self.by_basename: dict[str, list[str]] = {}
        for name in self.names:
            self.by_basename.setdefault(name.rsplit("/", 1)[-1], []).append(name)

        tokens = self.paths | self.by_basename.keys()
        tokens.discard("")
        self.fallback = sorted(t for t in tokens if not _RUN_RE.fullmatch(t))
        self.tokens = tokens.difference(self.fallback)
        self.first_chars = {t[0] for t in self.tokens}
        self.last_chars = {t[-1] for t in self.tokens}
        self.min_len = min((len(t) for t in self.tokens), default=0)
        self.max_len = max((len(t) for t in self.tokens), default=0)

        self._order: dict[str, int] = {}
        for i, name in enumerate(self.names):
            self._order.setdefault(name, i)
        self._has_duplicates = len(self._order) != len(self.names)
        self._memo: dict[str, tuple[str, ...]] = {}

    def candidate_tokens(self, content: str) -> set[str]:
        """Superset of the tokens _contains_token() could match in content."""
        hits = set(t for t in self.fallback if t in content)
        if not self.tokens:
            return hits
        memo = self._memo
        for run in _RUN_RE.findall(content):
            run_hits = memo.get(run)
            if run_hits is None:
                run_hits = self._run_hits(run)
                if len(memo) < self._MEMO_LIMIT:
                    memo[run] = run_hits
            if run_hits:
                hits.update(run_hits)
        return hits

    def _run_hits(self, run: str) -> tuple[str, ...]:
        nonword = [m.start() for m in _NON_WORD_RE.finditer(run)]
        if not nonword:
            return (run,) if run in self.tokens else ()
        n = len(run)
        starts = [0] + [p + 1 for p in nonword if p + 1 < n]
        ends = nonword + [n]
        if ends[0] == 0:
            ends = ends[1:]
        first, last, tokens = self.first_chars, self.last_chars, self.tokens
        lo, hi = self.min_len, self.max_len
        out = []
        for s in starts:
            if run[s] not in first:
                continue
            for e in ends:
                length = e - s
                if length < lo:
                    continue
                if length > hi:
                    break
                if run[e - 1] in last:
                    piece = run[s:e]
                    if piece in tokens:
                        out.append(piece)
        return tuple(out)

    def ordered(self, found: set[str]) -> list[str]:
        """found, in the original all_names order (duplicates preserved)."""
        if self._has_duplicates:
            return [name for name in self.names if name in found]
        return sorted(found, key=self._order.__getitem__)


# Chars a path-like run may contain; anything else is a non-word separator
# (so a run boundary is always a valid _contains_token() boundary).
_RUN_CHARS = r"[^\s\"'`<>()\[\]{}|,;*=]"
_RUN_RE = re.compile(_RUN_CHARS + "+")
_NON_WORD_RE = re.compile(r"\W")


def _contains_token(content: str, token: str, strict_path_boundary: bool = False) -> bool:
    """Word-boundary-anchored substring search. re.escape(token) so path
    separators/dots in it are matched literally, not as regex metacharacters.
    (?<!\\w)/(?!\\w) rather than plain \\b: \\b only fires at a \\w/\\W
    transition, and "/"/"." are already \\W, so a plain \\b placed right
    before a leading path separator or after a trailing one doesn't reliably
    require what we actually want -- only an *adjacent letter/digit/
    underscore* should disqualify a match as "part of a different, larger
    token" (a leading "/" or "." is a normal, expected path delimiter, not a
    sign of a false positive) -- *except* right before a full relative path,
    where treating "/" as a harmless boundary lets a shorter collected
    file's path false-match as a substring of a longer, unrelated file's
    path whenever a "/" sits at the split point (e.g. content naming only
    "sub/scenes/player.gd" would otherwise also match the unrelated token
    "scenes/player.gd").

    strict_path_boundary additionally requires that a leading "/" not be a
    bare directory separator continuing a longer path -- only a URI-scheme-
    style "//" (Godot's res://, a rare double-slash), a relative-link prefix
    ("./"/"../", one or more segments -- the common way a Markdown link or a
    config value spells a relative path), or the true start of content/line
    still counts as a valid boundary there. Left off (the default) for a
    bare filename match, where any directory prefix is a normal, expected
    way to reference it.
    """
    # Cheap pre-filter: every pattern below requires the literal token, so if
    # it isn't a substring at all no regex can match. Skips compiling and
    # running a lookbehind-heavy pattern over the whole content for the
    # (vast majority of) candidates that simply aren't mentioned -- profiled
    # at ~7s of a ~11s non-LLM pack of Ziplex's own repo.
    if token not in content:
        return False
    escaped = re.escape(token)
    if strict_path_boundary:
        # Third alternative: one or more "./"/"../" segments, themselves
        # preceded by start-of-content or a non-word/dot/slash char -- a
        # real gap found by code review: the first two alternatives alone
        # never match a path immediately after a single "./" or "../"
        # prefix (the char right before the path is always just "/", which
        # the first alternative excludes and the second requires "//" for),
        # so "see ./pkg_a/util.py" or "see ../pkg_a/util.py" silently
        # recorded no reference at all.
        pattern = rf"(?:(?<![\w/])|(?<=//)|(?:^|(?<=[^\w./]))(?:\.\.?/)+){escaped}(?!\w)"
    else:
        pattern = rf"(?<!\w){escaped}(?!\w)"
    return re.search(pattern, content) is not None
