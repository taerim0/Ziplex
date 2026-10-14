"""Per-folder summary generation for pack() -- what role each folder plays
in the project, the aggregate-level counterpart to summarizer.py's per-file
summaries. Split into its own module for the same reason summarizer.py is
its own module: packager.py stays the pipeline stitcher, not the owner of
"how a folder's summary actually gets generated."

Deliberately much lighter-weight than summarizer.py's own machinery:
- One folder is never re-summarized across packs the way an unchanged
  file's summary is (freshness.load_previous_summaries()) -- a folder has
  no content hash of its own to compare against, and the cost of always
  regenerating is bounded by folder count, not file count.
- A folder-summary failure is NOT wired into checkpoint.handle_llm_failure()
  the way rules/prompt/every per-file summary are. Rules/prompt/summaries
  feed confidence scoring and the human review flow directly -- losing
  progress on those on an interrupted run is a real cost worth a resumable
  checkpoint. A folder summary is a pure orientation aid layered on top of
  already-generated file summaries -- editable via the same corrector.py/
  edits.py review flow as per-file summaries (edits.set_folder_summary());
  falling back to a free structural sentence for just the folders a chunked
  LLM call happened to miss is an acceptable degrade that keeps this
  feature from growing its own checkpoint-schema footprint for a
  comparatively low-stakes step.

group_confidence_by_folder() below gives folder review the same
triage() split per-file review already gets -- not an independent
correctness signal (a folder summary's own wording is never checked
against anything), just the average of what per-file confidence already
knows about the folder's members, surfaced one level up.
"""

import contextvars
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Callable

from .file.textutil import parent_folder
from .llm import analyze_folder_summaries, format_folder_block, FOLDER_PROMPT_OVERHEAD_CHARS

# One analyze_folder_summaries() call covers at most this many folders and
# this many prompt chars (template included). A single uncapped call was
# ~255K chars on django -- far past what fits a request timeout.
FOLDER_CHUNK_MAX_FOLDERS = 40
FOLDER_PROMPT_MAX_CHARS = 30_000
# Chunks run concurrently on this many threads.
FOLDER_MAX_WORKERS = 4

# Cap on how many files a structural (--no-llm) folder summary lists by
# name before collapsing the rest into "+N more" -- same reasoning and
# same shape as summarizer.py's own _STRUCTURAL_SUMMARY_SHOWN.
_STRUCTURAL_FILES_SHOWN = 5

# Labels for _structural_folder_summary()'s fixed sentence, one set per
# supported packed-content language -- same convention as summarizer.py's
# own _STRUCTURAL_LABELS (this text is entirely Ziplex's own, never an LLM
# call, so it follows the chosen `lang` too).
_STRUCTURAL_FOLDER_LABELS: dict[str, dict[str, str]] = {
    "en": {
        "contains": "Contains {n} file(s): {files}",
        "more": "+{n} more",
    },
    "ko": {
        "contains": "파일 {n}개 포함: {files}",
        "more": "+{n}개 더",
    },
}


def group_files_by_folder(files_data: dict) -> dict[str, list[str]]:
    """{relative name: data} -> {folder path: ["filename: summary", ...]},
    one entry per file directly inside that folder (never recursive --
    a subfolder's own files show up under the subfolder's own key, not
    duplicated into every ancestor). A root-level file's folder is "."
    (Path(name).parent's own natural value for a name with no directory
    component), not a special-cased empty string or "root" sentinel --
    matches how every other path in this codebase is already POSIX-
    normalized (file/textutil.py's relative_key()).

    Dict iteration order (Python 3.7+, guaranteed) follows files_data's own
    insertion order, so a folder's file list reads in roughly the same
    order collect_files() found them -- not alphabetized, but stable and
    not worth sorting for what's ultimately just LLM prompt input.
    """
    grouped: dict[str, list[str]] = {}
    for name, data in files_data.items():
        folder = parent_folder(name)
        filename = Path(name).name
        summary = data.get("summary", "")
        entry = f"{filename}: {summary}" if summary else filename
        grouped.setdefault(folder, []).append(entry)
    return grouped


def group_confidence_by_folder(files_data: dict) -> dict[str, float]:
    """{relative name: data} -> {folder path: aggregate confidence}, the
    confidence-level counterpart to group_files_by_folder() above --
    averages each directly-contained file's already-computed
    confidence.estimate_confidence() score (files_data[name]["confidence"],
    set by packager.py's per-file loop before folder summaries are ever
    generated). This is NOT a new, independently-verified signal --
    folder_summary.py still has no way to check a folder summary's own
    wording against anything -- it's the existing per-file signal averaged
    up one level, so corrector.py/pack_service.py's folder review step can
    triage.triage() it exactly like per-file summaries instead of always
    showing every single folder.

    A file with no "confidence" key yet (called before packager.py's
    per-file loop has run) is silently skipped rather than treated as 0 --
    same "nothing to contradict yet" reasoning as estimate_confidence()'s
    own no-signatures shortcut. A folder with no scored files at all (that
    skip left it with nothing) defaults to 1.0, the same convention.
    """
    grouped: dict[str, list[float]] = {}
    for name, data in files_data.items():
        if "confidence" not in data:
            continue
        grouped.setdefault(parent_folder(name), []).append(data["confidence"])
    return {
        folder: round(sum(scores) / len(scores), 2)
        for folder, scores in grouped.items()
    }


def _structural_folder_summary(entries: list[str], lang: str) -> str:
    labels = _STRUCTURAL_FOLDER_LABELS.get(lang, _STRUCTURAL_FOLDER_LABELS["en"])
    # entries are "filename: summary" strings -- only the filename half is
    # shown here, matching summarizer.py's own structural summaries (which
    # list signature/dependency *names*, not their own descriptions either).
    names = [entry.split(":", 1)[0] for entry in entries]
    shown = names[:_STRUCTURAL_FILES_SHOWN]
    remaining = len(names) - len(shown)
    files_text = ", ".join(shown)
    if remaining > 0:
        files_text += f" {labels['more'].format(n=remaining)}"
    return labels["contains"].format(n=len(names), files=files_text)


def generate_structural_folder_summaries(files_data: dict, lang: str = "en") -> dict[str, str]:
    """The use_llm=False counterpart to generate_folder_summaries() below --
    no network, each folder's summary a deterministic "Contains N files:
    ..." sentence built from its own already-grouped file list.
    """
    return {
        folder: _structural_folder_summary(entries, lang)
        for folder, entries in group_files_by_folder(files_data).items()
    }


def chunk_folders(folders: dict[str, list[str]]) -> list[dict[str, list[str]]]:
    """Splits `folders` (insertion order kept) into chunks of at most
    FOLDER_CHUNK_MAX_FOLDERS folders whose analyze_folder_summaries()
    prompt stays within FOLDER_PROMPT_MAX_CHARS. Deterministic."""
    budget = FOLDER_PROMPT_MAX_CHARS - FOLDER_PROMPT_OVERHEAD_CHARS
    chunks: list[dict[str, list[str]]] = []
    current: dict[str, list[str]] = {}
    used = 0
    for folder, entries in folders.items():
        # +2 for the blank line joining blocks.
        cost = len(format_folder_block(folder, entries)) + 2
        if current and (len(current) >= FOLDER_CHUNK_MAX_FOLDERS or used + cost > budget):
            chunks.append(current)
            current, used = {}, 0
        current[folder] = entries
        used += cost
    if current:
        chunks.append(current)
    return chunks


def _describe_chunk(chunk: dict[str, list[str]], lang: str, should_stop: Callable[[], bool] | None) -> dict:
    """One chunk's {folder: summary} from the LLM; {} on any failure or
    when `should_stop()` is already true. Never raises -- a failure here
    must not abort pack() and lose the already-paid file summaries."""
    if should_stop is not None and should_stop():
        return {}
    try:
        result = json.loads(analyze_folder_summaries(chunk, lang=lang))
    except Exception:
        return {}
    if not isinstance(result, dict):
        return {}
    return {folder: summary for folder, summary in result.items() if folder in chunk and isinstance(summary, str)}


def generate_folder_summaries(
    files_data: dict, lang: str = "en", should_stop: Callable[[], bool] | None = None,
) -> dict[str, str]:
    """{folder: summary} for every folder in `files_data`. Multi-file
    folders are described by the LLM in chunks (chunk_folders()) run on a
    FOLDER_MAX_WORKERS thread pool; any folder a chunk didn't cover (call
    failed, bad JSON, key not echoed, or `should_stop()` true before the
    chunk started) and every one-file folder gets the free structural
    sentence instead -- no retry loop, no checkpoint (see module
    docstring). Output order follows group_files_by_folder()."""
    folder_files = group_files_by_folder(files_data)
    if not folder_files:
        return {}

    # packager._assemble_aif() drops one-file folders (their summary would
    # just restate the file's own), so they never go to the LLM.
    to_describe = {folder: entries for folder, entries in folder_files.items() if len(entries) > 1}
    chunks = chunk_folders(to_describe)

    folder_summaries: dict[str, str] = {}
    if len(chunks) == 1:
        folder_summaries = _describe_chunk(chunks[0], lang, should_stop)
    elif chunks:
        # copy_context() per chunk: worker threads don't inherit the
        # caller's ContextVars (progress_i18n's log language) on their own.
        with ThreadPoolExecutor(max_workers=min(FOLDER_MAX_WORKERS, len(chunks))) as pool:
            futures = [
                pool.submit(contextvars.copy_context().run, _describe_chunk, chunk, lang, should_stop)
                for chunk in chunks
            ]
            for future in futures:
                folder_summaries.update(future.result())

    return {
        folder: folder_summaries.get(folder) or _structural_folder_summary(entries, lang)
        for folder, entries in folder_files.items()
    }
