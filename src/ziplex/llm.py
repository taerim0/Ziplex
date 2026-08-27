import json
import os
import re
import time
from typing import Protocol

import requests
from dotenv import load_dotenv

from . import settings as app_settings

load_dotenv()

# Applied to every provider's requests.post() call below. Without this,
# requests has no default timeout at all -- a network path that's blocked
# or filtered (a corporate firewall/proxy silently dropping the connection,
# a captive network) leaves the call hanging on the socket read forever,
# with no exception ever raised. That skips the retry-with-backoff path
# entirely (nothing to catch), so a stuck request looks identical to a
# process that's simply frozen: no retry log, no completion log, nothing --
# a real case reported from an external user's environment where "LLM
# analysis" started and never printed a single per-file completion line
# afterward, not even a warning. requests.exceptions.Timeout is already a
# RequestException subclass, so raising it here needs no new except clause
# -- it flows straight into the existing transient-failure retry below.
REQUEST_TIMEOUT = 60

# Supported packed-content languages -- what a summary/rule/AI-guide value
# actually gets *written in*, independent of the GUI's own display-language
# switcher (js/i18n.js, a fixed dictionary translating the GUI's own chrome,
# not anything an LLM produces). "en" is both the default and the
# recommended choice (cli.py's --lang, the GUI pack form's language select)
# -- these prompts, this docstring, and every other piece of Ziplex's own
# documentation are written in English, so English output is what's actually
# been exercised the most; "ko" is supported but newer. Deliberately just
# these two for now, not a free-text field -- see the analyze_*() functions
# below for where this is used, and packager.py's STRUCTURAL_ONLY_NOTE/
# FORMAT_NOTES and summarizer.SUMMARY_FAILED_PLACEHOLDERS for the fixed
# (non-LLM) strings that follow the same `lang` choice for consistency.
LANGUAGE_NAMES: dict[str, str] = {"en": "English", "ko": "Korean"}


def _lang_name(lang: str) -> str:
    """Display name for a prompt's output-language instruction -- falls back
    to English for anything not in LANGUAGE_NAMES (an unrecognized value
    should never silently produce a prompt asking for no language at all)."""
    return LANGUAGE_NAMES.get(lang, LANGUAGE_NAMES["en"])


# Applied to every provider's backoff wait between retries (5s, 10s, 15s,
# ... capped here rather than growing unbounded) -- reported directly as
# "어색한 로직": the printed "(attempt/max)" counter used to make the retry
# loop's own cap look meaningless the moment a batch summary request
# exhausted it and immediately handed off to a fresh per-file fallback call
# with its own brand-new "(1/5)" counter, reading as if the loop had simply
# ignored its own stated limit. Removing the counter from the message (see
# _label_prefix() below, used instead to say *what's* being retried) and
# capping the wait time here is the fix that was actually asked for -- with
# today's retry=5 default this cap never engages (5*5=25s, under 50 either
# way), but it's what keeps a possible future increase to `retry` from
# producing an absurd wait (5*20=100s) for what's still meant to be "the
# API is having a transient blip," not an hours-long stall.
MAX_RETRY_WAIT_SECONDS = 50


def _retry_wait(attempt: int) -> int:
    return min(5 * (attempt + 1), MAX_RETRY_WAIT_SECONDS)


def _label_prefix(label: str) -> str:
    """Prefixes a retry/error log line with which item the call is actually
    for -- a single file's relative name (analyze_file_summary/
    analyze_text_summary), a batch's file list (analyze_batch_summaries),
    or a fixed name for the two non-per-file calls ("코딩 룰"/"AI 가이드") --
    reported directly as missing: watching a pack fail with no indication
    of *which* file was the problem made it hard to tell whether one bad
    file was blocking everything or the whole API was down. Empty string
    (analyze_relationships, dead code with no caller today) means no
    prefix at all, not a stray "[]".
    """
    return f"[{label}] " if label else ""


# Appended to every per-file summary prompt (analyze_file_summary/
# analyze_text_summary/analyze_batch_summaries) -- confidence.py's
# estimate_confidence() scores a summary by word-overlap against the file's
# own extracted signatures, and that overlap only happens "for free" in
# English: an English summary naturally reuses identifier-derived words
# (describing `parse_input()` as "parses input" overlaps "parse"/"input"
# even with no literal quote) because the summary vocabulary and the
# identifier vocabulary are the same language. A Korean summary of the same
# function uses genuinely different words (파싱하다/입력, not "parse"/
# "input") for the same meaning -- there's no shared-vocabulary overlap to
# find regardless of how accurate the summary is, unless the LLM happens to
# quote the identifier verbatim. Reported directly: packing this repo's own
# CLAUDE.md-documented codebase flagged ~17 low-confidence files in English
# but ~63 in Korean, not because the Korean summaries were worse but because
# the scoring heuristic's whole signal (shared vocabulary) structurally
# doesn't exist across languages. This instruction asks the model to keep
# identifiers untranslated so that signal exists again by construction --
# also a real readability win on its own (a Korean reader can still map a
# summary back to the actual function/class name it's describing).
_IDENTIFIER_FIDELITY_NOTE = (
    "When you mention a specific function, class, method, variable, or file "
    "name, keep it exactly as written in the source (do not translate or "
    "transliterate it)."
)


def _clean_json(text: str) -> str:
    # strip markdown code fences
    text = text.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        lines = [l for l in lines if not l.startswith("```")]
        text = "\n".join(lines)
    return text.strip()


class LLMProvider(Protocol):
    """The contract every provider must satisfy to be usable via generate().

    Structural (Protocol), not a base class to inherit from: a provider just
    needs a matching generate() method, nothing registers or declares
    conformance. Kept minimal on purpose -- when a second provider actually
    gets added, this is the seam to grow (e.g. auth/config passed through
    __init__ can differ freely per provider; only generate()'s shape matters
    here) and the natural point to split providers into their own module
    (or package, if there end up being several) instead of stacking classes
    with very different error-handling/retry logic into this one file.
    """

    def generate(self, prompt: str, retry: int = 5, label: str = "") -> str: ...


class GeminiProvider:
    """Gemini REST API provider.

    To add another LLM, implement generate(prompt, retry) -> str like this
    class (see LLMProvider above) and register it in PROVIDERS below with one
    line. The analyze_* functions only ever call the module-level generate(),
    regardless of which provider is active.
    """

    # "-latest" is a floating alias, not a pinned release -- it can point at
    # whatever's newest (and least provisioned) at any given moment. Verified
    # directly during a real 503-frequency investigation: gemini-flash-latest
    # returned 503 on 1 of 3 back-to-back calls while gemini-3.5-flash and
    # gemini-3.7-flash (pinned point releases available at the same moment)
    # returned 200 on all 3 -- consistent with the user's own independent
    # observation that Google AI Studio showed the same 503s for this model,
    # i.e. genuine upstream capacity, not something about our prompts/request
    # pattern. A snapshot, not a permanent verdict -- capacity shifts over
    # time -- so this stays the shipped default (unchanged, since it's what
    # most users already have working) rather than being swapped outright;
    # GEMINI_MODEL lets it be overridden without a code change instead.
    DEFAULT_MODEL = "gemini-flash-latest"

    def __init__(self, api_key: str | None = None, model: str | None = None):
        # Neither the key nor the model is resolved to a final value here
        # anymore -- both stay re-resolved on every call (_resolve_api_key()/
        # the url property below), not baked in once at construction time.
        # The model used to be construction-time-only (a GUI Options-page
        # override for it didn't exist yet), which meant the module-level
        # `_provider` singleton (built once at import, living for the whole
        # GUI server process) could never pick up a model saved after that
        # -- confirmed as a real bug (2026-08-24): an external user who only
        # entered an API key via the GUI stayed stuck on DEFAULT_MODEL
        # (a floating "-latest" alias with a documented history of 503s,
        # see that constant's own comment) with no way to switch away from
        # it short of an env var on their own machine, which a GUI-only
        # user has no reason to know exists.
        self._explicit_api_key = api_key
        self._explicit_model = model

    @property
    def url(self) -> str:
        # Precedence: an explicit constructor arg (programmatic/test
        # callers) wins over settings.py's stored model (the Options page)
        # wins over GEMINI_MODEL (a user's own .env override) wins over
        # DEFAULT_MODEL.
        resolved_model = (
            self._explicit_model
            or app_settings.resolve_gemini_model()
            or os.getenv("GEMINI_MODEL")
            or self.DEFAULT_MODEL
        )
        return (
            f"https://generativelanguage.googleapis.com/v1beta/models/"
            f"{resolved_model}:generateContent"
        )

    def _resolve_api_key(self) -> str | None:
        """Re-resolved on every generate() call, not cached at __init__ time
        the way the model is -- llm.py's module-level `_provider` singleton
        (see below) is built once at import and stays alive for the whole
        process, so a key entered later via the GUI's Options page
        (settings.py, GUI-editable at runtime) needs to take effect on the
        very next pack, not only after a restart the way an env var change
        would require anyway. Precedence: an explicit constructor arg
        (programmatic/test callers, e.g. GeminiProvider(api_key="x")) wins
        over settings.py's stored key (the Options page) wins over the
        GEMINI_API_KEY env var / .env (the pre-existing CLI/power-user
        path, unaffected either way if neither of the first two is set).
        """
        if self._explicit_api_key:
            return self._explicit_api_key
        stored = app_settings.resolve_gemini_api_key()
        if stored:
            return stored
        return os.getenv("GEMINI_API_KEY")

    def generate(self, prompt: str, retry: int = 5, label: str = "") -> str:
        api_key = self._resolve_api_key()
        prefix = _label_prefix(label)
        for attempt in range(retry):
            try:
                response = requests.post(f"{self.url}?key={api_key}", json={
                    "contents": [{"parts": [{"text": prompt}]}],
                    # Every prompt here is a short, single-shot extraction
                    # task (summarize this file in one line, list these
                    # rules) with a "JSON only" answer -- none of it benefits
                    # from Gemini's extended "thinking" mode, which is on by
                    # default for the 2.5+/3.x Flash generation this
                    # project's own DEFAULT_MODEL/GEMINI_MODEL point at.
                    # Confirmed directly against the real API (2026-08-26,
                    # reported by a user whose AI Studio cost jumped ~10x
                    # with no code or usage-pattern change on their end,
                    # traced to exactly this): a trivial one-word request
                    # billed usageMetadata.thoughtsTokenCount = 142 (of a
                    # 150-token total) with no thinkingConfig set, and 0
                    # (field absent, totalTokenCount = 8) with
                    # thinkingBudget: 0 -- since `-latest` is a floating
                    # alias (see DEFAULT_MODEL's own comment), the model it
                    # actually points to -- and therefore whether thinking
                    # is on by default -- can change with no code change on
                    # this end at all. Every model reachable through the API
                    # at the time of this fix (checked directly) is from a
                    # thinking-capable generation -- both older, non-thinking
                    # Flash releases tried during this investigation came
                    # back 404 (sunset) -- so there's no live model left to
                    # confirm this field is harmlessly ignored elsewhere
                    # rather than rejected; revisit if a future model
                    # actually errors on it.
                    "generationConfig": {"thinkingConfig": {"thinkingBudget": 0}},
                }, timeout=REQUEST_TIMEOUT)
                data = response.json()
            except (requests.exceptions.RequestException, json.JSONDecodeError) as e:
                # A transport-level failure (DNS, connection reset, read
                # timeout) or a non-JSON response body (a proxy's HTML error
                # page) is just as transient as an explicit 503/429 from the
                # API itself -- retried the same way, rather than propagating
                # straight out of generate() uncaught. Left uncaught, this
                # used to skip every caller's checkpoint-on-failure path
                # (summarizer.py's thread pool, packager.py's rules/prompt
                # calls, checkpoint.handle_llm_failure()) entirely, crashing
                # the whole pack() run and losing all extraction work done
                # so far instead of saving a resumable checkpoint first.
                wait = _retry_wait(attempt)
                print(f"  ⚠️  {prefix}네트워크 오류 ({e.__class__.__name__}), {wait}초 후 재시도")
                time.sleep(wait)
                continue

            if "candidates" in data:
                text = data["candidates"][0]["content"]["parts"][0]["text"]
                return _clean_json(text)

            error_code = data.get("error", {}).get("code", 0)
            error_msg = data.get("error", {}).get("message", "unknown")

            if error_code in [503, 429]:
                wait = _retry_wait(attempt)
                print(f"  ⚠️  {prefix}서버 과부하, {wait}초 후 재시도")
                time.sleep(wait)
                continue

            print(f"  ❌ {prefix}API 에러: {error_msg}")
            break

        return "{}"


class OpenAIProvider:
    """OpenAI's Chat Completions request/response shape -- and, since that
    shape is the de facto standard almost every other provider also speaks
    (Ollama, LM Studio, vLLM, llama.cpp's own server, OpenRouter, Groq, and
    real OpenAI itself), this one class covers all of them via a
    configurable base_url rather than needing a dedicated class per vendor.
    A local model (Gemma, Llama, Mistral, ...) served through any of those
    tools works the same way: point base_url at wherever it's listening
    (e.g. http://localhost:11434/v1 for Ollama) and set model to whatever
    name that server expects -- no code change, just config.
    """

    DEFAULT_BASE_URL = "https://api.openai.com/v1"
    DEFAULT_MODEL = "gpt-4o-mini"

    def __init__(self, api_key: str | None = None, base_url: str | None = None, model: str | None = None):
        # api_key stays unresolved here, same reasoning as GeminiProvider's
        # own _explicit_api_key -- see _resolve_api_key() below. base_url/
        # model aren't expected to change mid-session the way a credential
        # might, so (like GeminiProvider's model) they're resolved once here.
        self._explicit_api_key = api_key
        self.base_url = (
            base_url or app_settings.resolve_openai_base_url() or os.getenv("OPENAI_BASE_URL") or self.DEFAULT_BASE_URL
        ).rstrip("/")
        self.model = model or app_settings.resolve_openai_model() or os.getenv("OPENAI_MODEL") or self.DEFAULT_MODEL

    def _resolve_api_key(self) -> str | None:
        # Same three-tier precedence as GeminiProvider._resolve_api_key():
        # explicit constructor arg > settings.py's stored key > env var.
        # Unlike Gemini, a missing key isn't necessarily a dead end -- most
        # local servers (Ollama, LM Studio) don't check it at all, so
        # generate() below sends no Authorization header rather than one
        # with a literal "None" in it.
        if self._explicit_api_key:
            return self._explicit_api_key
        stored = app_settings.resolve_openai_api_key()
        if stored:
            return stored
        return os.getenv("OPENAI_API_KEY")

    def generate(self, prompt: str, retry: int = 5, label: str = "") -> str:
        api_key = self._resolve_api_key()
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        body = {"model": self.model, "messages": [{"role": "user", "content": prompt}]}
        prefix = _label_prefix(label)

        for attempt in range(retry):
            try:
                response = requests.post(
                    f"{self.base_url}/chat/completions", headers=headers, json=body, timeout=REQUEST_TIMEOUT
                )
                data = response.json()
            except (requests.exceptions.RequestException, json.JSONDecodeError) as e:
                # Same transient-failure treatment as GeminiProvider's own
                # generate() -- see that method's comment for why.
                wait = _retry_wait(attempt)
                print(f"  ⚠️  {prefix}네트워크 오류 ({e.__class__.__name__}), {wait}초 후 재시도")
                time.sleep(wait)
                continue

            if response.status_code == 200 and "choices" in data:
                text = data["choices"][0]["message"]["content"]
                return _clean_json(text)

            # Checked on the HTTP status, not a response body field, unlike
            # GeminiProvider's error.code -- an OpenAI-compatible error body
            # commonly carries a string error.type ("rate_limit_exceeded"),
            # not a numeric code, so the status line is the reliable signal
            # across every backend this class might be pointed at.
            if response.status_code in (429, 500, 502, 503, 504):
                wait = _retry_wait(attempt)
                print(f"  ⚠️  {prefix}서버 과부하, {wait}초 후 재시도")
                time.sleep(wait)
                continue

            error_msg = data.get("error", {}).get("message", "unknown") if isinstance(data, dict) else "unknown"
            print(f"  ❌ {prefix}API 에러: {error_msg}")
            break

        return "{}"


class ClaudeProvider:
    """Anthropic's Messages API -- a different shape from both Gemini and
    the OpenAI-compatible family above (an x-api-key header instead of a
    Bearer token or a ?key= query param, a required max_tokens, and
    response text at content[0].text rather than candidates[0]... or
    choices[0]...), so it gets its own class rather than reusing
    OpenAIProvider with a different base_url.
    """

    DEFAULT_MODEL = "claude-sonnet-4-5"
    MAX_TOKENS = 4096
    API_URL = "https://api.anthropic.com/v1/messages"

    def __init__(self, api_key: str | None = None, model: str | None = None):
        self._explicit_api_key = api_key
        self.model = model or app_settings.resolve_claude_model() or os.getenv("CLAUDE_MODEL") or self.DEFAULT_MODEL

    def _resolve_api_key(self) -> str | None:
        # Same three-tier precedence as the other two providers'
        # _resolve_api_key(). Two env var names checked (ANTHROPIC_API_KEY
        # first) since that's the name Anthropic's own SDK/docs use --
        # CLAUDE_API_KEY stays as a fallback for anyone who already set
        # that instead, consistent with this project's own GEMINI_API_KEY
        # naming for its other providers.
        if self._explicit_api_key:
            return self._explicit_api_key
        stored = app_settings.resolve_claude_api_key()
        if stored:
            return stored
        return os.getenv("ANTHROPIC_API_KEY") or os.getenv("CLAUDE_API_KEY")

    def generate(self, prompt: str, retry: int = 5, label: str = "") -> str:
        api_key = self._resolve_api_key()
        headers = {
            "x-api-key": api_key or "",
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }
        body = {
            "model": self.model,
            "max_tokens": self.MAX_TOKENS,
            "messages": [{"role": "user", "content": prompt}],
        }
        prefix = _label_prefix(label)

        for attempt in range(retry):
            try:
                response = requests.post(self.API_URL, headers=headers, json=body, timeout=REQUEST_TIMEOUT)
                data = response.json()
            except (requests.exceptions.RequestException, json.JSONDecodeError) as e:
                wait = _retry_wait(attempt)
                print(f"  ⚠️  {prefix}네트워크 오류 ({e.__class__.__name__}), {wait}초 후 재시도")
                time.sleep(wait)
                continue

            if response.status_code == 200 and "content" in data:
                text = data["content"][0]["text"]
                return _clean_json(text)

            # 529 is Anthropic's own overloaded_error status, alongside the
            # usual 429/5xx set every other provider here also retries on.
            if response.status_code in (429, 500, 502, 503, 504, 529):
                wait = _retry_wait(attempt)
                print(f"  ⚠️  {prefix}서버 과부하, {wait}초 후 재시도")
                time.sleep(wait)
                continue

            error_msg = data.get("error", {}).get("message", "unknown") if isinstance(data, dict) else "unknown"
            print(f"  ❌ {prefix}API 에러: {error_msg}")
            break

        return "{}"


class MockProvider:
    """Deterministic, network-free provider for fast local iteration and
    integration tests -- a live worked example of the "implement generate()
    and register it" seam GeminiProvider's docstring describes.

    Every real analyze_* call in this file ends its prompt with a one-line
    example of the JSON shape it wants (e.g. `{"summary": "..."}`); this
    just pattern-matches on which field name shows up to hand back a fixed,
    valid response of the right shape, so a full `pack()` run actually
    completes end to end -- checkpointing, parallel summaries, rules/prompt
    generation, token counting -- without ever making a network call or
    waiting out a retry/backoff loop. Content is fixed and not meant to be
    realistic; this validates that the pipeline is wired together correctly,
    not that any particular project's summaries read well. See CLAUDE.md /
    tests/test_pack_integration.py for how to select it.
    """

    def generate(self, prompt: str, retry: int = 5, label: str = "") -> str:
        if '"rules"' in prompt:
            return '{"rules": ["mock rule: methods use camelCase"]}'
        if '"relationships"' in prompt:
            return '{"relationships": {}}'
        if '"prompt"' in prompt:
            return '{"prompt": "Mock AI guide for local testing."}'
        if '"summaries"' in prompt:
            # analyze_batch_summaries() -- echo back every "File: <name>" line
            # it wrote into the prompt, same fixed summary for each, so a
            # batched pack() run gets one entry per file the same way a
            # real batched Gemini response would.
            names = re.findall(r"^File: (.+)$", prompt, re.MULTILINE)
            return json.dumps({"summaries": {name: "Mock summary for local testing." for name in names}})
        if re.search(r"^Folder: ", prompt, re.MULTILINE):
            # analyze_folder_summaries() -- same "echo back the real keys the
            # prompt wrote in" idea as the batch-summaries case above, since
            # packager.py looks up this response by each real folder path.
            folders = re.findall(r"^Folder: (.+)$", prompt, re.MULTILINE)
            return json.dumps({folder: "Mock folder summary for local testing." for folder in folders})
        # analyze_file_summary and analyze_text_summary both want this shape
        return '{"summary": "Mock summary for local testing."}'


# Registry of supported providers. To add a new LLM, add a class here and let
# LLM_PROVIDER select it (e.g. PROVIDERS["claude"] = ClaudeProvider). Select
# a non-default provider by setting LLM_PROVIDER before the process starts
# (it's read once, at import time, into the module-level _provider below) --
# e.g. `LLM_PROVIDER=mock ziplex pack ...` for an instant,
# network-free run. Tests instead monkeypatch the _provider instance
# directly, since by the time a test runs, this module may already have been
# imported (and _provider constructed) by an earlier test file.
PROVIDERS: dict[str, type[LLMProvider]] = {
    "gemini": GeminiProvider,
    "openai": OpenAIProvider,
    "claude": ClaudeProvider,
    "mock": MockProvider,
}

_provider: LLMProvider = PROVIDERS[os.getenv("LLM_PROVIDER", "gemini")]()


def _active_provider() -> LLMProvider:
    """Which provider actually handles the next generate() call -- settings.py's
    stored choice (the GUI Options page's provider selector) if one's been
    made, else `_provider` above (the process-lifetime default selected via
    LLM_PROVIDER at import time -- what LLM_PROVIDER=mock and every existing
    test that does `monkeypatch.setattr(llm, "_provider", ...)` already
    relies on, so both keep working unchanged when settings.py has no
    provider choice stored, which is every environment except a real GUI
    session that used the selector).

    Re-resolved on every call rather than cached, same reasoning as
    GeminiProvider._resolve_api_key(): a provider switched in the GUI needs
    to take effect on the very next pack, not after a restart. Constructing
    a fresh instance per call is cheap -- each provider's own __init__ just
    resolves config strings, no network -- and each provider's generate()
    still re-resolves its own key fresh underneath regardless, so this adds
    no new staleness of its own.
    """
    name = app_settings.resolve_llm_provider_name()
    if name and name in PROVIDERS:
        return PROVIDERS[name]()
    return _provider


def generate(prompt: str, retry: int = 5, label: str = "") -> str:
    """Delegates to the currently active LLM provider. Thread-safe (provider state is read-only).

    label identifies which item this call is for (a file's relative name, a
    batch's file list, "코딩 룰", "AI 가이드") purely for the retry/error log
    lines a provider's generate() prints -- reported directly as missing:
    a failing pack gave no indication of *which* file was the problem.
    """
    return _active_provider().generate(prompt, retry=retry, label=label)


def analyze_file_summary(file_path: str, signatures: list[str], dependencies: list[str], lang: str = "en") -> str:
    prompt = f"""
Based on the file info below, summarize this file's role in one line.
Write the "summary" value in {_lang_name(lang)}. Keep the JSON key itself in English.
{_IDENTIFIER_FIDELITY_NOTE}
Respond with JSON only, nothing else.

File: {file_path}
Function signatures: {signatures}
Dependencies: {dependencies}

{{"summary": "..."}}
"""
    return generate(prompt, label=file_path)


def analyze_text_summary(file_path: str, content: str, lang: str = "en") -> str:
    prompt = f"""
Based on the file content below, summarize this file's role in one line.
Write the "summary" value in {_lang_name(lang)}. Keep the JSON key itself in English.
{_IDENTIFIER_FIDELITY_NOTE}
Respond with JSON only, nothing else.

File: {file_path}
Content:
{content[:500]}

{{"summary": "..."}}
"""
    return generate(prompt, label=file_path)


def analyze_batch_summaries(items: list[dict], lang: str = "en") -> str:
    """Same one-line-per-file summary task as analyze_file_summary() /
    analyze_text_summary(), but for several files in a single request.

    Cuts the *request count* a first pack makes, roughly by len(items) --
    which is what actually drives the 429/503 backoff above in practice
    (hit repeatedly in this session on even a 12-file toy project), even
    though total token volume sent to Gemini is about the same either way.

    items: [{"file": name, "signatures": [...], "dependencies": [...],
    "content": "..."}, ...]. Per item, signatures/dependencies are used when
    either is non-empty (mirrors analyze_file_summary); otherwise content is
    used, truncated the same 500 chars as analyze_text_summary(). `name`
    should be a short, stable identifier (packager.py uses the file's
    relative key, not its absolute path) -- it's both what's shown to the
    model and the exact key the response is expected to echo back, so the
    caller can match summaries to files by string equality. A model that
    renames/drops a key just means that one item comes back unmatched; see
    packager.py's per-file fallback for what happens then.
    """
    parts = []
    names = []
    for item in items:
        names.append(item["file"])
        if item.get("signatures") or item.get("dependencies"):
            parts.append(
                f"File: {item['file']}\n"
                f"Function signatures: {item['signatures']}\n"
                f"Dependencies: {item['dependencies']}"
            )
        else:
            parts.append(
                f"File: {item['file']}\n"
                f"Content:\n{item.get('content', '')[:500]}"
            )
    joined = "\n\n".join(parts)

    prompt = f"""
Based on the file info below, summarize each file's role in one line.
Write every summary value in {_lang_name(lang)}. Keep JSON keys (including each
file name) in their original form -- only the summary text itself is
translated.
{_IDENTIFIER_FIDELITY_NOTE}
Respond with JSON only, nothing else.

{joined}

{{"summaries": {{"<file>": "...", "<file>": "..."}}}}
"""
    return generate(prompt, label=", ".join(names))


def analyze_rules(signatures_map: dict, lang: str = "en") -> str:
    prompt = f"""
Analyze the function signature patterns of the project below
and extract its implicit coding rules.
Write each rule in {_lang_name(lang)}. Keep the JSON key itself in English.
Respond with JSON only, nothing else.

Signature list: {signatures_map}

{{"rules": ["...", "...", "..."]}}
"""
    return generate(prompt, label="코딩 룰")


def analyze_prompt(project_name: str, architecture: list[str], rules: list[str], lang: str = "en") -> str:
    prompt = f"""
Based on the project info below, write 2-3 sentences of core context
that let an AI (or a human skimming this project for the first time)
understand this project immediately on first look.

Focus on WHAT this project is and WHAT PROBLEM it solves -- its purpose,
its main components, and how they fit together. The "Coding rules" list
below is naming/style convention only (e.g. "methods use camelCase") --
do NOT center the summary on coding style or conventions; mention a
rule only if it's genuinely load-bearing context, not as the main point.

Write the "prompt" value in {_lang_name(lang)}. Keep the JSON key itself in English.
Respond with JSON only, nothing else.

Project name: {project_name}
Architecture (tech stack + each file's own summary): {architecture}
Coding rules (style/naming only, secondary context): {rules}

{{"prompt": "..."}}
"""
    return generate(prompt, label="AI 가이드")


def analyze_folder_summaries(folders: dict[str, list[str]], lang: str = "en") -> str:
    """folders: {folder path: ["filename: summary", ...]} -- every file
    directly inside that folder (not recursive/nested subfolders) paired
    with its own already-generated summary, so the model can describe each
    folder's role without re-reading any file content itself. One call
    covers every folder in the project at once (a project's folder count is
    always far smaller than its file count, so this never needs the
    batching/chunking summarizer.py's own per-file summaries need).

    Built as an explicit "Folder: <path>" text block per folder (same
    convention analyze_batch_summaries() uses for its own per-item blocks),
    not a raw dict repr -- both so a real folder path containing characters
    that would look odd mid-Python-repr renders cleanly, and so
    MockProvider can regex-extract the same real folder-path keys a batch
    summary's "File: <name>" lines already let it extract for files.
    """
    parts = [
        "Folder: {}\nFiles:\n{}".format(folder, "\n".join(f"- {entry}" for entry in entries))
        for folder, entries in folders.items()
    ]
    joined = "\n\n".join(parts)

    prompt = f"""
Based on the folders below (each folder's directly-contained files and
their own one-line summaries), write ONE short sentence per folder
describing that folder's role in the project -- what kind of code lives
there and why, not a restatement of the file list.
Write each summary value in {_lang_name(lang)}. Keep the JSON keys (folder paths) unchanged, exactly as given.
Respond with JSON only, nothing else.

{joined}

{{"<folder path>": "...", "<another folder path>": "..."}}
"""
    return generate(prompt, label="폴더 요약")

def analyze_relationships(file_summaries: dict) -> str:
    prompt = f"""
Based on the file names and partial content below,
extract only the direct dependency relationships between files.

Rules:
- Include only cases where one file directly references or uses another
- Exclude cases that are merely related in topic
- Use an empty array if there is no relationship

Respond with JSON only, nothing else.

File list:
{file_summaries}

{{
  "relationships": {{
    "fileA": ["fileB it directly references"],
    "fileB": []
  }}
}}
"""
    return generate(prompt)
