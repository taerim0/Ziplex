import io

from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError

# Same thresholds as json.py's, for consistency -- both formats are just a
# tree of dict/list/scalar values, so the same limits make sense for both.
MAX_ARRAY_ITEMS = 3
MAX_STRING_LEN = 200

MARKER = "⋮----"

# A single shared, pre-configured instance -- construction cost (loading
# ruamel's resolver/representer tables) isn't worth paying per call.
_yaml = YAML()
_yaml.preserve_quotes = True
# Ruamel's default ~80-char line wrap would reflow an untouched, under-
# threshold long string across multiple lines -- a cosmetic diff from the
# single-line source that undercuts the whole point of round-trip mode
# (preserve what wasn't touched, exactly as authored). A wide width avoids it.
_yaml.width = 4096


def compress_yaml(text: str) -> str:
    """Compresses YAML by keeping structure (keys) and eliding repetitive/long content.

    Same idea as compress_json() -- keep keys, elide long strings/arrays -- but
    deliberately NOT built the same way: json.py's load-then-dump-as-plain-
    Python-objects approach would silently corrupt YAML values PyYAML-style --
    an unquoted `3.10` becomes the float 3.1, an unquoted `NO` becomes `False`
    (YAML 1.1's well-known "Norway problem," confirmed live against a real
    GitHub-Actions-style `python-version: 3.10` line before writing this).
    JSON round-trips losslessly because its grammar is unambiguous; YAML's
    implicit typing isn't, so the same load-then-dump strategy isn't safe here.

    Uses ruamel.yaml's round-trip mode instead: every untouched scalar keeps
    its exact original text (quoting, numeric formatting), key order survives
    unchanged, and comments survive too (none of which plain PyYAML's
    safe_load/safe_dump preserve). Anchors/aliases (&x/*x -- common in
    Kubernetes and Docker Compose for shared config blocks) are preserved as
    references rather than being fully expanded, too -- expansion would make
    an anchor-heavy file *larger* after "compression," the opposite of the
    point. Mutates the loaded CommentedMap/CommentedSeq nodes in place rather
    than rebuilding plain dict/list the way json.py's _compress_value() does --
    ruamel attaches comments to the container instance itself, so rebuilding a
    fresh dict/list from a comprehension silently drops them (confirmed live:
    a trailing "# comment" on a key survives an in-place `d[key] = new_value`
    reassignment, but not a `{k: v for k, v in d.items()}` rebuild).

    Handles multi-document files (`---`-separated, common for Kubernetes
    manifests bundling several resources in one file) by compressing each
    document independently and rejoining with a leading `---` per document --
    except a single-document file, which is returned without one, matching
    how most real single-resource YAML files are actually written.

    Returns the original text unchanged if it isn't valid YAML (parsing
    failed) or has no documents at all (empty/whitespace-only file) -- same
    fallback contract as compress_json()'s on invalid JSON.
    """
    try:
        docs = list(_yaml.load_all(text))
    except YAMLError:
        return text

    if not docs:
        return text

    parts = []
    for doc in docs:
        compressed = _compress_value(doc)
        buf = io.StringIO()
        _yaml.dump(compressed, buf)
        parts.append(buf.getvalue())

    if len(parts) == 1:
        return parts[0]
    return "".join(f"---\n{part}" for part in parts)


def _compress_value(value, _seen=None):
    # A plain `*alias` (not a `<<:` merge -- that case is its own key-level
    # guard below) referencing a whole list/map isn't a copy in ruamel's
    # round-trip mode -- it's the exact same Python object as the anchor it
    # points to. Without this, a shared list gets compressed twice (once via
    # the anchor, once via the alias): the second pass sees the marker string
    # the first pass already appended as if it were real data and re-
    # truncates around it, understating how much was actually elided
    # (confirmed live: a 5-item list aliased twice reported "1개 항목 생략"
    # instead of the correct "2개 항목 생략" -- caught by /code-review low,
    # reproduced before fixing). `_seen` tracks container identity (id(), not
    # equality) across one compress_yaml() call's recursion so a second visit
    # to the same object just returns it as-is, already compressed.
    if _seen is None:
        _seen = set()
    if isinstance(value, (dict, list)):
        if id(value) in _seen:
            return value
        _seen.add(id(value))

    if isinstance(value, dict):
        # A CommentedMap materialized via a `<<: *anchor` merge key already
        # has every merged-in key in its own storage at load time -- its
        # .keys() can't tell an explicitly-written key apart from one that
        # only exists because of the merge. Reassigning a merge-derived key
        # -- even to the exact same value -- makes ruamel treat it as newly
        # explicit and duplicate it in the dump alongside the `<<:`
        # reference itself, which can make an anchor-heavy file *larger*
        # after "compression" (confirmed live against a real docker-
        # compose.yml fixture: every service using `<<: *defaults` got its
        # inherited keys spelled out redundantly). Skip any key whose name
        # comes from a merge source entirely -- the rare case of a key that
        # both merges *and* explicitly overrides with a long value just
        # doesn't get compressed, a low-cost trade-off against duplicating
        # every single merge use.
        merged_keys = set()
        for source in getattr(value, "merge", []):
            merged_keys.update(source.keys())

        for key in list(value.keys()):
            if key in merged_keys:
                continue
            value[key] = _compress_value(value[key], _seen)
        return value

    if isinstance(value, list):
        remaining = 0
        if len(value) > MAX_ARRAY_ITEMS:
            remaining = len(value) - MAX_ARRAY_ITEMS
            del value[MAX_ARRAY_ITEMS:]
        for i in range(len(value)):
            value[i] = _compress_value(value[i], _seen)
        if remaining:
            value.append(f"{MARKER} ({remaining}개 항목 생략)")
        return value

    if isinstance(value, str) and len(value) > MAX_STRING_LEN:
        return value[:MAX_STRING_LEN] + f" {MARKER} (생략)"

    return value
