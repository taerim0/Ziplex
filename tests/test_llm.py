"""GeminiProvider's model resolution -- constructed URL only, no network
calls (checking .url is enough to verify precedence without needing a real
API key or GEMINI_MODEL to reach through to an actual request) -- plus
_resolve_api_key()'s precedence (explicit arg > settings.py's stored key >
GEMINI_API_KEY env var), which the model isn't resolved the same way (see
GeminiProvider.__init__'s own comment on why the key specifically has to
be re-resolved per call instead of cached at construction).
"""

import json

import requests

from ziplex import llm
from ziplex import settings as app_settings


def _model_in_url(provider) -> str:
    return provider.url.split("/models/")[1].split(":")[0]


def test_gemini_provider_defaults_to_default_model_with_no_override(monkeypatch):
    monkeypatch.delenv("GEMINI_MODEL", raising=False)
    assert _model_in_url(llm.GeminiProvider(api_key="x")) == llm.GeminiProvider.DEFAULT_MODEL


def test_gemini_provider_env_var_overrides_the_default(monkeypatch):
    monkeypatch.setenv("GEMINI_MODEL", "gemini-3.5-flash")
    assert _model_in_url(llm.GeminiProvider(api_key="x")) == "gemini-3.5-flash"


def test_gemini_provider_explicit_arg_wins_over_env_var(monkeypatch):
    monkeypatch.setenv("GEMINI_MODEL", "gemini-3.5-flash")
    provider = llm.GeminiProvider(api_key="x", model="gemini-3.7-flash")
    assert _model_in_url(provider) == "gemini-3.7-flash"


def test_resolve_api_key_falls_back_to_env_var_when_nothing_else_set(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setenv("GEMINI_API_KEY", "env-key")
    provider = llm.GeminiProvider()  # no explicit api_key
    assert provider._resolve_api_key() == "env-key"


def test_resolve_api_key_settings_overrides_env_var(monkeypatch, tmp_path):
    monkeypatch.setattr(app_settings, "SETTINGS_PATH", tmp_path / "settings.json")
    monkeypatch.setenv("GEMINI_API_KEY", "env-key")
    app_settings.save_settings({"output_dir": "", "project_output_dirs": {}, "gemini_api_key": "options-key"})

    provider = llm.GeminiProvider()
    assert provider._resolve_api_key() == "options-key"


def test_resolve_api_key_explicit_arg_wins_over_settings(monkeypatch, tmp_path):
    monkeypatch.setattr(app_settings, "SETTINGS_PATH", tmp_path / "settings.json")
    app_settings.save_settings({"output_dir": "", "project_output_dirs": {}, "gemini_api_key": "options-key"})

    provider = llm.GeminiProvider(api_key="explicit-key")
    assert provider._resolve_api_key() == "explicit-key"


def test_resolve_api_key_re_resolves_on_every_call_not_cached(monkeypatch, tmp_path):
    # The whole reason this isn't resolved once in __init__ like the model
    # is -- a single long-lived GeminiProvider instance (llm.py's
    # module-level `_provider`) must pick up a key saved via the Options
    # page mid-session, without needing to be reconstructed.
    monkeypatch.setattr(app_settings, "SETTINGS_PATH", tmp_path / "settings.json")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    provider = llm.GeminiProvider()
    assert provider._resolve_api_key() is None or provider._resolve_api_key() == ""

    app_settings.save_settings({"output_dir": "", "project_output_dirs": {}, "gemini_api_key": "just-saved"})
    assert provider._resolve_api_key() == "just-saved"


class _FakeResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def json(self):
        return self._payload


def test_generate_disables_gemini_thinking_by_default(monkeypatch):
    # Real cost bug reported directly: Gemini's "thinking" mode is on by
    # default for the 2.5+/3.x Flash generation DEFAULT_MODEL/GEMINI_MODEL
    # point at, and none of these prompts (short, single-shot, "JSON only")
    # benefit from it -- confirmed directly against the real API that a
    # trivial request billed 142 hidden thinking tokens (of 150 total) with
    # no thinkingConfig set, and 0 with thinkingBudget: 0. Every request
    # must carry that field regardless of which model ends up handling it.
    captured = {}

    def fake_post(url, json, timeout=None):
        captured["json"] = json
        return _FakeResponse({"candidates": [{"content": {"parts": [{"text": "{}"}]}}]})

    monkeypatch.setattr(llm.requests, "post", fake_post)
    provider = llm.GeminiProvider(api_key="x")

    provider.generate("prompt")

    assert captured["json"]["generationConfig"]["thinkingConfig"]["thinkingBudget"] == 0


def test_generate_retries_past_a_transport_level_exception(monkeypatch):
    # A network blip (connection reset, DNS failure, read timeout) used to
    # propagate straight out of generate() uncaught -- unlike an explicit
    # 503/429 JSON error response, which was already retried. Must be
    # retried the same way instead of crashing the whole pack() run.
    monkeypatch.setattr(llm.time, "sleep", lambda s: None)
    calls = {"n": 0}

    def fake_post(url, json, timeout=None):
        calls["n"] += 1
        if calls["n"] == 1:
            raise requests.exceptions.ConnectionError("simulated network blip")
        return _FakeResponse({"candidates": [{"content": {"parts": [{"text": '{"summary": "ok"}'}]}}]})

    monkeypatch.setattr(llm.requests, "post", fake_post)
    provider = llm.GeminiProvider(api_key="x")

    result = provider.generate("prompt", retry=3)

    assert result == '{"summary": "ok"}'
    assert calls["n"] == 2


def test_gemini_generate_retries_when_response_body_is_not_a_json_object(monkeypatch):
    # Syntactically valid JSON that isn't an object (a bare `null`) used to
    # crash uncaught: `"candidates" in data` raises TypeError for anything
    # but a dict/list/str, escaping generate() entirely instead of being
    # retried the same way a malformed/non-JSON body already was.
    monkeypatch.setattr(llm.time, "sleep", lambda s: None)
    calls = {"n": 0}

    def fake_post(url, json, timeout=None):
        calls["n"] += 1
        if calls["n"] == 1:
            return _FakeResponse(None)
        return _FakeResponse({"candidates": [{"content": {"parts": [{"text": '{"summary": "ok"}'}]}}]})

    monkeypatch.setattr(llm.requests, "post", fake_post)
    provider = llm.GeminiProvider(api_key="x")

    result = provider.generate("prompt", retry=3)

    assert result == '{"summary": "ok"}'
    assert calls["n"] == 2


def test_openai_generate_retries_when_response_body_is_not_a_json_object(monkeypatch):
    # OpenAIProvider is the provider most likely to hit this in practice --
    # it's explicitly meant to point at local servers (Ollama/LM Studio/
    # vLLM/llama.cpp) that can return a non-object body while starting up.
    monkeypatch.setattr(llm.time, "sleep", lambda s: None)
    calls = {"n": 0}

    def fake_post(url, headers=None, json=None, timeout=None):
        calls["n"] += 1
        if calls["n"] == 1:
            return _FakeResponse(None)
        return _FakeResponse({"choices": [{"message": {"content": '{"summary": "ok"}'}}]})

    monkeypatch.setattr(llm.requests, "post", fake_post)
    provider = llm.OpenAIProvider(api_key="x")

    result = provider.generate("prompt", retry=3)

    assert result == '{"summary": "ok"}'
    assert calls["n"] == 2


def test_retry_wait_grows_by_5s_then_caps_at_the_max(monkeypatch):
    # Reported directly as "어색한 로직" -- the retry backoff used to grow
    # unbounded (5 * attempt), which combined with the old "(attempt/max)"
    # display made the printed max look meaningless the moment a caller
    # (e.g. summarizer.py's batch-then-per-file-fallback chain) started a
    # fresh generate() call with its own new counter right after the first
    # one's ran out. The counter is gone from the message entirely now (see
    # the label test below); this is just the backoff formula itself.
    assert [llm._retry_wait(a) for a in range(12)] == [
        5, 10, 15, 20, 25, 30, 35, 40, 45, 50, 50, 50,
    ]


def test_generate_retry_message_has_no_attempt_counter_and_names_the_item(monkeypatch):
    # Two things reported directly: (1) the old "(attempt+1)/{retry}" counter
    # read as broken the moment a fresh generate() call (e.g. summarizer.py's
    # per-file fallback after a failed batch) started its own new count from
    # 1 again -- removed entirely rather than trying to make it "honest"
    # across calls that don't share state; (2) a failing pack gave no way to
    # tell *which* file/item was the problem, just "네트워크 오류... 재시도".
    monkeypatch.setattr(llm.time, "sleep", lambda s: None)

    def fake_post(url, json, timeout=None):
        raise requests.exceptions.ConnectionError("simulated network blip")

    monkeypatch.setattr(llm.requests, "post", fake_post)
    provider = llm.GeminiProvider(api_key="x")

    messages = []
    monkeypatch.setattr("builtins.print", lambda *a, **k: messages.append(" ".join(str(x) for x in a)))

    provider.generate("prompt", retry=2, label="main.py")

    assert any("[main.py]" in m for m in messages)
    assert not any("/2)" in m or "(1/" in m or "(2/" in m for m in messages)


def test_generate_omits_the_label_bracket_when_none_given(monkeypatch):
    monkeypatch.setattr(llm.time, "sleep", lambda s: None)

    def fake_post(url, json, timeout=None):
        raise requests.exceptions.ConnectionError("simulated network blip")

    monkeypatch.setattr(llm.requests, "post", fake_post)
    provider = llm.GeminiProvider(api_key="x")

    messages = []
    monkeypatch.setattr("builtins.print", lambda *a, **k: messages.append(" ".join(str(x) for x in a)))

    provider.generate("prompt", retry=1)

    assert not any("[" in m for m in messages)


def test_generate_retries_past_a_non_json_response(monkeypatch):
    # A proxy returning an HTML error page (not JSON at all) raises
    # JSONDecodeError from response.json() -- same treatment as a
    # transport-level exception, not an uncaught crash.
    monkeypatch.setattr(llm.time, "sleep", lambda s: None)
    calls = {"n": 0}

    class _BadJsonResponse:
        def json(self):
            raise json.JSONDecodeError("bad", "not json", 0)

    def fake_post(url, json, timeout=None):
        calls["n"] += 1
        if calls["n"] == 1:
            return _BadJsonResponse()
        return _FakeResponse({"candidates": [{"content": {"parts": [{"text": '{"summary": "ok"}'}]}}]})

    monkeypatch.setattr(llm.requests, "post", fake_post)
    provider = llm.GeminiProvider(api_key="x")

    result = provider.generate("prompt", retry=3)

    assert result == '{"summary": "ok"}'
    assert calls["n"] == 2


def _capture_generate(monkeypatch):
    """Monkeypatches llm.generate() to record every prompt it's called with,
    returning a fixed benign response so the caller doesn't need a real
    provider -- for unit-testing prompt *construction* (analyze_*()) in
    isolation from any provider's transport/retry logic.
    """
    captured = []

    def fake_generate(prompt, retry=5, label=""):
        captured.append(prompt)
        return "{}"

    monkeypatch.setattr(llm, "generate", fake_generate)
    return captured


def test_analyze_file_summary_default_lang_asks_for_english(monkeypatch):
    captured = _capture_generate(monkeypatch)
    llm.analyze_file_summary("main.py", ["add(a, b)"], [])
    assert "English" in captured[0]


def test_analyze_file_summary_lang_ko_asks_for_korean(monkeypatch):
    captured = _capture_generate(monkeypatch)
    llm.analyze_file_summary("main.py", ["add(a, b)"], [], lang="ko")
    assert "Korean" in captured[0]


def test_analyze_text_summary_lang_ko_asks_for_korean(monkeypatch):
    captured = _capture_generate(monkeypatch)
    llm.analyze_text_summary("README.md", "hello", lang="ko")
    assert "Korean" in captured[0]


def test_analyze_batch_summaries_lang_ko_asks_for_korean(monkeypatch):
    captured = _capture_generate(monkeypatch)
    llm.analyze_batch_summaries([{"file": "main.py", "signatures": ["add()"], "dependencies": []}], lang="ko")
    assert "Korean" in captured[0]


def test_analyze_rules_lang_ko_asks_for_korean(monkeypatch):
    captured = _capture_generate(monkeypatch)
    llm.analyze_rules({"main.py": ["add()"]}, lang="ko")
    assert "Korean" in captured[0]


def test_analyze_prompt_lang_ko_asks_for_korean(monkeypatch):
    captured = _capture_generate(monkeypatch)
    llm.analyze_prompt("myproj", [], [], lang="ko")
    assert "Korean" in captured[0]


def test_analyze_folder_summaries_lang_ko_asks_for_korean(monkeypatch):
    captured = _capture_generate(monkeypatch)
    llm.analyze_folder_summaries({"src": ["a.py: does a"]}, lang="ko")
    assert "Korean" in captured[0]


def test_analyze_prompt_instructs_to_focus_on_purpose_not_style(monkeypatch):
    # Real, reported bug: analyze_prompt() used to be called with an
    # always-empty `architecture` list (packager.py), so the only real
    # signal it ever had was a list of coding-style rules -- structurally
    # guaranteed to read like a style blurb rather than a "what is this
    # project" summary. Fixed at both the data level (packager.py now
    # populates architecture for real) and the instruction level (this
    # test) -- the prompt itself has to say not to center the summary on
    # style, since a real architecture signal alone doesn't stop the model
    # from still leaning on the rules list if nothing tells it not to.
    captured = _capture_generate(monkeypatch)
    llm.analyze_prompt("myproj", ["src/main.py: entry point"], ["methods use camelCase"])
    assert "do NOT center the summary on coding style" in captured[0]


def test_unrecognized_lang_falls_back_to_english_instruction(monkeypatch):
    captured = _capture_generate(monkeypatch)
    llm.analyze_file_summary("main.py", ["add(a, b)"], [], lang="fr")
    assert "English" in captured[0]


def _capture_generate_labels(monkeypatch):
    """Like _capture_generate() but records the `label` each call was made
    with -- for asserting analyze_*() passes through the right item name
    for the retry/error log's [label] prefix (see llm._label_prefix()),
    without touching the existing prompt-only tests above.
    """
    labels = []

    def fake_generate(prompt, retry=5, label=""):
        labels.append(label)
        return "{}"

    monkeypatch.setattr(llm, "generate", fake_generate)
    return labels


def test_analyze_file_summary_labels_the_retry_with_the_file_path(monkeypatch):
    labels = _capture_generate_labels(monkeypatch)
    llm.analyze_file_summary("src/main.py", ["add(a, b)"], [])
    assert labels == ["src/main.py"]


def test_analyze_text_summary_labels_the_retry_with_the_file_path(monkeypatch):
    labels = _capture_generate_labels(monkeypatch)
    llm.analyze_text_summary("README.md", "hello")
    assert labels == ["README.md"]


def test_analyze_batch_summaries_labels_the_retry_with_every_file_in_the_batch(monkeypatch):
    labels = _capture_generate_labels(monkeypatch)
    llm.analyze_batch_summaries([
        {"file": "a.py", "signatures": [], "dependencies": []},
        {"file": "b.py", "signatures": [], "dependencies": []},
    ])
    assert labels == ["a.py, b.py"]


def test_analyze_rules_labels_the_retry_with_a_fixed_name(monkeypatch):
    labels = _capture_generate_labels(monkeypatch)
    llm.analyze_rules({"main.py": ["add()"]})
    assert labels == ["코딩 룰"]


def test_analyze_prompt_labels_the_retry_with_a_fixed_name(monkeypatch):
    labels = _capture_generate_labels(monkeypatch)
    llm.analyze_prompt("myproj", [], [])
    assert labels == ["AI 가이드"]


def test_analyze_folder_summaries_labels_the_retry_with_a_fixed_name(monkeypatch):
    labels = _capture_generate_labels(monkeypatch)
    llm.analyze_folder_summaries({"src": ["a.py: does a"]})
    assert labels == ["폴더 요약"]


# Real bug reported directly: packing this same repo scored ~17 low-confidence
# files in English but ~63 in Korean via confidence.py's word-overlap
# heuristic -- not because the Korean summaries were actually worse, but
# because an English summary naturally reuses identifier-derived words (no
# literal quote needed) while a Korean summary uses genuinely different
# words for the same meaning, leaving no shared vocabulary for the heuristic
# to find unless the model quotes the identifier verbatim. These three
# prompts (the ones confidence.py's overlap is scored against) now ask the
# model to keep identifiers untranslated so that signal exists regardless of
# output language -- see llm.py's _IDENTIFIER_FIDELITY_NOTE.
def test_analyze_file_summary_asks_to_keep_identifiers_untranslated(monkeypatch):
    captured = _capture_generate(monkeypatch)
    llm.analyze_file_summary("main.py", ["add(a, b)"], [], lang="ko")
    assert "do not translate" in captured[0]


def test_analyze_text_summary_asks_to_keep_identifiers_untranslated(monkeypatch):
    captured = _capture_generate(monkeypatch)
    llm.analyze_text_summary("README.md", "hello", lang="ko")
    assert "do not translate" in captured[0]


def test_analyze_batch_summaries_asks_to_keep_identifiers_untranslated(monkeypatch):
    captured = _capture_generate(monkeypatch)
    llm.analyze_batch_summaries([{"file": "main.py", "signatures": ["add()"], "dependencies": []}], lang="ko")
    assert "do not translate" in captured[0]


def test_generate_gives_up_gracefully_after_repeated_transport_failures(monkeypatch):
    # Exhausting every retry on transport failures alone (never even
    # reaching a real HTTP response) must still return the same "{}"
    # give-up sentinel every other exhausted-retry path returns, not
    # propagate the underlying exception to the caller.
    monkeypatch.setattr(llm.time, "sleep", lambda s: None)

    def always_fails(url, json, timeout=None):
        raise requests.exceptions.Timeout("simulated timeout")

    monkeypatch.setattr(llm.requests, "post", always_fails)
    provider = llm.GeminiProvider(api_key="x")

    assert provider.generate("prompt", retry=2) == "{}"
