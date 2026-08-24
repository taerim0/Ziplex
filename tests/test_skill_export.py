import json
from pathlib import Path

from ziplex.skill_export import _slugify, _yaml_double_quoted, generate_skill_files, export_skill


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


def test_relationships_md_shows_internal_external_and_no_deps():
    files = generate_skill_files(_sample_aif(), {})
    rel_md = files["references/relationships.md"]

    assert "`src/utils.py`" in rel_md  # app.py's internal dependency
    assert "external" in rel_md and "flask" in rel_md
    assert "(no dependencies)" in rel_md  # utils.py has none


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
