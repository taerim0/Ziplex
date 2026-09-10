import subprocess
import json
import re

from ..file.textutil import read_text
from .media import classify_media_file

# fallback keyword/pattern pairs used when secretlint fails/isn't
# available. Each pattern captures just the plausible *value* token right
# after "=" as group 1 -- a quoted string, or an unquoted run limited to
# characters a real key/path/hostname could actually contain
# ([A-Za-z0-9_.-/:+]) -- not the rest of the line, and not a bare `\S+`
# either: both would sweep up trailing punctuation/prose that isn't part of
# the value at all (a Markdown code span's closing backtick, a Korean
# particle glued directly onto it with no space -- `GEMINI_API_KEY=...`를 --,
# a trailing comma/semicolon/paren), which used to keep a doc's placeholder
# ellipsis or a code expression from ever being recognized as just that.
# `_looks_like_a_real_secret()` below judges the cleanly-isolated value;
# unquoted still has to be supported at all (not quote-only) so a standard
# .env-style assignment (API_KEY=sk-live-abc123, no quotes) keeps matching.
_VALUE_FRAGMENT = r'("[^"]*"|\'[^\']*\'|[A-Za-z0-9_.\-/:+]+)'
_SENSITIVE_KEYWORDS = [
    "AWS_SECRET", "API_KEY", "PASSWORD", "SECRET_KEY",
    "PRIVATE_KEY", "ACCESS_TOKEN", "DATABASE_URL",
]
SENSITIVE_PATTERNS = [rf'{keyword}\s*=\s*{_VALUE_FRAGMENT}' for keyword in _SENSITIVE_KEYWORDS]

# Values these patterns must not flag on their own, case-insensitively --
# a real placeholder/reference rather than an actual secret, not a
# judgment call about entropy or format. Verified directly dogfooding
# Ziplex on its own repo: every one of a real pack's 17 flagged files
# turned out to be one of these shapes, never an actual secret.
_PLACEHOLDER_VALUES = {
    "...", "…", "todo", "tbd", "n/a", "none", "null", "xxx", "changeme",
    "change_me", "your_key_here", "your_api_key_here", "placeholder", "example",
}

_NON_WORD_RE = re.compile(r"[^a-z0-9]+")


def _normalize_for_self_reference(value: str) -> str:
    """Lowercased, non-alphanumerics stripped -- "api_key", "apiKey", and
    "API-KEY" all collapse to "apikey" so a substring check against a
    keyword catches any of them the same way.
    """
    return _NON_WORD_RE.sub("", value.lower())


def _looks_like_a_real_secret(raw_value: str, keyword: str) -> bool:
    """False for the two false-positive shapes found dogfooding Ziplex on
    its own repo -- a doc's placeholder ellipsis (`GEMINI_API_KEY=...`,
    telling a *reader* to put their own key there, not a leaked one) and an
    unquoted value that's really a reference to another similarly-named
    variable (`self._explicit_api_key = api_key`, `body.gemini_api_key =
    apiKeyInput.value.trim()`) rather than a literal. True for anything
    else, including a quoted placeholder-shaped test value like `"abc123"`
    -- this stays a heuristic, not a verifier (see this module's own
    docstring), and a project's own security-scanner test fixtures
    *should* still trip it the same way a real secret would; a human
    reviewing "why was this flagged" already has the full matched line to
    judge that from (file/selector.py's review_dangerous_files()).

    The self-reference check specifically looks for `keyword` (normalized,
    see _normalize_for_self_reference()) appearing *inside* the unquoted
    value itself -- e.g. "apiKeyInput" contains "apikey" -- rather than
    rejecting every bare identifier-shaped unquoted value outright: a real
    unquoted secret (.env's own convention, `API_KEY=abc123`) is
    indistinguishable in shape from a bare variable reference
    (`API_KEY=api_key`) by itself, but a real secret's *text* essentially
    never happens to spell out the name of the field holding it.
    """
    value = raw_value.strip()
    quoted = bool(value) and value[0] in ("'", '"')
    if quoted:
        quote = value[0]
        end = value.find(quote, 1)
        value = value[1:end] if end != -1 else value[1:]
        value = value.strip()
    if not value or value.strip(".…") == "":
        return False  # empty, or a bare run of "..."/"…"
    if value.lower() in _PLACEHOLDER_VALUES:
        return False
    if len(value) <= 2:
        # Too short to plausibly be a real credential, quoted or not -- a
        # 1-2 character placeholder like "x", common in tests unrelated to
        # secret-scanning itself (e.g. GeminiProvider(api_key="x") in a
        # model-resolution test, or GEMINI_API_KEY=x in a doctor.py fixture).
        return False
    if quoted:
        # A quoted value ("abc123", "api_key") is unambiguously a string
        # literal already, syntactically -- the self-reference check below
        # is for the unquoted case only.
        return True
    return _normalize_for_self_reference(keyword) not in _normalize_for_self_reference(value)


def _line_at(file_path: str, line: int | None) -> str | None:
    """1-based line lookup for a flagged file -- the matched line's own
    text, not the whole file, is what a human needs to judge a false
    positive (see scan_file()'s docstring). None if line is unknown, or
    out of range for whatever the file actually contains (a mismatch would
    only happen if the file changed between the scan and this lookup).
    """
    if not line:
        return None
    content = read_text(file_path)
    if content is None:
        return None
    lines = content.splitlines()
    return lines[line - 1] if 1 <= line <= len(lines) else None


def _scan_with_secretlint(file_path: str) -> dict | bool | None:
    """False if secretlint ran and found nothing; a reason dict (see
    scan_file()) if it found something; None if secretlint itself couldn't
    run at all -- not installed, no .secretlintrc config in scope (it
    refuses to run without one), or its output wasn't the JSON shape
    expected -- the caller's signal to fall back to pattern scanning
    instead of trusting an empty result that might just mean secretlint
    silently failed to run.

    Known gap, not fixed here: on Windows, a global npm install of
    secretlint is a .cmd shim, and subprocess.run() with a bare
    "secretlint" argv[0] (no shell=True) can't resolve the .cmd extension
    the way a real shell's PATH lookup would -- every invocation raises
    FileNotFoundError and falls back to the regex path below, on every
    Windows machine, without secretlint ever actually running (verified
    directly). Resolving it via shutil.which() first would fix that, but
    was tried and reverted: it also makes secretlint actually *run* (a real
    Node process spawn, ~100ms+) on every scanned file on every platform,
    including the overwhelmingly common case of a project with no
    .secretlintrc at all (secretlint errors out immediately once spawned,
    same net result as today, just far slower to get there) -- a real,
    measured slowdown (this project's own test suite went from ~8s to
    ~40s) for a fix that only helps the narrower "Windows + a project that
    actually configured secretlint" case. Left as today's fast, if
    Windows-broken, behavior until that tradeoff has an actual answer
    (e.g. checking for a secretlint config file's existence first, cheaply,
    before ever spawning the process).
    """
    try:
        result = subprocess.run(
            ["secretlint", "--format", "json", file_path],
            capture_output=True,
            text=True,
        )
        # secretlint's JSON formatter emits a list with one entry per file
        # scanned (always exactly one here, since this is called per file),
        # not the top-level {"messages": [...]} shape a first pass at this
        # assumed -- harmless today (the FileNotFoundError above masks it
        # on every Windows run), but would have crashed uncaught the moment
        # secretlint ever actually ran successfully anywhere else.
        findings = json.loads(result.stdout)
        messages = findings[0].get("messages", []) if findings else []
        if not messages:
            return False

        first = messages[0]
        line = first.get("range", {}).get("start", {}).get("line")
        return {
            "reason": first.get("message") or "secretlint rule triggered",
            "line": line,
            "matched_text": _line_at(file_path, line),
        }
    except (json.JSONDecodeError, KeyError, IndexError, TypeError, OSError):
        return None  # secretlint failed -> fallback


def _scan_with_pattern(file_path: str) -> dict | None:
    content = read_text(file_path)
    if content is None:
        return None

    # Line-outer, pattern-inner -- the first offending *line* in top-to-
    # bottom file order wins, regardless of which pattern happens to sit
    # earlier in SENSITIVE_PATTERNS. A human reading "why was this flagged"
    # expects the earliest suspicious line in the file, not whichever
    # pattern this list happened to check first across the whole file.
    for lineno, line in enumerate(content.splitlines(), 1):
        for keyword, pattern in zip(_SENSITIVE_KEYWORDS, SENSITIVE_PATTERNS):
            match = re.search(pattern, line, re.IGNORECASE)
            if match and _looks_like_a_real_secret(match.group(1), keyword):
                return {"reason": f"패턴 일치: {pattern}", "line": lineno, "matched_text": line}
    return None


def scan_file(file_path: str) -> dict | None:
    """None if the file looks safe. Otherwise a dict describing *why* it
    was flagged -- {"reason": ..., "line": 1-based line number or None,
    "matched_text": that line's own text or None} -- enough for a human to
    judge a false positive (see file/selector.py's review_dangerous_files())
    without needing to go open the file themselves.

    A recognized media asset -- file/media.py's classify_media_file(),
    extension *and* content both confirmed, not extension alone (see that
    function's own docstring for the real gap extension-alone had) -- always
    scans as safe, with neither secretlint nor the pattern fallback ever
    actually run on it. A genuine binary media file still costs only one
    read_text() call here (which fails fast) instead of a real secretlint
    process spawn -- the real, if smaller, win this exists for.
    """
    if classify_media_file(file_path) is not None:
        return None

    result = _scan_with_secretlint(file_path)

    # 2. pattern-based fallback if secretlint failed to run at all
    if result is None:
        return _scan_with_pattern(file_path)

    return result or None  # False (secretlint ran, found nothing) -> None


def scan_files(file_paths: list[str]) -> dict:
    """{"safe": [path, ...], "dangerous": [{"file": path, "reason": ...,
    "line": ..., "matched_text": ...}, ...]} -- "dangerous" carries the
    same reason detail scan_file() returns, keyed under "file" alongside
    it, rather than a bare path list, so a caller (file/selector.py's
    terminal review, or the GUI's file-selection screen) can show a human
    *why* without a second scan_file() call per file.
    """
    results = {"safe": [], "dangerous": []}
    for file_path in file_paths:
        reason = scan_file(file_path)
        if reason:
            results["dangerous"].append({"file": file_path, **reason})
        else:
            results["safe"].append(file_path)
    return results
