"""Pure, I/O-free operations on an in-progress `aif` dict.

Every function here takes plain data and returns plain data -- no input()/
print(), no terminal assumptions. This is the seam `corrector.py`'s
interactive CLI flow is built on top of, and the one `gui/pack_service.py`'s
review flow calls directly with structured requests (a whole submitted form)
instead of parsed terminal strings (one prompt answered at a time). See
file/relationship.py for the equivalent pure API over the dependency graph
itself (build_tree, move_file, has_cycle).

Each function mutates its `aif`/`aif["files"]` argument in place and also
returns it, matching the mutate-and-return style already used by
corrector.py and file/relationship.py -- callers can chain or ignore the
return value.
"""

from .file.relationship import build_tree


def set_project_name(aif: dict, name: str) -> dict:
    aif["project"]["name"] = name
    return aif


def set_project_prompt(aif: dict, prompt: str) -> dict:
    aif["project"]["prompt"] = prompt
    return aif


def add_rule(aif: dict, rule: str) -> dict:
    aif["rules"].append(rule)
    return aif


def remove_rule(aif: dict, index: int) -> dict:
    """index is 0-based. Raises IndexError if out of range."""
    aif["rules"].pop(index)
    return aif


def set_rules(aif: dict, rules: list[str]) -> dict:
    """Replaces the whole rules list at once -- add_rule()/remove_rule()
    above are the one-at-a-time API corrector.py's turn-by-turn terminal
    prompt is built on; a caller that already has the final list in hand
    (gui/pack_service.py's review screen submits one whole edited array, not
    a sequence of individual add/remove actions) uses this instead, so a
    rules edit still goes through edits.py's seam like every other field
    rather than mutating aif["rules"] directly.
    """
    aif["rules"] = list(rules)
    return aif


def set_file_summary(aif: dict, file_name: str, summary: str) -> dict:
    """Raises KeyError if file_name isn't in aif["files"]."""
    if file_name not in aif["files"]:
        raise KeyError(file_name)
    aif["files"][file_name]["summary"] = summary
    return aif


def set_folder_summary(aif: dict, folder: str, summary: str) -> dict:
    """The `aif["folders"]` (see folder_summary.py) counterpart to
    set_file_summary() above -- same shape (`{folder: {"summary": ...}}`),
    same one-field setter. Raises KeyError if folder isn't in
    aif["folders"].
    """
    if folder not in aif.get("folders", {}):
        raise KeyError(folder)
    aif["folders"][folder]["summary"] = summary
    return aif


def finalize_aif(aif: dict) -> dict:
    """Builds `relationships` and prunes now-redundant working-state fields.

    Not itself a correction step -- this always has to run before `aif` is
    shippable, whether or not any of the editing functions above (or
    corrector.py's interactive prompts) ran first. Callers that want a fully
    non-interactive `pack` (auto-accepting whatever the LLM produced, e.g.
    for CI or scripted use) call this directly instead of going through
    corrector.py's `correct_aif()`.

    dependencies drove file/relationship.py's move_file()/build_tree() if any
    reparenting happened (or is untouched from packager.py otherwise);
    signatures fed analyze_rules() back in packager.py. Both are redundant in
    the shipped output once this runs: dependencies is fully represented by
    `relationships` (deduped, split into internal/external), and
    signatures/api duplicate what's already visible inline in `compressed`
    (only function bodies are stripped -- signatures, imports, and decorators
    stay in place).
    """
    aif["relationships"] = build_tree(aif["files"])

    for data in aif["files"].values():
        data.pop("signatures", None)
        data.pop("dependencies", None)
        data.pop("api", None)

    return aif
