import json
import os
import re
import time
from typing import Protocol

import requests
from dotenv import load_dotenv

from . import settings as app_settings

load_dotenv()


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

    def generate(self, prompt: str, retry: int = 5) -> str: ...


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
        # Not resolved to a final key here -- see _resolve_api_key() below
        # for why the key specifically (unlike the model) has to stay
        # re-resolved on every call instead of being baked in once at
        # construction time.
        self._explicit_api_key = api_key
        # Precedence: an explicit constructor arg (programmatic/test callers)
        # wins over GEMINI_MODEL (a user's own .env override) wins over
        # DEFAULT_MODEL.
        resolved_model = model or os.getenv("GEMINI_MODEL") or self.DEFAULT_MODEL
        self.url = (
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

    def generate(self, prompt: str, retry: int = 5) -> str:
        api_key = self._resolve_api_key()
        for attempt in range(retry):
            try:
                response = requests.post(f"{self.url}?key={api_key}", json={
                    "contents": [{"parts": [{"text": prompt}]}]
                })
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
                wait = 5 * (attempt + 1)  # 5s, 10s, 15s, ...
                print(f"  ⚠️  네트워크 오류 ({e.__class__.__name__}), {wait}초 후 재시도 ({attempt+1}/{retry})")
                time.sleep(wait)
                continue

            if "candidates" in data:
                text = data["candidates"][0]["content"]["parts"][0]["text"]
                return _clean_json(text)

            error_code = data.get("error", {}).get("code", 0)
            error_msg = data.get("error", {}).get("message", "unknown")

            if error_code in [503, 429]:
                wait = 5 * (attempt + 1)  # 5s, 10s, 15s, ...
                print(f"  ⚠️  서버 과부하, {wait}초 후 재시도 ({attempt+1}/{retry})")
                time.sleep(wait)
                continue

            print(f"  ❌ API 에러: {error_msg}")
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

    def generate(self, prompt: str, retry: int = 5) -> str:
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
    "mock": MockProvider,
}

_provider: LLMProvider = PROVIDERS[os.getenv("LLM_PROVIDER", "gemini")]()


def generate(prompt: str, retry: int = 5) -> str:
    """Delegates to the currently active LLM provider. Thread-safe (provider state is read-only)."""
    return _provider.generate(prompt, retry=retry)


def analyze_file_summary(file_path: str, signatures: list[str], dependencies: list[str]) -> str:
    prompt = f"""
Based on the file info below, summarize this file's role in one line.
Respond with JSON only, nothing else.

File: {file_path}
Function signatures: {signatures}
Dependencies: {dependencies}

{{"summary": "..."}}
"""
    return generate(prompt)


def analyze_text_summary(file_path: str, content: str) -> str:
    prompt = f"""
Based on the file content below, summarize this file's role in one line.
Respond with JSON only, nothing else.

File: {file_path}
Content:
{content[:500]}

{{"summary": "..."}}
"""
    return generate(prompt)


def analyze_batch_summaries(items: list[dict]) -> str:
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
    for item in items:
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
Respond with JSON only, nothing else.

{joined}

{{"summaries": {{"<file>": "...", "<file>": "..."}}}}
"""
    return generate(prompt)


def analyze_rules(signatures_map: dict) -> str:
    prompt = f"""
Analyze the function signature patterns of the project below
and extract its implicit coding rules.
Respond with JSON only, nothing else.

Signature list: {signatures_map}

{{"rules": ["...", "...", "..."]}}
"""
    return generate(prompt)


def analyze_prompt(project_name: str, architecture: list[str], rules: list[str]) -> str:
    prompt = f"""
Based on the project info below, write 2-3 sentences of core context
that let an AI understand this project immediately on first look.
Respond with JSON only, nothing else.

Project name: {project_name}
Architecture: {architecture}
Coding rules: {rules}

{{"prompt": "..."}}
"""
    return generate(prompt)

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
