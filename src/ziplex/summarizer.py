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
from .llm import analyze_file_summary, analyze_text_summary, analyze_batch_summaries

# Concurrency used when requesting per-file summaries in parallel.
# Kept conservative with LLM API rate limits in mind — adjust if needed.
MAX_WORKERS = 4

# Files per batched summary request (see llm.analyze_batch_summaries). Trades
# a somewhat larger prompt for far fewer requests -- directly eases the
# rate-limit pressure that drives most first-pack retries, without changing
# MAX_WORKERS' cross-batch parallelism.
BATCH_SIZE = 8

# generate_summaries()'s per-file placeholder on failure (see its own
# docstring). Exposed as a constant, not just an inline literal, so
# confidence.py can recognize it and force a failed summary to always read as
# low-confidence -- see the comment there for why that check exists.
SUMMARY_FAILED_PLACEHOLDER = "요약 생성 실패"


def request_summary(file_path: str, data: dict) -> str:
    """Tries once to get one file's summary; returns an empty string on failure.

    (Network retries are already handled inside llm.generate(), so this only
    tries once — whether to involve the user is up to the caller.)
    """
    if data["signatures"] or data["dependencies"]:
        response = analyze_file_summary(
            file_path,
            data["signatures"],
            data["dependencies"]
        )
    else:
        # Use the already-computed compressed text, not a fresh raw read: it's
        # cheaper (no second read, no separate truncation logic) and keeps the
        # summary grounded in what actually ships in aif.json rather than text
        # that may get stripped out of `compressed`.
        response = analyze_text_summary(file_path, data.get("compressed", ""))

    try:
        return json.loads(response).get("summary", "")
    except json.JSONDecodeError:
        return ""


def chunked(items: list, size: int) -> list[list]:
    return [items[i:i + size] for i in range(0, len(items), size)]


def request_batch_summaries(batch: list[tuple[str, dict]]) -> dict[str, str]:
    """batch: [(relative name, data), ...]. Tries one LLM call covering the
    whole batch; any name the response doesn't cover (missing entirely, or
    the model didn't echo the exact key) falls back to request_summary()
    individually -- so a partially wrong/incomplete batch response only
    costs what it actually failed on, not the whole batch, and a fully
    garbled response degrades to the old one-call-per-file behavior instead
    of losing every file in it.
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
    response = analyze_batch_summaries(items)
    try:
        summaries = json.loads(response).get("summaries", {})
    except json.JSONDecodeError:
        summaries = {}

    result = {}
    for name, data in batch:
        result[name] = summaries.get(name) or request_summary(name, data)
    return result


def generate_summaries(pending: dict[str, dict], root: Path) -> dict[str, str]:
    """Requests a summary for every entry in `pending` ({absolute file path:
    data}), batching BATCH_SIZE-at-a-time (see llm.analyze_batch_summaries)
    across a MAX_WORKERS thread pool, printing progress as each one lands.
    Returns {file path: summary} -- same keys as `pending` itself, so the
    caller can write straight back into its own files_data dict.

    Each summary falls back to a placeholder ("요약 생성 실패") rather than
    an empty string on failure: correct_aif()'s per-file review (triaged by
    confidence.py) is what catches and fixes a wrong or missing summary
    later, not a retry loop here -- see pack()'s own docstring for why that
    trade-off (try once, in parallel, let review catch the rest) was made.
    """
    name_to_fp = {_rel_key(fp, root): fp for fp in pending}
    batches = [
        [(name, pending[name_to_fp[name]]) for name in chunk]
        for chunk in chunked(list(name_to_fp.keys()), BATCH_SIZE)
    ]

    results = {}
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {
            executor.submit(request_batch_summaries, batch): batch
            for batch in batches
        }
        for future in as_completed(futures):
            for name, summary in future.result().items():
                fp = name_to_fp[name]
                results[fp] = summary or SUMMARY_FAILED_PLACEHOLDER
                print(f"  ✅ {name}")
    return results


# Cap on how many signature/dependency names a structural summary lists
# before collapsing the rest into "+N more" -- matches corrector.py's own
# cap on a flagged file's shown signatures (_SIGNATURES_SHOWN in
# pack_service.py), same reasoning: enough to be useful, not a full dump.
_STRUCTURAL_SUMMARY_SHOWN = 5


def _structural_summary(data: dict) -> str:
    """A summary with no LLM involved at all: just what extract_signatures()/
    extract_dependencies() already found, formatted as one line. Not a
    description of what the file *does* (that's exactly the part only an
    LLM -- or a human -- can actually judge) -- a plain listing of what's
    there, which is still strictly more useful than an empty string.
    """
    sigs = data.get("signatures") or []
    if sigs:
        shown = ", ".join(sigs[:_STRUCTURAL_SUMMARY_SHOWN])
        if len(sigs) > _STRUCTURAL_SUMMARY_SHOWN:
            shown += f", +{len(sigs) - _STRUCTURAL_SUMMARY_SHOWN} more"
        return f"Defines: {shown}"

    deps = data.get("dependencies") or []
    if deps:
        shown = ", ".join(deps[:_STRUCTURAL_SUMMARY_SHOWN])
        if len(deps) > _STRUCTURAL_SUMMARY_SHOWN:
            shown += f", +{len(deps) - _STRUCTURAL_SUMMARY_SHOWN} more"
        return f"References: {shown}"

    return "No signatures or dependencies detected (structural-only mode, no LLM summary)."


def generate_structural_summaries(pending: dict[str, dict], root: Path) -> dict[str, str]:
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
    own output -- not for anything in the summary text itself.
    """
    results = {}
    for fp, data in pending.items():
        results[fp] = _structural_summary(data)
        print(f"  ✅ {_rel_key(fp, root)}")
    return results
