"""scan_file()/scan_files() -- the pattern-based fallback specifically,
since secretlint itself either isn't installed in most environments or (on
Windows, see _scan_with_secretlint()'s own docstring) never actually runs;
every real invocation in this suite exercises the fallback path.
"""

from ziplex import progress_i18n
from ziplex.file.scanner import scan_file, scan_files, _looks_like_a_real_secret


def _write(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_scan_file_returns_none_for_a_clean_file(tmp_path):
    path = tmp_path / "main.py"
    _write(path, "def add(a, b):\n    return a + b\n")
    assert scan_file(str(path)) is None


def test_scan_file_reports_the_matched_line_not_the_whole_file(tmp_path):
    path = tmp_path / "secret.env"
    _write(path, 'NORMAL=fine\nAPI_KEY = "abc123"\nOTHER=fine\n')

    result = scan_file(str(path))

    assert result["line"] == 2
    assert result["matched_text"] == 'API_KEY = "abc123"'
    assert "API_KEY" in result["reason"]


def test_scan_file_matches_the_first_triggering_line(tmp_path):
    # two lines would both match different patterns -- the first one found
    # wins, not an arbitrary one, so the reported line is deterministic.
    # Values long enough (3+ chars) to not also trip the too-short-to-be-a-
    # real-credential guard -- see _looks_like_a_real_secret()'s own test
    # for that guard specifically.
    path = tmp_path / "multi.env"
    _write(path, 'PASSWORD = "hunter2"\nAPI_KEY = "abc123"\n')

    result = scan_file(str(path))
    assert result["line"] == 1
    assert "PASSWORD" in result["reason"]


def test_scan_files_splits_safe_and_dangerous_with_reasons(tmp_path):
    _write(tmp_path / "main.py", "def add(a, b):\n    return a + b\n")
    _write(tmp_path / "secret.env", 'API_KEY = "abc123"\n')

    result = scan_files([str(tmp_path / "main.py"), str(tmp_path / "secret.env")])

    assert result["safe"] == [str(tmp_path / "main.py")]
    assert len(result["dangerous"]) == 1
    entry = result["dangerous"][0]
    assert entry["file"] == str(tmp_path / "secret.env")
    assert entry["line"] == 1
    assert entry["matched_text"] == 'API_KEY = "abc123"'


def test_scan_file_falls_back_to_pattern_when_secretlint_is_unavailable(tmp_path, monkeypatch):
    from ziplex.file import scanner

    monkeypatch.setattr(scanner, "_scan_with_secretlint", lambda path, cwd=None: None)
    path = tmp_path / "secret.env"
    _write(path, 'API_KEY = "abc123"\n')

    result = scan_file(str(path))
    assert result is not None
    assert result["matched_text"] == 'API_KEY = "abc123"'


def test_scan_file_still_scans_a_text_file_that_merely_has_a_media_extension(tmp_path):
    # a media *extension* alone must never waive scanning -- only a file
    # that's actually undecodable as text may. Otherwise a secrets file
    # simply renamed to .mp3 (or a Git LFS pointer file checked in for a
    # real video/image) would silently skip both secretlint and the
    # pattern fallback purely because of its name.
    path = tmp_path / "secrets.mp3"
    _write(path, 'API_KEY = "abc123supersecret"\n')

    result = scan_file(str(path))
    assert result is not None
    assert result["matched_text"] == 'API_KEY = "abc123supersecret"'


def test_scan_file_treats_a_recognized_media_asset_as_always_safe(tmp_path, monkeypatch):
    # neither secretlint nor the pattern fallback should even run against a
    # media file's opaque binary bytes -- proven here by making both blow up
    # if called at all, not just by asserting the final "safe" result
    from ziplex.file import scanner

    def _boom(path, cwd=None):
        raise AssertionError("scan_file must not scan a recognized media asset")

    monkeypatch.setattr(scanner, "_scan_with_secretlint", _boom)
    monkeypatch.setattr(scanner, "_scan_with_pattern", _boom)

    path = tmp_path / "logo.png"
    path.write_bytes(bytes(range(256)))

    assert scan_file(str(path)) is None


# The pattern fallback's false-positive fixes, found dogfooding Ziplex on
# its own repo -- every one of a real pack's 17 flagged files (before this
# fix) turned out to be one of the two shapes below, never an actual
# secret. Unit-tested directly against _looks_like_a_real_secret() rather
# than through scan_file() -- these are about the *value* judgment, not
# the file-scanning plumbing the tests above already cover.

def test_ellipsis_placeholder_is_not_a_real_secret():
    # A doc's own setup instruction ("Requires a `.env` with
    # `GEMINI_API_KEY=...`") tells a *reader* to put their own key there --
    # not a leaked one.
    assert _looks_like_a_real_secret("...", "API_KEY") is False


def test_scan_file_does_not_flag_a_doc_placeholder_glued_to_trailing_punctuation(tmp_path):
    # Real false positive: a Markdown code span's closing backtick (or, in
    # a Korean sentence, a particle glued directly onto it with no space)
    # used to get swept into the captured "value" by a bare `\S+` capture,
    # so the value was never recognized as just "...".
    path = tmp_path / "README.md"
    _write(path, "Requires a `.env` with `GEMINI_API_KEY=...`를 추가하세요.\n")
    assert scan_file(str(path)) is None


def test_unquoted_self_reference_is_not_a_real_secret():
    # "self._explicit_api_key = api_key" -- assigning a variable to
    # another similarly-named variable is a code reference, never a
    # literal credential value, even though "api_key" is shaped exactly
    # like a plausible unquoted .env value on its own.
    assert _looks_like_a_real_secret("api_key", "API_KEY") is False


def test_unquoted_dotted_self_reference_is_not_a_real_secret():
    # "body.gemini_api_key = apiKeyInput.value.trim()" -- the captured
    # value ("apiKeyInput.value.trim") contains the keyword too, just
    # embedded in a longer dotted expression.
    assert _looks_like_a_real_secret("apiKeyInput.value.trim", "API_KEY") is False


def test_unquoted_unrelated_value_is_still_a_real_secret():
    # The self-reference check must not become "reject every bare
    # identifier-shaped unquoted value" -- .env's own convention
    # (API_KEY=abc123, no quotes) has to keep matching, since a real
    # secret's text essentially never happens to spell out the name of the
    # field holding it.
    assert _looks_like_a_real_secret("abc123", "API_KEY") is True


def test_scan_file_does_not_flag_a_code_expression_assigned_to_a_similarly_named_variable(tmp_path):
    path = tmp_path / "provider.py"
    _write(path, "        self._explicit_api_key = api_key\n")
    assert scan_file(str(path)) is None


def test_short_value_is_not_a_real_secret_quoted_or_not():
    # A 1-2 character placeholder ("x", common in tests unrelated to
    # secret-scanning itself) is too short to plausibly be a real
    # credential either way.
    assert _looks_like_a_real_secret('"x"', "API_KEY") is False
    assert _looks_like_a_real_secret("x", "API_KEY") is False


def test_scan_file_reason_follows_progress_lang(tmp_path):
    # A real gap found dogfooding Ziplex's own English-language GUI: this
    # was the one "reason" string in the whole pipeline that stayed
    # hardcoded Korean regardless of progress_i18n's setting (every other
    # print/message site already routes through progress_i18n.pick(), see
    # checkpoint.py/summarizer.py/llm.py) -- misleading specifically because
    # scan_file()'s reason is rendered straight into the GUI's own display
    # (landing.js), not "CLI output" that's allowed to stay Korean by
    # convention. conftest.py's autouse _reset_progress_lang resets the
    # (process-wide) ContextVar around this test, so no manual restore is
    # needed here.
    path = tmp_path / "secret.env"
    _write(path, 'API_KEY = "abc123"\n')

    progress_i18n.set_current("en")
    assert scan_file(str(path))["reason"].startswith("Pattern match:")

    progress_i18n.set_current("ko")
    assert scan_file(str(path))["reason"].startswith("패턴 일치:")


def _fake_secretlint_result(stdout: str):
    class _Result:
        pass
    r = _Result()
    r.stdout = stdout
    return r


def test_scan_with_secretlint_no_message_fallback_follows_progress_lang(tmp_path, monkeypatch):
    # Found alongside the pattern-fallback fix (see
    # test_scan_file_reason_follows_progress_lang above): secretlint's own
    # per-finding "message" is always English (out of this project's
    # control -- see this branch's own comment in scanner.py), but the
    # *fallback* string used when a finding carries no message at all is
    # ours to localize, and used to be hardcoded English unconditionally.
    # secretlint doesn't actually run in this dev/CI environment (see
    # _scan_with_secretlint()'s own documented Windows .cmd-shim gap), so
    # this mocks subprocess.run directly rather than relying on a real
    # secretlint install to exercise the branch at all.
    import subprocess

    stdout = '[{"messages": [{"range": {"start": {"line": 1}}}]}]'  # no "message" key
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _fake_secretlint_result(stdout))
    path = tmp_path / "secret.env"
    _write(path, "irrelevant -- secretlint itself is mocked\n")

    progress_i18n.set_current("en")
    assert scan_file(str(path))["reason"] == "secretlint rule triggered"

    progress_i18n.set_current("ko")
    assert scan_file(str(path))["reason"] == "secretlint 규칙 위반"


def test_scan_with_secretlint_real_message_ignores_progress_lang(tmp_path, monkeypatch):
    # The *other* half of the same branch: when secretlint does report a
    # message, that text is used verbatim regardless of progress_lang --
    # it's secretlint's own English output, not something pick() should
    # ever touch or replace.
    import subprocess

    stdout = '[{"messages": [{"message": "Found a GitHub token", "range": {"start": {"line": 2}}}]}]'
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _fake_secretlint_result(stdout))
    path = tmp_path / "secret.env"
    _write(path, "irrelevant -- secretlint itself is mocked\n")

    progress_i18n.set_current("ko")
    assert scan_file(str(path))["reason"] == "Found a GitHub token"


def test_scan_file_trusts_a_clean_secretlint_result_without_falling_back(tmp_path, monkeypatch):
    # secretlint returning False (ran, found nothing) must NOT trigger the
    # pattern fallback -- only None (secretlint itself couldn't run) should.
    # A file that would otherwise match a fallback pattern stays "safe" here
    # specifically to prove the False/None distinction is respected.
    from ziplex.file import scanner

    monkeypatch.setattr(scanner, "_scan_with_secretlint", lambda path, cwd=None: False)
    path = tmp_path / "secret.env"
    _write(path, 'API_KEY = "abc123"\n')

    assert scan_file(str(path)) is None


def test_scan_file_flags_secrets_the_keyword_equals_pattern_missed(tmp_path):
    # All four scanned as safe before -- the regex fallback is the only
    # scanner that ever runs on Windows (see _scan_with_secretlint()).
    cases = {
        "id_rsa": "-----BEGIN RSA PRIVATE KEY-----\nMIIEowIBAAKCAQEA1234\n-----END RSA PRIVATE KEY-----\n",
        "config.json": '{\n  "api_key": "sk-live-9f8e7d6c5b4a"\n}\n',
        "app.yaml": "db:\n  password: hunter2secret\n",
        "ci.sh": "export GITHUB_TOKEN=ghp_abcdefghijklmnopqrstuvwxyz0123456789\n",
        "aws.py": 'KEY_ID = "AKIAIOSFODNN7EXAMPLE"\n',
    }
    for name, content in cases.items():
        path = tmp_path / name
        _write(path, content)
        assert scan_file(str(path)) is not None, name


def test_scan_file_flags_an_env_password_containing_its_own_field_name(tmp_path):
    # The self-reference filter (for `api_key = api_key` in code) used to
    # drop this real .env password because it contains the word "password".
    path = tmp_path / ".env.local"
    _write(path, "PASSWORD=MyPassword!2024\n")
    result = scan_file(str(path))
    assert result is not None and result["line"] == 1


def test_scan_file_ignores_colon_forms_that_are_not_literals(tmp_path):
    # ":" matching (JSON/YAML) must not flag a type annotation, a dict
    # entry referencing a variable, or a quoted help sentence.
    path = tmp_path / "code.py"
    _write(path, (
        "def login(password: str, api_key: str) -> None:\n"
        "    body = {'api_key': api_key}\n"
        "    hints = {\"gemini_api_key\": \"not set -- uses GEMINI_API_KEY from .env\"}\n"
    ))
    assert scan_file(str(path)) is None


def test_scan_file_ignores_an_env_template_placeholder(tmp_path):
    path = tmp_path / ".env.example"
    _write(path, "API_KEY=your_key_here\nPASSWORD=\n")
    assert scan_file(str(path)) is None


def test_scan_with_secretlint_reads_its_real_output_shape(tmp_path, monkeypatch):
    # Real secretlint JSON: `range` is a [start, end] offset list and the
    # line is under loc.start.line -- reading `range` as a dict used to
    # raise an uncaught AttributeError the moment it reported anything.
    from ziplex.file import scanner

    path = tmp_path / "creds.txt"
    _write(path, "first\nAWS key here\n")
    output = [{"filePath": str(path), "messages": [{
        "message": "found AWS Access Key ID", "range": [6, 18],
        "loc": {"start": {"line": 2, "column": 0}, "end": {"line": 2, "column": 12}},
    }]}]

    class _Result:
        stdout = __import__("json").dumps(output)

    monkeypatch.setattr(scanner.subprocess, "run", lambda *a, **k: _Result())
    result = scanner.scan_file(str(path))
    assert result == {"reason": "found AWS Access Key ID", "line": 2, "matched_text": "AWS key here"}


def test_scan_files_runs_secretlint_from_the_project_root(tmp_path, monkeypatch):
    # secretlint reads .secretlintrc from its working directory -- it used to
    # inherit wherever ziplex was launched, ignoring the project's own config.
    from ziplex.file import scanner

    seen = []
    monkeypatch.setattr(scanner, "_scan_with_secretlint", lambda path, cwd=None: seen.append(cwd) or False)
    path = tmp_path / "a.txt"
    _write(path, "hello\n")

    scan_files([str(path)], root=str(tmp_path))
    assert seen == [str(tmp_path)]
