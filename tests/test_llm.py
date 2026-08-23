"""GeminiProvider's model resolution -- constructed URL only, no network
calls (checking .url is enough to verify precedence without needing a real
API key or GEMINI_MODEL to reach through to an actual request) -- plus
_resolve_api_key()'s precedence (explicit arg > settings.py's stored key >
GEMINI_API_KEY env var), which the model isn't resolved the same way (see
GeminiProvider.__init__'s own comment on why the key specifically has to
be re-resolved per call instead of cached at construction).
"""

import llm
import settings as app_settings


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
