import subprocess
import json
import re

from ..file.textutil import read_text
from ..progress_i18n import pick
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
# Regex fragments for a secret-holding field name, matched case-insensitively
# as a whole identifier. The *SECRET*/*TOKEN* families replaced a short
# fixed list that missed JWT_SECRET, NEXTAUTH_SECRET, GITHUB_TOKEN, HF_TOKEN,
# an .npmrc `_authToken` and the like.
_SENSITIVE_KEYWORDS = [
    r"[A-Za-z0-9_]*API_?KEY",
    r"[A-Za-z0-9_]*PASSWORD", r"[A-Za-z0-9_]*PASSWD",
    r"[A-Za-z0-9_]*SECRET(?:_?KEY)?",
    r"[A-Za-z0-9_]*TOKEN",
    r"[A-Za-z0-9_]*PRIVATE_KEY",
    r"DATABASE_URL",
]
# The field name must be a whole identifier: not glued onto a longer word
# on the left (the fragments already absorb any `prefix_`), and not a
# prefix of a longer name on the right (`secrets_dir`, `token_count`).
# `-`/`.` too: `id-token: write` (a GitHub Actions permission) and
# `registry-auth-token: 5.1.1` (a lockfile package) are not secret fields.
_KEY_BOUNDARY_LEFT = r"(?<![/@A-Za-z0-9_.\-])"
# An optional type annotation between the name and `=` (`API_KEY: str =
# "sk-live-..."`, `const apiKey: string = "..."`): without it the only match
# was the annotation itself, skipped as a non-literal, and the literal after
# `=` was never looked at.
_TYPE_ANNOTATION = r"(?:\s*:\s*[A-Za-z_][\w\[\]., |]*?(?=\s*=))?"
# `KEY = value` and also `KEY: value` / `"KEY": value` (JSON, YAML, TOML
# inline tables) -- "=" alone missed every JSON/YAML config file. Named
# groups key/sep/value; see _counts_as_a_secret() for why the separator
# matters.
SENSITIVE_PATTERNS = [
    # (?<![/@]): not part of a package name ('@inquirer/password': 5.2.2 in
    # a pnpm lockfile) -- a real field name is never preceded by / or @.
    rf'{_KEY_BOUNDARY_LEFT}(?P<key>{keyword})["\']?{_TYPE_ANNOTATION}\s*(?P<sep>[=:])\s*(?P<value>{_VALUE_FRAGMENT})'
    for keyword in _SENSITIVE_KEYWORDS
]

# Secrets recognizable by their own shape, whatever they're assigned to --
# a PEM key has no `KEY=` line at all, and a token under an unlisted name
# (GITHUB_TOKEN, a bare CLI arg) never matched a keyword.
_SECRET_SHAPES = [
    # The header alone at end of line (a .pem file), or followed by an
    # escaped/real newline and actual base64 (a key embedded in a string) --
    # not code *assembling* a PEM around a variable (`...-----\n${encode(k)}`).
    ("PEM private key", re.compile(r"-----BEGIN (?:[A-Z]+ )*PRIVATE KEY-----(?:\s*$|(?:\\n|\n)[A-Za-z0-9+/]{16})")),
    ("GitHub token", re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{22,})")),
    ("AWS access key id", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("Slack token", re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}")),
    ("Google API key", re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b")),
    ("Stripe live key", re.compile(r"\b[sr]k_live_[0-9A-Za-z]{16,}")),
    ("Anthropic API key", re.compile(r"\bsk-ant-[A-Za-z0-9_\-]{20,}")),
    ("OpenAI API key", re.compile(r"\bsk-(?:proj-|svcacct-|admin-)?[A-Za-z0-9_\-]{32,}")),
    ("Hugging Face token", re.compile(r"\bhf_[A-Za-z0-9]{30,}")),
    ("npm token", re.compile(r"\bnpm_[A-Za-z0-9]{36}\b")),
]

# Files whose every assignment is a literal value (no code, so no
# `api_key = api_key`-style variable references to filter out) -- the
# self-reference heuristic and the conservative unquoted charset both only
# exist to keep *code* and *prose* from false-positiving.
_ENV_VALUE_FRAGMENT = r'("[^"]*"|\'[^\']*\'|\S+)'


def _is_env_file(file_path: str) -> bool:
    name = file_path.replace("\\", "/").rsplit("/", 1)[-1].lower()
    return name == ".env" or name.startswith(".env.") or name.endswith(".env")


def _is_env_template(file_path: str) -> bool:
    """`.env.example`/`.sample`/`.template`/`.dist` -- the committed templates
    DEFAULT_IGNORE deliberately keeps. Their values are placeholders by
    purpose (`OPENAI_API_KEY=sk-...`, `DB_PASSWORD=changethis`), so field
    names prove nothing there; only a real secret's own shape counts."""
    name = file_path.replace("\\", "/").rsplit("/", 1)[-1].lower()
    return name.endswith((".example", ".sample", ".template", ".dist")) and ".env" in name


def _is_yaml_file(file_path: str) -> bool:
    return file_path.lower().endswith((".yaml", ".yml"))


def _is_code_file(file_path: str) -> bool:
    """A source file where a hardcoded secret is always a quoted string
    literal -- `API_KEY = abc123` is a variable reference in Python/JS/Go/...,
    never a literal. Shell is the exception (`PASSWORD=hunter2` *is* one)."""
    from ..extract.code.languages import LANGUAGE_CONFIGS

    suffix = "." + file_path.rsplit(".", 1)[-1].lower() if "." in file_path else ""
    return suffix in LANGUAGE_CONFIGS and suffix not in (".sh", ".bash")


def _keyword_is_a_whole_quoted_key(line: str, start: int, end: int) -> bool:
    """When a quote directly follows the key (`password"`), the key must
    be a whole quoted key (`"db_password": ...`) -- not the tail of a quoted
    sentence (`"Hide password" : "Show password"`, a JSX ternary)."""
    if end >= len(line) or line[end] not in ("'", '"'):
        return True
    i = start
    while i > 0 and (line[i - 1].isalnum() or line[i - 1] in "_-"):
        i -= 1
    return i > 0 and line[i - 1] == line[end]

# Values these patterns must not flag on their own, case-insensitively --
# a real placeholder/reference rather than an actual secret, not a
# judgment call about entropy or format. Verified directly dogfooding
# Ziplex on its own repo: every one of a real pack's 17 flagged files
# turned out to be one of these shapes, never an actual secret.
_PLACEHOLDER_VALUES = {
    "...", "…", "todo", "tbd", "n/a", "none", "null", "xxx", "changeme",
    "change_me", "your_key_here", "your_api_key_here", "placeholder", "example",
    # A field's own generic name as its value (`password: 'password'`) is a
    # test/doc placeholder, not a credential.
    "password", "secret", "apikey", "api_key", "token",
}

_NON_WORD_RE = re.compile(r"[^a-z0-9]+")
# Placeholder shapes beyond _PLACEHOLDER_VALUES' exact words: an elided
# value (`sk-...`), a "change me" instruction, a `your_...` hint, all-x.
_PLACEHOLDER_SHAPE_RE = re.compile(
    r".*(?:\.\.\.|…).*|change[_-]?(?:me|this|it)\w*|your[_-].*|x{3,}|<[^<>]+>", re.IGNORECASE
)
# The whole value is one template/interpolation slot: Jinja/Handlebars
# `{{ x }}`, shell/JS `${X}`, Python `%(x)s`, `<x>` -- e.g. a react-email
# template's `password = "{{ password }}"` default prop.
_TEMPLATE_PLACEHOLDER_RE = re.compile(r"\{\{[^{}]*\}\}|\$\{[^{}]*\}|%\([\w.]+\)[sd]|<[\w.-]+>")


def _normalize_for_self_reference(value: str) -> str:
    """Lowercased, non-alphanumerics stripped -- "api_key", "apiKey", and
    "API-KEY" all collapse to "apikey" so a substring check against a
    keyword catches any of them the same way.
    """
    return _NON_WORD_RE.sub("", value.lower())


def _looks_like_a_real_secret(raw_value: str, keyword: str, literal_context: bool = False) -> bool:
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
    if _TEMPLATE_PLACEHOLDER_RE.fullmatch(value) or _PLACEHOLDER_SHAPE_RE.fullmatch(value):
        return False
    if value.startswith(("${", "$(")):
        # Filled in at runtime: `"$(python -c '...token_urlsafe()')"`.
        return False  # `"{{ password }}"`, `"${PASSWORD}"`: filled in later, not a literal
    if len(value) <= 4:
        # Too short to plausibly be a real credential, quoted or not -- a
        # 1-4 character placeholder like "x"/"foo"/"abc", common in tests unrelated to
        # secret-scanning itself (e.g. GeminiProvider(api_key="x") in a
        # model-resolution test, or GEMINI_API_KEY=x in a doctor.py fixture).
        return False
    if any(c.isspace() for c in value) and "PASSWORD" not in keyword.upper():
        # A key/token/URL never contains whitespace; a quoted sentence
        # under a key-named field is UI/help text ("gemini_api_key": "not
        # set -- uses GEMINI_API_KEY"), common once ":" (JSON/dict) matches.
        return False
    if quoted or literal_context:
        # A quoted value ("abc123", "api_key") is unambiguously a string
        # literal already, syntactically -- the self-reference check below
        # is for the unquoted case only. So is every value in a .env-style
        # file (literal_context): there, PASSWORD=MyPassword!2024 is a real
        # password that merely contains its own field name, not a code
        # reference to a variable.
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


def _scan_with_secretlint(file_path: str, cwd: str | None = None) -> dict | bool | None:
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
            # secretlint reads .secretlintrc from its working directory --
            # the scanned project's root, not wherever ziplex was launched
            # (which ignored the project's own config and used a stray one).
            cwd=cwd,
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
        # secretlint's real output puts the line under loc.start.line;
        # `range` is a [start, end] character-offset list, and reading it as
        # a dict raised an uncaught AttributeError the moment secretlint
        # actually reported something, aborting the whole scan.
        loc = first.get("loc")
        line = loc.get("start", {}).get("line") if isinstance(loc, dict) else None
        # first["message"] is secretlint's own output -- always English,
        # out of this project's control -- so the pick() below only ever
        # actually localizes the *fallback* half (no message present at
        # all), not full en/ko parity with the pattern-fallback path below.
        # Still worth doing: a Korean-display-language GUI user hitting this
        # exact fallback (secretlint ran, matched a rule, but reported no
        # message text) shouldn't see English leak in from here alone.
        reason = first.get("message") or pick("secretlint rule triggered", "secretlint 규칙 위반")
        return {
            "reason": reason,
            "line": line,
            "matched_text": _line_at(file_path, line),
        }
    except (json.JSONDecodeError, KeyError, IndexError, TypeError, AttributeError, OSError):
        return None  # secretlint failed -> fallback


def _scan_with_pattern(file_path: str) -> dict | None:
    content = read_text(file_path)
    if content is None:
        return None

    env_template = _is_env_template(file_path)
    env_file = _is_env_file(file_path) and not env_template
    yaml_file = _is_yaml_file(file_path)
    code_file = _is_code_file(file_path)
    # Prose: an unquoted `database_url = str(value)` in a Markdown code span
    # is quoted code, not configuration.
    prose_file = file_path.lower().endswith((".md", ".mdx", ".rst"))
    patterns = [] if env_template else SENSITIVE_PATTERNS
    if env_file:
        # Every value is a literal here, so take the whole unquoted token
        # (the conservative charset would cut PASSWORD=Pa$$w0rd! short).
        patterns = [
            rf'{_KEY_BOUNDARY_LEFT}(?P<key>{keyword})["\']?{_TYPE_ANNOTATION}\s*(?P<sep>[=:])\s*(?P<value>{_ENV_VALUE_FRAGMENT})'
            for keyword in _SENSITIVE_KEYWORDS
        ]

    # Line-outer, pattern-inner -- the first offending *line* in top-to-
    # bottom file order wins, regardless of which pattern happens to sit
    # earlier in SENSITIVE_PATTERNS. A human reading "why was this flagged"
    # expects the earliest suspicious line in the file, not whichever
    # pattern this list happened to check first across the whole file.
    for lineno, line in enumerate(content.splitlines(), 1):
        for label, shape in _SECRET_SHAPES:
            if shape.search(line):
                reason = pick(f"Looks like a {label}", f"{label} 형식으로 보임")
                return {"reason": reason, "line": lineno, "matched_text": line}
        for pattern in patterns:
            # Every match on the line, not just the first: in
            # `API_KEY: str = "sk-live-..."` the first hit is the type
            # annotation (skipped below), and the literal after `=` was
            # never looked at.
            for match in re.finditer(pattern, line, re.IGNORECASE):
                if _counts_as_a_secret(match, line, code_file, yaml_file, env_file, prose_file):
                    # The matched field name, not the raw regex -- a human
                    # reviewing "why was this flagged" reads this.
                    key = match.group("key")
                    reason = pick(
                        f"Pattern match: {key} is assigned a literal value",
                        f"패턴 일치: {key}에 리터럴 값이 할당됨",
                    )
                    return {"reason": reason, "line": lineno, "matched_text": line}
    return None


def _counts_as_a_secret(
    match: re.Match, line: str, code_file: bool, yaml_file: bool, env_file: bool, prose_file: bool = False
) -> bool:
    key, separator, value = match.group("key"), match.group("sep"), match.group("value")
    quoted = value[:1] in ("'", '"')
    if line[match.end("value"):].startswith(("${", "$(")):
        # The value continues into an interpolation
        # (`postgresql://postgres:${POSTGRES_PASSWORD}@db/app`) -- a
        # template filled in at runtime, not a literal credential.
        return False
    if (code_file or prose_file) and not quoted:
        # In source code an unquoted value is an expression --
        # `auth_token = response["access_token"]`, `database_url =
        # str(value)`, `const recoverPassword = async (...)` (all
        # found packing fastapi/full-stack-fastapi-template).
        return False
    if not _keyword_is_a_whole_quoted_key(line, match.start("key"), match.end("key")):
        return False
    if separator == ":" and not quoted and not (yaml_file or env_file):
        # An unquoted value after ":" outside YAML/.env is a type
        # annotation (`password: str`), a dict entry referencing a
        # variable (`{"api_key": api_key}`), or prose -- not a literal.
        return False
    if not (yaml_file or env_file) and line.lstrip().startswith(("*", "//", "#", "<!--")):
        # A `key: 'value'` / `const token = '...'` inside a code comment is
        # a documentation example (a JSDoc usage block), not configuration.
        # A real-format key there is still caught by _SECRET_SHAPES.
        return False
    return _looks_like_a_real_secret(value, key, literal_context=env_file)


def scan_file(file_path: str, root: str | None = None) -> dict | None:
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

    result = _scan_with_secretlint(file_path, cwd=root)

    # 2. pattern-based fallback if secretlint failed to run at all
    if result is None:
        return _scan_with_pattern(file_path)

    return result or None  # False (secretlint ran, found nothing) -> None


def scan_files(file_paths: list[str], root: str | None = None) -> dict:
    """{"safe": [path, ...], "dangerous": [{"file": path, "reason": ...,
    "line": ..., "matched_text": ...}, ...]} -- "dangerous" carries the
    same reason detail scan_file() returns, keyed under "file" alongside
    it, rather than a bare path list, so a caller (file/selector.py's
    terminal review, or the GUI's file-selection screen) can show a human
    *why* without a second scan_file() call per file.
    """
    results = {"safe": [], "dangerous": []}
    for file_path in file_paths:
        reason = scan_file(file_path, root=root)
        if reason:
            results["dangerous"].append({"file": file_path, **reason})
        else:
            results["safe"].append(file_path)
    return results
