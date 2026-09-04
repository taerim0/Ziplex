"""Which language the pipeline's own progress/status `print()`s (llm.py's
retry/error messages, checkpoint.py's save/failure notices, summarizer.py's
per-file lines, packager.py's own stage headers) should be written in --
entirely separate from `pack()`'s own `lang` param (the packed *content*
language: summaries/rules/AI guide) and from the GUI's display-language
chrome switcher (`js/i18n.js`, which only ever translates the GUI's own web
page, never anything Python prints). Before this existed, every one of
those prints was hardcoded Korean regardless of either setting -- a real
gap reported directly: setting the GUI to English still showed a Korean
pack-progress log, since that log is just captured stdout from these same
prints (`gui/pack_service.py`'s `_capture_for_job`), not GUI-rendered text
`js/i18n.js` has any chance to translate.

A `contextvars.ContextVar`, not a plain module global or `threading.local`:
`pack()`'s own summarizer.py step runs several `analyze_*()` calls in
parallel via a `ThreadPoolExecutor`, and only a `ContextVar`'s value (set
once, here, at `pack()`'s own entry, on whichever thread is actually
running it -- the CLI's main thread, or one of `pack_service.py`'s GUI job
threads) is automatically captured into a worker thread `.submit()`'d from
that same thread (`concurrent.futures` copies the caller's context on
submit) -- a `threading.local()` would leave every worker thread seeing
only the default, since a plain thread inherits nothing from the one that
started it.

`pack()`'s own `progress_lang: str = "ko"` parameter (defaulting to today's
existing CLI behavior) is the only place this ever gets `set()` -- every
other function in the pipeline just calls `pick()` at its own print site,
so none of those needed a new parameter threaded through the whole call
chain from `pack()` down to their own `print()`.
"""

from contextvars import ContextVar

_current: ContextVar[str] = ContextVar("progress_lang", default="ko")


def set_current(lang: str) -> None:
    """Normalizes an unrecognized value to "ko" (today's long-standing
    default) rather than raising -- same "never let a bad language value
    break the pipeline" spirit `packager.pack()`'s own `lang` param already
    follows.
    """
    _current.set(lang if lang in ("en", "ko") else "ko")


def current() -> str:
    return _current.get()


def pick(en: str, ko: str) -> str:
    """The one call every progress print site in the pipeline makes instead
    of hardcoding a Korean literal: `print(pick("Collecting files...",
    "파일 수집 중..."))`. Deliberately just two inline strings, not a
    dict/key registry -- a print site's own two translations are easiest to
    verify sitting right next to each other, and there's no reuse across
    call sites the way `packager.STRUCTURAL_ONLY_NOTE`/`FORMAT_NOTES` (whole
    documents, not one-off log lines) actually need a shared dict for.
    """
    return en if current() == "en" else ko
