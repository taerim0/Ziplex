"""Per-folder one-line summaries for aif.json's `folders` block: a
deterministic "Contains N files: ..." sentence per folder (no LLM call --
an LLM-written folder pass was never shown to help and cost a request per
40 folders)."""

from pathlib import Path

from .file.textutil import parent_folder

# Files listed by name before collapsing the rest into "+N more".
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
    """Every folder's summary --
    no network, each folder's summary a deterministic "Contains N files:
    ..." sentence built from its own already-grouped file list.
    """
    return {
        folder: _structural_folder_summary(entries, lang)
        for folder, entries in group_files_by_folder(files_data).items()
    }
