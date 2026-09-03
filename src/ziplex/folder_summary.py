"""Per-folder summary generation for pack() -- what role each folder plays
in the project, the aggregate-level counterpart to summarizer.py's per-file
summaries. Split into its own module for the same reason summarizer.py is
its own module: packager.py stays the pipeline stitcher, not the owner of
"how a folder's summary actually gets generated."

Deliberately much lighter-weight than summarizer.py's own machinery:
- One folder is never re-summarized across packs the way an unchanged
  file's summary is (freshness.load_previous_summaries()) -- a folder has
  no content hash of its own to compare against, and the cost of always
  regenerating is bounded by folder count, not file count (see
  generate_folder_summaries()'s own docstring for why this is cheap).
- A folder-summary failure is NOT wired into checkpoint.handle_llm_failure()
  the way rules/prompt/every per-file summary are. Rules/prompt/summaries
  feed confidence scoring and the human review flow directly -- losing
  progress on those on an interrupted run is a real cost worth a resumable
  checkpoint. A folder summary is a pure orientation aid layered on top of
  already-generated file summaries -- editable via the same corrector.py/
  edits.py review flow as per-file summaries (edits.set_folder_summary()),
  but with no confidence-based triage of its own (no per-folder confidence
  signal exists, so every folder is shown for review rather than only a
  flagged subset); falling back to a free structural sentence for just the
  folders a single LLM call happened to miss is an acceptable degrade that
  keeps this feature from growing its own checkpoint-schema footprint for a
  comparatively low-stakes step.
"""

import json
from pathlib import Path

from .file.textutil import parent_folder
from .llm import analyze_folder_summaries

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


def generate_folder_summaries(files_data: dict, lang: str = "en") -> dict[str, str]:
    """One LLM call describing every folder in the project at once (a
    project's folder count is always far smaller than its file count, so
    this never needs summarizer.py's own batching/chunking/thread-pool
    machinery -- one call, synchronous, same shape as analyze_rules()/
    analyze_prompt()). Falls back to a free structural sentence (see
    _structural_folder_summary()) for any folder the response didn't
    cover -- missing entirely, a JSON parse failure for the whole
    response, or the model not echoing the exact key -- rather than a
    retry loop or checkpoint escalation; see this module's own docstring
    for why that's an acceptable trade-off here specifically.

    The except clause is deliberately broad (`Exception`, not just
    `json.JSONDecodeError`) -- code review caught that a narrower one
    would leave this module's own "best-effort, never blocks the run"
    promise unmet for anything else `analyze_folder_summaries()` could
    raise (an unexpected response shape, a future provider that raises
    instead of returning `"{}"` on a non-retryable error, ...). Unlike
    rules/prompt, a failure here has no checkpoint safety net at all --
    letting it propagate would abort the whole `pack()` run and lose
    every already-completed (and already paid for) file summary too.
    """
    folder_files = group_files_by_folder(files_data)
    if not folder_files:
        return {}

    try:
        response = analyze_folder_summaries(folder_files, lang=lang)
        folder_summaries = json.loads(response)
        if not isinstance(folder_summaries, dict):
            folder_summaries = {}
    except Exception:
        folder_summaries = {}

    return {
        folder: folder_summaries.get(folder) or _structural_folder_summary(entries, lang)
        for folder, entries in folder_files.items()
    }
