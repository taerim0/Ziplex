import json

from ziplex.flat_export import generate_flat_markdown, _language_tag, export_flat


def _sample_aif():
    return {
        "project": {"name": "My Cool App!", "prompt": "A tiny app for testing."},
        "rules": ["Use camelCase for methods."],
        "folders": {
            "src": {"summary": "Application source.", "confidence": 0.95, "file_count": 2},
        },
        "files": {
            "src/app.py": {"summary": "Entry point.", "confidence": 0.9},
            "src/utils.py": {"summary": "Helper functions.", "confidence": 0.2},
        },
        "relationships": {
            "src/app.py": {"internal": ["src/utils.py"], "external": ["flask"]},
            "src/utils.py": {"internal": [], "external": []},
        },
    }


def _sample_detail():
    return {
        "src/app.py": {"compressed": "def main():\n    ⋮----\n"},
        "src/utils.py": {"compressed": "def helper():\n    ⋮----\n"},
    }


def test_language_tag_known_and_unknown_extensions():
    assert _language_tag("src/app.py") == "python"
    assert _language_tag("main.go") == "go"
    assert _language_tag("README") == ""


def test_generate_flat_markdown_includes_project_header():
    md = generate_flat_markdown(_sample_aif(), _sample_detail())
    assert "# My Cool App!" in md
    assert "A tiny app for testing." in md
    assert "Use camelCase for methods." in md


def test_generate_flat_markdown_inlines_summary_and_code_together():
    # The whole point of this module: no cross-referencing between a
    # separate summary file and a separate detail file -- both must appear
    # right next to each other for the same file.
    md = generate_flat_markdown(_sample_aif(), _sample_detail())
    app_section = md[md.index("`src/app.py`"):md.index("`src/utils.py`")]
    assert "Entry point." in app_section
    assert "def main():" in app_section


def test_generate_flat_markdown_flags_low_confidence_files():
    md = generate_flat_markdown(_sample_aif(), _sample_detail())
    utils_section = md[md.index("`src/utils.py`"):]
    assert "⚠️" in utils_section.splitlines()[0]
    app_section = md[md.index("`src/app.py`"):md.index("`src/utils.py`")]
    assert "⚠️" not in app_section.splitlines()[0]


def test_generate_flat_markdown_includes_folders_when_present():
    md = generate_flat_markdown(_sample_aif(), _sample_detail())
    assert "`src`" in md
    assert "Application source." in md


def test_generate_flat_markdown_skips_files_with_no_dependencies():
    md = generate_flat_markdown(_sample_aif(), _sample_detail())
    dep_section = md[md.index("## Dependency graph"):]
    assert "`src/app.py` -> src/utils.py; external: flask" in dep_section
    assert "src/utils.py` ->" not in dep_section


def test_generate_flat_markdown_handles_missing_folders_key():
    aif = _sample_aif()
    del aif["folders"]
    md = generate_flat_markdown(aif, _sample_detail())
    assert "## Files" in md


def test_export_flat_writes_a_single_file(tmp_path):
    aif_path = tmp_path / "proj.json"
    detail_path = tmp_path / "proj.detail.json"
    aif_path.write_text(json.dumps(_sample_aif()), encoding="utf-8")
    detail_path.write_text(json.dumps(_sample_detail()), encoding="utf-8")

    target = export_flat(str(aif_path))

    assert target == str(tmp_path / "proj.flat.md")
    content = (tmp_path / "proj.flat.md").read_text(encoding="utf-8")
    assert "# My Cool App!" in content
    assert "def main():" in content


def test_export_flat_custom_output_path(tmp_path):
    aif_path = tmp_path / "proj.json"
    detail_path = tmp_path / "proj.detail.json"
    aif_path.write_text(json.dumps(_sample_aif()), encoding="utf-8")
    detail_path.write_text(json.dumps(_sample_detail()), encoding="utf-8")

    custom = tmp_path / "out" / "combined.md"
    custom.parent.mkdir()
    target = export_flat(str(aif_path), str(custom))

    assert target == str(custom)
    assert custom.exists()


def test_fence_outgrows_any_backtick_run_in_the_body():
    # A Markdown body's own ``` blocks closed the fixed ``` wrapper early.
    from ziplex.flat_export import _fence_for

    assert _fence_for("plain code") == "```"
    assert _fence_for("# Doc\n```py\nx = 1\n```\n") == "````"
    assert _fence_for("````` five") == "``````"
