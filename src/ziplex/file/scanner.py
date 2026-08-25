import subprocess
import json
import re

from ..file.textutil import read_text
from .media import classify_media_file

# fallback patterns used when secretlint fails/isn't available
SENSITIVE_PATTERNS = [
    r'AWS_SECRET\s*=\s*["\']',
    r'API_KEY\s*=\s*["\']',
    r'PASSWORD\s*=\s*["\']',
    r'SECRET_KEY\s*=\s*["\']',
    r'PRIVATE_KEY\s*=\s*["\']',
    r'ACCESS_TOKEN\s*=\s*["\']',
    r'DATABASE_URL\s*=\s*["\']',
]


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
        for pattern in SENSITIVE_PATTERNS:
            if re.search(pattern, line, re.IGNORECASE):
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
