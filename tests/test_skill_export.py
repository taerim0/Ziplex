import json
from pathlib import Path

from ziplex.skill_export import (
    _slugify,
    _yaml_double_quoted,
    generate_skill_files,
    export_skill,
    resolve_skill_target,
    resolve_skill_display_name,
    read_existing_skill_project_name,
)


def _sample_aif():
    return {
        "project": {"name": "My Cool App!", "prompt": "A tiny app for testing."},
        "rules": ["Use camelCase for methods."],
        "tokens": {"GPT-4o": {"original": 1000, "compressed": 200, "saved_pct": 80.0}},
        "files": {
            "src/app.py": {"summary": "Entry point.", "confidence": 0.9},
            "src/utils.py": {"summary": "Helpers | with a pipe\nand a newline.", "confidence": 0.3},
        },
        "relationships": {
            "src/app.py": {"internal": ["src/utils.py"], "external": ["flask"]},
            "src/utils.py": {"internal": [], "external": []},
        },
    }


def test_slugify_lowercases_and_collapses_non_alnum():
    assert _slugify("My Cool App!") == "my-cool-app"
    assert _slugify("  --Weird__Name--  ") == "weird-name"
    assert _slugify("") == "project"


def test_generate_skill_files_returns_all_expected_paths():
    files = generate_skill_files(_sample_aif(), {"src/app.py": {"compressed": "..."}})
    assert set(files.keys()) == {
        "SKILL.md",
        "references/overview.md",
        "references/files.md",
        "references/relationships.md",
        "references/detail.json",
    }


def test_skill_md_has_valid_frontmatter_and_mentions_the_project():
    files = generate_skill_files(_sample_aif(), {})
    skill_md = files["SKILL.md"]

    assert skill_md.startswith("---\n")
    assert "name: my-cool-app" in skill_md
    assert "description: \"" in skill_md
    assert "My Cool App!" in skill_md
    assert "A tiny app for testing." in skill_md
    # points at every reference file it promises
    assert "references/overview.md" in skill_md
    assert "references/files.md" in skill_md
    assert "references/relationships.md" in skill_md
    assert "references/detail.json" in skill_md


def test_yaml_double_quoted_escapes_quotes_backslashes_and_newlines():
    assert _yaml_double_quoted('He said "hi"') == 'He said \\"hi\\"'
    assert _yaml_double_quoted("back\\slash") == "back\\\\slash"
    assert _yaml_double_quoted("line1\nline2") == "line1\\nline2"


def test_skill_md_escapes_a_quote_in_the_project_name():
    # An unescaped `"` from a project renamed to include one (corrector.py/
    # the GUI's set_project_name don't validate the new name) would
    # otherwise truncate the YAML frontmatter's description value early.
    aif = _sample_aif()
    aif["project"]["name"] = 'My "Weird" App'
    files = generate_skill_files(aif, {})
    skill_md = files["SKILL.md"]

    desc_line = next(line for line in skill_md.splitlines() if line.startswith("description:"))
    without_escaped_quotes = desc_line.replace('\\"', "")
    # only the two frontmatter-delimiting quotes should remain unescaped
    assert without_escaped_quotes.count('"') == 2


def test_files_md_lists_every_file_sorted_with_escaped_summary():
    files = generate_skill_files(_sample_aif(), {})
    files_md = files["references/files.md"]

    app_idx = files_md.index("src/app.py")
    utils_idx = files_md.index("src/utils.py")
    assert app_idx < utils_idx  # alphabetical

    # the pipe/newline in utils.py's summary would otherwise break the
    # Markdown table it's embedded in
    assert "Helpers \\| with a pipe and a newline." in files_md
    assert "0.90" in files_md
    assert "0.30" in files_md


def test_overview_md_reports_confidence_summary():
    # _sample_aif() has two files: confidence 0.9 (auto-kept) and 0.3
    # (below REVIEW_THRESHOLD) -- average 0.60, one flagged.
    overview_md = generate_skill_files(_sample_aif(), {})["references/overview.md"]
    assert "0.60 average" in overview_md
    assert "1 file(s) flagged for review" in overview_md


def test_overview_md_omits_folders_section_when_field_absent():
    # _sample_aif() has no "folders" key at all -- an aif.json packed
    # before this field existed. Must not crash or render an empty heading.
    files = generate_skill_files(_sample_aif(), {})
    assert "## Folders" not in files["references/overview.md"]


def test_overview_md_lists_folders_when_present():
    aif = _sample_aif()
    aif["folders"] = {
        ".": {"summary": "Top-level files.", "confidence": 1.0, "file_count": 1},
        "src": {"summary": "Core logic.", "confidence": 0.5, "file_count": 2},
    }
    overview_md = generate_skill_files(aif, {})["references/overview.md"]

    assert "## Folders" in overview_md
    assert "`.` (1 file(s), confidence 1.00): Top-level files." in overview_md
    assert "`src` (2 file(s), confidence 0.50): Core logic." in overview_md


def test_overview_md_omits_tech_stack_section_when_field_absent():
    # _sample_aif() has no "tech_stack" key at all -- an aif.json packed
    # before this field existed. Must not crash or render an empty heading.
    files = generate_skill_files(_sample_aif(), {})
    assert "## Tech stack" not in files["references/overview.md"]


def test_overview_md_lists_tech_stack_when_present():
    aif = _sample_aif()
    aif["project"]["tech_stack"] = [{
        "manifest": "requirements.txt",
        "language": "Python",
        "package_manager": "pip",
        "dependencies": ["flask", "requests"],
        "dependencies_truncated": False,
    }]
    overview_md = generate_skill_files(aif, {})["references/overview.md"]

    assert "## Tech stack" in overview_md
    assert "Python" in overview_md
    assert "requirements.txt" in overview_md
    assert "flask, requests" in overview_md


def test_overview_md_marks_a_truncated_dependency_list():
    aif = _sample_aif()
    aif["project"]["tech_stack"] = [{
        "manifest": "package.json",
        "language": "JavaScript/TypeScript",
        "package_manager": "npm",
        "dependencies": ["react"],
        "dependencies_truncated": True,
    }]
    overview_md = generate_skill_files(aif, {})["references/overview.md"]
    assert "react, ..." in overview_md


def test_overview_md_omits_security_scan_section_when_field_absent():
    # Same backward-compat guard as tech_stack -- an aif.json packed before
    # this field existed has no "security_scan" key at all.
    files = generate_skill_files(_sample_aif(), {})
    assert "## Security scan" not in files["references/overview.md"]


def test_overview_md_reports_no_files_flagged():
    aif = _sample_aif()
    aif["project"]["security_scan"] = {"flagged": 0, "included_anyway": 0, "excluded": 0}
    overview_md = generate_skill_files(aif, {})["references/overview.md"]

    assert "## Security scan" in overview_md
    assert "No files were flagged" in overview_md


def test_overview_md_reports_flagged_included_and_excluded_counts():
    aif = _sample_aif()
    aif["project"]["security_scan"] = {"flagged": 3, "included_anyway": 1, "excluded": 2}
    overview_md = generate_skill_files(aif, {})["references/overview.md"]

    assert "3 file(s) flagged" in overview_md
    assert "1 included anyway" in overview_md
    assert "2 left out" in overview_md


def test_relationships_md_shows_internal_external_and_no_deps():
    files = generate_skill_files(_sample_aif(), {})
    rel_md = files["references/relationships.md"]

    assert "`src/utils.py`" in rel_md  # app.py's internal dependency
    assert "external" in rel_md and "flask" in rel_md
    assert "(no dependencies)" in rel_md  # utils.py has none


def test_relationships_md_annotates_a_text_reference_only_edge():
    aif = _sample_aif()
    aif["relationships"]["src/utils.py"] = {
        "internal": ["src/app.py"],
        "external": [],
        "internal_text_refs": ["src/app.py"],
    }
    rel_md = generate_skill_files(aif, {})["references/relationships.md"]

    app_section, utils_section = rel_md.split("## `src/app.py`")[1].split("## `src/utils.py`")
    # app.py's real import of utils.py must not be annotated as a text ref
    assert "text reference" not in app_section
    # utils.py's edge back to app.py exists only as a text reference
    assert "`src/app.py` (text reference, not an import)" in utils_section


def test_detail_json_round_trips():
    detail = {"src/app.py": {"compressed": "def main():\n    ...\n"}}
    files = generate_skill_files(_sample_aif(), detail)
    assert json.loads(files["references/detail.json"]) == detail


def test_export_skill_writes_to_default_slugified_directory(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    aif_path = tmp_path / "result" / "My Cool App!.json"
    aif_path.parent.mkdir()
    aif_path.write_text(json.dumps(_sample_aif()), encoding="utf-8")
    detail_path = aif_path.with_name("My Cool App!.detail.json")
    detail_path.write_text(json.dumps({"src/app.py": {"compressed": "x"}}), encoding="utf-8")

    target = export_skill(str(aif_path))

    # relative to the (monkeypatched) cwd, matching where Claude Code looks
    # for project-level skills -- not resolved to an absolute path
    assert target == str(Path(".claude") / "skills" / "my-cool-app")
    written = tmp_path / ".claude" / "skills" / "my-cool-app"
    assert (written / "SKILL.md").exists()
    assert (written / "references" / "files.md").exists()
    assert json.loads((written / "references" / "detail.json").read_text(encoding="utf-8")) == {
        "src/app.py": {"compressed": "x"}
    }


def test_export_skill_honors_custom_output_dir(tmp_path):
    aif_path = tmp_path / "out.json"
    aif_path.write_text(json.dumps(_sample_aif()), encoding="utf-8")
    custom = tmp_path / "somewhere" / "else"

    target = export_skill(str(aif_path), str(custom))

    assert target == str(custom)
    assert (custom / "SKILL.md").exists()


def test_export_skill_tolerates_a_missing_detail_json(tmp_path):
    # no sibling <name>.detail.json written at all
    aif_path = tmp_path / "out.json"
    aif_path.write_text(json.dumps(_sample_aif()), encoding="utf-8")

    target = export_skill(str(aif_path), str(tmp_path / "skill"))

    detail = json.loads((Path(target) / "references" / "detail.json").read_text(encoding="utf-8"))
    assert detail == {}


# Real gap found by code review: no collision guard at all on export_skill()'s
# slug-derived output directory -- two differently-named projects that
# happen to slugify to the same name (a monorepo packing multiple
# subprojects with generic names like "backend"/"api" is a real, plausible
# way to hit this) silently overwrite each other's skill directory. These
# two helpers are what cli.py's own warn-before-overwrite check is built on.
def test_resolve_skill_target_uses_the_slugified_project_name_by_default():
    aif = {"project": {"name": "My Cool App!"}}
    assert resolve_skill_target(aif) == Path(".claude/skills") / "my-cool-app"


def test_resolve_skill_target_honors_an_explicit_output_dir():
    aif = {"project": {"name": "My Cool App!"}}
    assert resolve_skill_target(aif, "somewhere/else") == Path("somewhere/else")


def test_read_existing_skill_project_name_returns_none_when_nothing_is_there(tmp_path):
    assert read_existing_skill_project_name(tmp_path / "does-not-exist") is None


def test_resolve_skill_display_name_uses_project_name_when_set():
    assert resolve_skill_display_name({"project": {"name": "My Cool App!"}}) == "My Cool App!"


# Real gap found by code review: cli.py's collision check used to compute
# this fallback independently as `project.get("name") or ""` -- missing
# _skill_md()'s own `name or slug` fallback -- so a project with an
# empty/missing name always mismatched the "project" heading _skill_md()
# actually wrote, triggering a false-positive collision warning on every
# re-export of that same project.
def test_resolve_skill_display_name_falls_back_to_the_slug_when_name_is_empty():
    assert resolve_skill_display_name({"project": {"name": ""}}) == "project"
    assert resolve_skill_display_name({"project": {}}) == "project"
    assert resolve_skill_display_name({}) == "project"


def test_export_skill_accepts_a_pre_parsed_aif_and_skips_reading_it_again(tmp_path):
    # aif_path still points at a real file (it locates the sibling
    # detail.json), but its own content is never read: a bogus one here
    # proves the `aif` kwarg -- not the file -- was what actually got used.
    aif_path = tmp_path / "out.json"
    aif_path.write_text("not valid json", encoding="utf-8")

    target = export_skill(str(aif_path), str(tmp_path / "skill"), aif=_sample_aif())

    assert "My Cool App!" in (Path(target) / "SKILL.md").read_text(encoding="utf-8")


def test_read_existing_skill_project_name_returns_none_for_an_unrecognized_skill_md(tmp_path):
    # A human-authored (or pre-this-format) SKILL.md that doesn't match the
    # exact "# {name} -- Ziplex reference" heading shape this module writes.
    target = tmp_path / "skill"
    target.mkdir()
    (target / "SKILL.md").write_text("---\nname: hi\n---\n\n# Just a heading\n", encoding="utf-8")

    assert read_existing_skill_project_name(target) is None


def test_read_existing_skill_project_name_extracts_the_display_name(tmp_path):
    target = tmp_path / "skill"
    aif = _sample_aif()
    export_skill(_write_sample_aif_json(tmp_path, aif), str(target))

    assert read_existing_skill_project_name(target) == "My Cool App!"


def _write_sample_aif_json(tmp_path, aif) -> str:
    aif_path = tmp_path / "src_aif.json"
    aif_path.write_text(json.dumps(aif), encoding="utf-8")
    return str(aif_path)


def test_read_existing_skill_project_name_detects_a_slug_collision_from_a_different_project(tmp_path):
    # The exact scenario this whole guard exists for: two differently-named
    # projects that slugify to the identical directory.
    target = tmp_path / "skill"
    first = _sample_aif()
    first["project"]["name"] = "My_Cool App"  # slugifies the same as "My Cool App!"
    export_skill(_write_sample_aif_json(tmp_path, first), str(target))

    existing_name = read_existing_skill_project_name(target)
    second = _sample_aif()  # "My Cool App!"
    new_name = second["project"]["name"]

    assert _slugify(existing_name) == _slugify(new_name)  # same slug...
    assert existing_name != new_name  # ...but a genuinely different project
