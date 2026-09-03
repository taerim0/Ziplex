"""Ziplex GUI's persistent, cross-session settings: where new packs get
saved by default, and which LLM provider/credentials handle summaries.
Lives outside the repo entirely, under the user's home directory -- unlike
config.py's .ziplex.json (per-*target*-project, lives inside that project,
meant to be git-committed alongside it), this is per-*user*, one file for
the whole app, matching what an installed local program's own settings
usually look like rather than a project file.

Two layers, resolved together by resolve_output_dir(): a global default
(`output_dir`, sensible fallback for any project with nothing more
specific configured) and per-project pins (`project_output_dirs`,
keyed by each project's absolute path) that override it. A pin is only
ever created when a user explicitly types an output path while packing a
given project (see gui/pack_service.py's start_pack_job()) -- never as a
side effect of merely using the global default -- so a project with no
pin of its own keeps tracking whatever the global default currently is,
even if that default changes later. Neither layer being set at all means
"nothing configured anywhere" -- callers (packager.py's `result_dir`
param) fall back to their own built-in default (RESULT_DIR) in that case,
so a fresh install with no settings.json behaves identically to before
this module existed.

`llm_provider` + each provider's own credential/model fields (`gemini_api_key`,
`openai_api_key`/`openai_base_url`/`openai_model`, `claude_api_key`/
`claude_model`) are the Options page's provider selector -- llm.py's
_active_provider() reads `llm_provider` fresh on every generate() call to
decide which of these to actually use, the same "GUI-editable, takes effect
on the very next pack, not after a restart" reasoning resolve_gemini_api_key()
already documents. All flat strings, not nested under a provider name,
since a human only ever fills in one set at a time via the dropdown --
switching providers doesn't need the others' fields cleared, just ignored.

Never raises, same spirit as config.py's load_config(): a missing file,
bad JSON, or a non-dict value all fall back to DEFAULT_SETTINGS unchanged,
so a fresh install (or a hand-corrupted settings.json) never blocks a pack.
"""

import json
from pathlib import Path

SETTINGS_PATH = Path.home() / ".ziplex" / "settings.json"

# Every field a human is allowed to write directly -- via gui_server.py's
# POST /api/settings (the Options page) or the `ziplex settings set` CLI
# subcommand, the two callers that need this list. `project_output_dirs` is
# deliberately excluded from both: a pin is only ever set implicitly, by
# packing with an explicit output path (set_project_output_dir(), called
# from gui/pack_service.py's start_pack_job()), never through a direct
# settings write -- see that function's own docstring for why. One shared
# tuple instead of two independently-typed-out copies, so the two callers
# can't quietly drift apart on which fields are actually editable.
EDITABLE_FIELDS = (
    "output_dir",
    "gemini_api_key",
    "gemini_model",
    "llm_provider",
    "openai_api_key",
    "openai_base_url",
    "openai_model",
    "claude_api_key",
    "claude_model",
)

DEFAULT_SETTINGS = {
    "output_dir": "",           # "" = no global override, packager.RESULT_DIR applies
    "project_output_dirs": {},  # {absolute project path: output dir}, explicit per-project pins
    "gemini_api_key": "",       # "" = no override, GEMINI_API_KEY env var / .env applies (see llm.py)
    # "" = no override, GEMINI_MODEL env var / DEFAULT_MODEL applies -- added
    # after a real external-tester report (2026-08-24): entering only an API
    # key via the Options page left them stuck on DEFAULT_MODEL (a floating
    # "-latest" alias with its own documented 503-overload history, see
    # GeminiProvider.DEFAULT_MODEL's comment) with literally no way to
    # switch models short of an env var on their own machine, which a
    # GUI-only user has no reason to know exists.
    "gemini_model": "",
    # Which LLM provider actually handles a pack's summaries/rules/AI guide.
    # "" = no override -- llm.py's LLM_PROVIDER env var (read once at import,
    # what every non-GUI caller and every test already relies on) applies,
    # defaulting to "gemini" if that's unset too. Set by the Options page's
    # provider selector; must be one of llm.PROVIDERS' own keys to take
    # effect (an unrecognized value degrades to the env-var default the same
    # way an empty one does -- see llm.py's _active_provider()).
    "llm_provider": "",
    # OpenAIProvider's own three knobs (llm.py) -- one class covers OpenAI
    # itself and anything speaking its Chat Completions API (Ollama, LM
    # Studio, vLLM, llama.cpp's server, OpenRouter, ...), including a local
    # model like Gemma or Llama served through one of those, distinguished
    # purely by base_url/model rather than a dedicated class per vendor.
    "openai_api_key": "",    # "" = OPENAI_API_KEY env var applies (often unneeded for a local server)
    "openai_base_url": "",   # "" = OpenAIProvider.DEFAULT_BASE_URL (api.openai.com/v1)
    "openai_model": "",      # "" = OpenAIProvider.DEFAULT_MODEL
    # ClaudeProvider's own two knobs -- Anthropic's Messages API, a
    # different shape from both Gemini and the OpenAI-compatible family
    # above, so it isn't just another OpenAIProvider base_url.
    "claude_api_key": "",    # "" = ANTHROPIC_API_KEY / CLAUDE_API_KEY env var applies
    "claude_model": "",      # "" = ClaudeProvider.DEFAULT_MODEL
}


def _fresh_defaults() -> dict:
    # Not dict(DEFAULT_SETTINGS) -- that's a shallow copy, so its
    # "project_output_dirs" key would still alias DEFAULT_SETTINGS' own {}
    # object. A caller that goes on to mutate the returned dict in place
    # (set_project_output_dir() does exactly that) would then be mutating
    # the shared module-level default for the rest of the process. A fresh
    # literal per call keeps every load_settings() result independently
    # owned, same as before this function existed to de-duplicate the two
    # call sites below.
    return {**DEFAULT_SETTINGS, "project_output_dirs": {}}


def load_settings() -> dict:
    try:
        with open(SETTINGS_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, OSError, UnicodeDecodeError):
        return _fresh_defaults()
    if not isinstance(data, dict):
        return _fresh_defaults()

    project_dirs = data.get("project_output_dirs")
    return {
        "output_dir": data.get("output_dir") or "",
        "project_output_dirs": project_dirs if isinstance(project_dirs, dict) else {},
        "gemini_api_key": data.get("gemini_api_key") or "",
        "gemini_model": data.get("gemini_model") or "",
        "llm_provider": data.get("llm_provider") or "",
        "openai_api_key": data.get("openai_api_key") or "",
        "openai_base_url": data.get("openai_base_url") or "",
        "openai_model": data.get("openai_model") or "",
        "claude_api_key": data.get("claude_api_key") or "",
        "claude_model": data.get("claude_model") or "",
    }


def save_settings(settings: dict) -> None:
    SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(SETTINGS_PATH, "w", encoding="utf-8") as f:
        json.dump(settings, f, ensure_ascii=False, indent=2)


def _abs_key(project_path: str) -> str:
    # Absolute so the same project resolves to the same key regardless of
    # what cwd-relative form it was typed in from; not case-normalized
    # (Windows paths differing only in case are treated as distinct keys,
    # same limitation as elsewhere in this codebase -- not worth the extra
    # complexity for a single-user local settings file).
    return str(Path(project_path).resolve())


def resolve_output_dir(project_path: str, settings: dict | None = None) -> str:
    """The effective output folder for `project_path` -- a per-project pin
    if one exists, else the global default, else "" (meaning: the caller
    should fall back to its own built-in default). `settings`, if not
    given, is loaded fresh -- pass an already-loaded dict when resolving
    for several projects at once to avoid re-reading the file each time.
    """
    settings = settings if settings is not None else load_settings()
    pinned = settings["project_output_dirs"].get(_abs_key(project_path))
    return pinned or settings["output_dir"] or ""


def resolve_output_path(project_path: str, project_name: str, settings: dict | None = None) -> str:
    """The full aif.json path a new pack of `project_path` should default
    to when the user hasn't typed an explicit one -- resolve_output_dir()'s
    folder plus this project's own name, or "" if nothing is configured at
    either layer (packager.py's own RESULT_DIR-based default then applies,
    completely unchanged from before this module existed).
    """
    output_dir = resolve_output_dir(project_path, settings)
    if not output_dir:
        return ""
    return str(Path(output_dir) / f"{project_name}.json")


def set_project_output_dir(project_path: str, output_dir: str) -> None:
    """Pins `project_path` to `output_dir` going forward. Called once a pack
    actually runs with an explicitly-typed output path (gui/pack_service.py's
    start_pack_job()) -- never automatically from merely resolving/using a
    default, since that would silently freeze a project onto whatever the
    global default happened to be at the time of its first pack, instead of
    letting it keep tracking later changes to that default the way an
    unpinned project is supposed to.
    """
    settings = load_settings()
    settings["project_output_dirs"][_abs_key(project_path)] = output_dir
    save_settings(settings)


def resolve_gemini_api_key(settings: dict | None = None) -> str:
    """The GUI-editable override for GEMINI_API_KEY -- "" if nothing's been
    set via the Options page, in which case llm.py's own env var / .env
    fallback applies, completely unchanged from before this setting
    existed. `settings`, if not given, is loaded fresh.

    Unlike output_dir/project_output_dirs, this is read fresh on every
    llm.py request (see GeminiProvider._resolve_api_key()), not once at
    provider-construction time -- llm.py's module-level `_provider` is a
    singleton built once at import and stays alive for the whole GUI
    server process, so a key saved here after that needs to take effect on
    the very next pack, not after a restart.
    """
    settings = settings if settings is not None else load_settings()
    return settings["gemini_api_key"] or ""


def _resolve_field(key: str, settings: dict | None = None) -> str:
    """Shared body for the six near-identical resolve_*() functions below --
    resolve_gemini_api_key() above stays its own named function (nothing
    new depends on it) rather than being folded in, so existing callers/
    imports are untouched.
    """
    settings = settings if settings is not None else load_settings()
    return settings.get(key) or ""


def resolve_gemini_model(settings: dict | None = None) -> str:
    return _resolve_field("gemini_model", settings)


def resolve_llm_provider_name(settings: dict | None = None) -> str:
    """"" (unset) means llm.py's LLM_PROVIDER env var / "gemini" default
    applies -- see llm.py's _active_provider() for the full precedence.
    """
    return _resolve_field("llm_provider", settings)


def resolve_openai_api_key(settings: dict | None = None) -> str:
    return _resolve_field("openai_api_key", settings)


def resolve_openai_base_url(settings: dict | None = None) -> str:
    return _resolve_field("openai_base_url", settings)


def resolve_openai_model(settings: dict | None = None) -> str:
    return _resolve_field("openai_model", settings)


def resolve_claude_api_key(settings: dict | None = None) -> str:
    return _resolve_field("claude_api_key", settings)


def resolve_claude_model(settings: dict | None = None) -> str:
    return _resolve_field("claude_model", settings)
