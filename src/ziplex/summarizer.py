"""Per-file summary generation for pack(), split out of packager.py so that
module is left owning just the pipeline itself, not also this: batching
pending files into fewer, larger LLM requests (see llm.analyze_batch_
summaries), and falling back to a per-file request for anything a batch
response didn't cover.

generate_summaries() is the entry point pack() actually calls when an LLM
is available; the smaller functions below it (chunked/request_summary/
request_batch_summaries) are exposed too, both because pack() doesn't need
the batching/threading detail and because tests exercise them directly
without spinning up a whole pack() run.

generate_structural_summaries() is the LLM-free alternative pack(...,
use_llm=False) calls instead -- see its own docstring for why it lives
here rather than a separate module: this file's actual subject is "how does
pack() get its per-file summaries," and a deterministic fallback is a
second answer to that same question, not an unrelated concern.

Reusing a previous pack's summary for an unchanged file (staleness stage 2)
is deliberately *not* here -- that's freshness.load_previous_summaries(),
since deciding what still counts as "fresh" is squarely that module's own
subject, not a concern of the LLM-call batching this module actually owns.
"""

from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
import json

from .file.textutil import relative_key as _rel_key
from . import llm
from .llm import analyze_file_summary, analyze_text_summary, analyze_batch_summaries
from .progress_i18n import pick

# Concurrency used when requesting per-file summaries in parallel.
# Kept conservative with LLM API rate limits in mind — adjust if needed.
MAX_WORKERS = 4

# Files per batched summary request (see llm.analyze_batch_summaries). Trades
# a somewhat larger prompt for far fewer requests -- directly eases the
# rate-limit pressure that drives most first-pack retries, without changing
# MAX_WORKERS' cross-batch parallelism.
BATCH_SIZE = 8

# generate_summaries()'s per-file placeholder on failure (see its own
# docstring), one per supported packed-content language (llm.LANGUAGE_NAMES)
# so a project packed in Korean gets a Korean placeholder and one packed in
# English gets an English one -- previously a single hardcoded Korean
# string regardless of any language setting. Exposed so confidence.py can
# recognize *any* of them (is_summary_failed_placeholder()) and force a
# failed summary to always read as low-confidence -- see the comment there
# for why that check exists. Checking every language, not just the current
# run's `lang`, matters because a project's cached previous-pack summary
# (freshness.load_previous_summaries()) may have been written under a
# different language selection than this run's.
SUMMARY_FAILED_PLACEHOLDERS: dict[str, str] = {
    "en": "Summary generation failed",
    "ko": "요약 생성 실패",
}


def is_summary_failed_placeholder(summary: str) -> bool:
    """True if `summary` is the failure placeholder in any supported
    language -- see SUMMARY_FAILED_PLACEHOLDERS above for why "any", not
    just the current run's own `lang`."""
    return summary in SUMMARY_FAILED_PLACEHOLDERS.values()


def request_summary(file_path: str, data: dict, lang: str = "en") -> str:
    """Tries once to get one file's summary; returns an empty string on failure.

    (Network retries are already handled inside llm.generate(), so this only
    tries once — whether to involve the user is up to the caller.)
    """
    if data["signatures"] or data["dependencies"]:
        response = analyze_file_summary(
            file_path,
            data["signatures"],
            data["dependencies"],
            lang=lang,
        )
    else:
        # Use the already-computed compressed text, not a fresh raw read: it's
        # cheaper (no second read, no separate truncation logic) and keeps the
        # summary grounded in what actually ships in aif.json rather than text
        # that may get stripped out of `compressed`.
        response = analyze_text_summary(file_path, data.get("compressed", ""), lang=lang)

    try:
        data = json.loads(response)
    except json.JSONDecodeError:
        return ""
    # A non-object JSON response (e.g. a bare list) would otherwise raise
    # AttributeError on .get() inside generate_summaries()'s thread pool.
    return _summary_text(data) if isinstance(data, dict) else ""


def chunked(items: list, size: int) -> list[list]:
    return [items[i:i + size] for i in range(0, len(items), size)]


def _normalize_key(name: str) -> str:
    """How a batch response's file key is compared against the name we
    sent: models sometimes echo a key back with a leading "./", Windows
    separators, or stray whitespace, which previously counted as a miss
    and cost a separate per-file request."""
    key = name.strip().replace("\\", "/")
    while key.startswith("./"):
        key = key[2:]
    return key


def _summary_text(value) -> str:
    """A summary value as a plain string -- a model occasionally nests it
    as {"summary": "..."} instead of the bare string the prompt asked for."""
    if isinstance(value, dict):
        value = value.get("summary", "")
    return value if isinstance(value, str) else ""


def _parse_batch_response(response: str) -> dict[str, str]:
    """{normalized file key: summary} from an analyze_batch_summaries()
    response, accepting the shapes models actually return besides the
    requested {"summaries": {file: text}}: a list of {"file", "summary"}
    objects, or the file map without the "summaries" wrapper. Anything
    unparseable is {} -- never an exception, since this runs inside
    generate_summaries()'s thread pool, where one would abort the pack."""
    try:
        data = json.loads(response)
    except json.JSONDecodeError:
        return {}
    if isinstance(data, dict):
        data = data.get("summaries", data)
    if isinstance(data, list):
        data = {
            item.get("file", ""): item.get("summary", "")
            for item in data if isinstance(item, dict)
        }
    if not isinstance(data, dict):
        return {}
    parsed = {}
    for key, value in data.items():
        text = _summary_text(value)
        if isinstance(key, str) and text:
            parsed[_normalize_key(key)] = text
    return parsed


def request_batch_summaries(batch: list[tuple[str, dict]], lang: str = "en", _rebatch: bool = True) -> dict[str, str]:
    """batch: [(relative name, data), ...]. Tries one LLM call covering the
    whole batch. Names the response doesn't cover (missing entirely, or an
    unmatched key) get one more batched call of just those names, and only
    what's *still* missing falls back to request_summary() per file -- so a
    garbled response costs one extra request, not one per file in it.
    """
    items = [
        {
            "file": name,
            "signatures": data["signatures"],
            "dependencies": data["dependencies"],
            "content": data.get("compressed", ""),
        }
        for name, data in batch
    ]
    response = analyze_batch_summaries(items, lang=lang)
    # llm._retry_loop()'s "gave up" sentinel (retries exhausted, rate limit,
    # non-retryable API error) -- re-batching would just repeat that same
    # failing call, so only an unusable-but-real response gets a re-batch.
    provider_gave_up = response.strip() == "{}"
    # Retries ran out (quota spent, outage) rather than this request being
    # refused: per-file fallback would repeat the same failing call once
    # per file, each with the full backoff.
    provider_exhausted = provider_gave_up and llm.last_call_exhausted()
    summaries = _parse_batch_response(response)

    result = {}
    missed = []
    for name, data in batch:
        summary = summaries.get(_normalize_key(name))
        if summary:
            result[name] = summary
        else:
            missed.append((name, data))

    if missed and _rebatch and len(missed) > 1 and not provider_gave_up:
        print(pick(
            f"  ⚠️  batch response missed {len(missed)}/{len(batch)} files -- retrying them as one batch",
            f"  ⚠️  배치 응답에서 {len(missed)}/{len(batch)}개 파일 누락 -- 한 배치로 재요청",
        ))
        result.update(request_batch_summaries(missed, lang=lang, _rebatch=False))
    elif provider_exhausted:
        for name, _data in missed:
            result[name] = SUMMARY_FAILED_PLACEHOLDERS.get(lang, SUMMARY_FAILED_PLACEHOLDERS["en"])
    else:
        for name, data in missed:
            result[name] = request_summary(name, data, lang=lang)
    return result


def generate_summaries(pending: dict[str, dict], root: Path, lang: str = "en") -> dict[str, str]:
    """Requests a summary for every entry in `pending` ({absolute file path:
    data}), batching BATCH_SIZE-at-a-time (see llm.analyze_batch_summaries)
    across a MAX_WORKERS thread pool, printing progress as each one lands.
    Returns {file path: summary} -- same keys as `pending` itself, so the
    caller can write straight back into its own files_data dict.

    lang picks which SUMMARY_FAILED_PLACEHOLDERS entry a failure falls back
    to, and is threaded down into every llm.analyze_*() call so the summary
    text itself is written in that language -- see llm.py's LANGUAGE_NAMES
    for the supported set and packager.pack()'s own `lang` param for where
    this ultimately comes from (CLI --lang / the GUI pack form).

    Each summary falls back to a placeholder rather than an empty string on
    failure: correct_aif()'s per-file review (triaged by confidence.py) is
    what catches and fixes a wrong or missing summary later, not a retry
    loop here -- see pack()'s own docstring for why that trade-off (try
    once, in parallel, let review catch the rest) was made. The printed
    progress line reflects which of those two actually happened per file
    (✅ for a real summary, ❌ for a placeholder) -- reported directly as a
    real bug: this used to print ✅ unconditionally, so a file that
    silently fell back to the failure placeholder looked exactly like a
    success in the log, with nothing pointing a human at the one place
    (correct_aif()'s review) meant to actually catch it.
    """
    name_to_fp = {_rel_key(fp, root): fp for fp in pending}
    batches = [
        [(name, pending[name_to_fp[name]]) for name in chunk]
        for chunk in chunked(list(name_to_fp.keys()), BATCH_SIZE)
    ]
    placeholder = SUMMARY_FAILED_PLACEHOLDERS.get(lang, SUMMARY_FAILED_PLACEHOLDERS["en"])

    results = {}
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {
            executor.submit(request_batch_summaries, batch, lang): batch
            for batch in batches
        }
        for future in as_completed(futures):
            for name, summary in future.result().items():
                fp = name_to_fp[name]
                if summary:
                    results[fp] = summary
                    print(f"  ✅ {name}")
                else:
                    results[fp] = placeholder
                    print(pick(
                        f"  ❌ {name} (summary generation failed -- needs review)",
                        f"  ❌ {name} (요약 생성 실패 -- 검토 단계에서 확인 필요)",
                    ))
    return results


# Cap on how many signature/dependency names a structural summary lists
# before collapsing the rest into "+N more" -- matches corrector.py's own
# cap on a flagged file's shown signatures (_SIGNATURES_SHOWN in
# pack_service.py), same reasoning: enough to be useful, not a full dump.
_STRUCTURAL_SUMMARY_SHOWN = 5

# Labels for _structural_summary()'s three shapes, one set per supported
# packed-content language -- this text is entirely Ziplex's own (never an
# LLM call, use_llm=False's whole point), so it follows the chosen `lang`
# for the same consistency reason packager.py's STRUCTURAL_ONLY_NOTE/
# FORMAT_NOTES and SUMMARY_FAILED_PLACEHOLDERS above do: a Korean-language
# pack shouldn't ship an English structural summary next to Korean LLM
# summaries for its other files.
_STRUCTURAL_LABELS: dict[str, dict[str, str]] = {
    "en": {
        "defines": "Defines",
        "references": "References",
        "none": "No signatures or dependencies detected (structural-only mode, no LLM summary).",
    },
    "ko": {
        "defines": "정의",
        "references": "참조",
        "none": "감지된 시그니처/의존성 없음 (구조 정보 전용 모드, LLM 요약 없음).",
    },
}


def is_structural_summary(summary: str) -> bool:
    """True if `summary` has _structural_summary()'s shape, in any supported
    language -- a --no-llm pack's output. pack() uses this to keep a later
    LLM pack from reusing those lines as if they were real summaries
    (freshness.load_previous_summaries() only knows a file is unchanged,
    not how its previous summary was made)."""
    for labels in _STRUCTURAL_LABELS.values():
        if summary == labels["none"]:
            return True
        if summary.startswith((f"{labels['defines']}: ", f"{labels['references']}: ")):
            return True
    return False


def _structural_summary(data: dict, lang: str = "en") -> str:
    """A summary with no LLM involved at all: just what extract_signatures()/
    extract_dependencies() already found, formatted as one line. Not a
    description of what the file *does* (that's exactly the part only an
    LLM -- or a human -- can actually judge) -- a plain listing of what's
    there, which is still strictly more useful than an empty string.
    """
    labels = _STRUCTURAL_LABELS.get(lang, _STRUCTURAL_LABELS["en"])

    sigs = data.get("signatures") or []
    if sigs:
        shown = ", ".join(sigs[:_STRUCTURAL_SUMMARY_SHOWN])
        if len(sigs) > _STRUCTURAL_SUMMARY_SHOWN:
            shown += f", +{len(sigs) - _STRUCTURAL_SUMMARY_SHOWN} more"
        return f"{labels['defines']}: {shown}"

    deps = data.get("dependencies") or []
    if deps:
        shown = ", ".join(deps[:_STRUCTURAL_SUMMARY_SHOWN])
        if len(deps) > _STRUCTURAL_SUMMARY_SHOWN:
            shown += f", +{len(deps) - _STRUCTURAL_SUMMARY_SHOWN} more"
        return f"{labels['references']}: {shown}"

    return labels["none"]


def generate_structural_summaries(pending: dict[str, dict], root: Path, lang: str = "en") -> dict[str, str]:
    """The use_llm=False counterpart to generate_summaries() -- same
    signature (pending, root), no network call, no batching, no retry:
    every entry in `pending` ({file path: data}) gets a summary built purely
    from its own already-extracted signatures/dependencies (see
    _structural_summary()). Synchronous and effectively instant, so there's
    no thread pool here the way generate_summaries() needs one to hide LLM
    request latency.

    Returns {file path: summary} -- same keys as `pending`, same contract
    generate_summaries() follows, so pack() can call either one
    interchangeably depending on use_llm. `root` is only used for the
    printed progress line's relative name, matching generate_summaries()'s
    own output -- not for anything in the summary text itself. `lang`
    selects which _STRUCTURAL_LABELS entry the summary text itself is
    written in.
    """
    results = {}
    for fp, data in pending.items():
        results[fp] = _structural_summary(data, lang=lang)
        print(f"  ✅ {_rel_key(fp, root)}")
    return results
