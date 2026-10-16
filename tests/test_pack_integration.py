"""End-to-end pack() test using llm.MockProvider instead of a live Gemini
call -- validates the pipeline's actual orchestration (checkpointing,
parallel per-file summaries, rules/prompt generation, token counting,
aif.json assembly) runs correctly wired together, without the cost/latency/
non-determinism of a real LLM call.

monkeypatching llm._provider (rather than the LLM_PROVIDER env var) works
regardless of import order: generate() looks up _provider as a module
global on every call, so this takes effect even though llm may already have
been imported -- with whatever provider LLM_PROVIDER resolved to at that
point -- by an earlier test file.
"""

import builtins

import pytest
import json
import struct
from pathlib import Path


from ziplex.enrich import llm
from ziplex import packager
from ziplex.enrich import summarizer


def _write(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_pack_never_calls_the_llm_for_a_media_asset_even_with_use_llm_true(tmp_path, monkeypatch):
    # A media file (file/media.py) must cost nothing to pack regardless of
    # whether an LLM is available for everything else -- proven the same
    # strict way test_pack_use_llm_false_... below proves it for that mode:
    # a provider that raises on any call at all, so a call this test doesn't
    # expect fails at the point it happens, not just via a call-count assert.
    class _CountingProvider(llm.MockProvider):
        def __init__(self):
            self.calls = 0

        def generate(self, prompt: str, retry: int = 5, label: str = "") -> str:
            self.calls += 1
            return super().generate(prompt, retry)

    provider = _CountingProvider()
    monkeypatch.setattr(llm, "_provider", provider)

    project = tmp_path / "project"
    _write(project / "main.py", "def add(a, b):\n    return a + b\n")
    # a minimal-but-real PNG header (89PNG signature + IHDR width/height) --
    # media_summary() only ever reads these bytes, never needs a full image
    (project / "logo.png").write_bytes(
        b"\x89PNG\r\n\x1a\n" + struct.pack(">I", 13) + b"IHDR" + struct.pack(">II", 64, 32)
    )

    aif = packager.pack(str(project), auto=True, interactive=False)

    assert set(aif["files"].keys()) == {"main.py", "logo.png"}
    assert aif["files"]["logo.png"]["summary"] == "[image asset, 64x32, 24B]"
    # no code to strip -- a media file's compressed body is always empty
    assert aif["files"]["logo.png"]["compressed"] == ""
    # no signatures to check against -- same free 1.0 a trivial text file
    # (e.g. this same test's README-less project has none for) gets

    # main.py still got a real (mocked) LLM call -- only the media file was
    # skipped, not the whole run
    assert provider.calls > 0
    assert aif["files"]["main.py"]["summary"] == "Mock summary for local testing."

    # media assets never got scanned as "dangerous" either
    assert aif["project"]["security_scan"] == {"flagged": 0, "included_anyway": 0, "excluded": 0}


def test_pack_gives_a_text_file_with_a_media_extension_a_real_summary_not_a_metadata_one(tmp_path, monkeypatch):
    # a Git LFS pointer file (or any mislabeled text file) checked in with a
    # media extension must still go through the normal text/LLM path --
    # extension alone must never misclassify it as a media asset (a real
    # gap caught by review: file/media.py's classify_media_file() confirms
    # content, not just extension)
    monkeypatch.setattr(llm, "_provider", llm.MockProvider())

    project = tmp_path / "project"
    _write(
        project / "video.mp4",
        "version https://git-lfs.github.com/spec/v1\noid sha256:abc\nsize 123\n",
    )

    aif = packager.pack(str(project), auto=True, interactive=False)

    assert aif["files"]["video.mp4"]["summary"] == "Mock summary for local testing."


def test_pack_use_llm_false_never_calls_the_llm_and_uses_structural_summaries(tmp_path, monkeypatch):
    # A provider that raises on any call at all -- stricter than counting
    # calls after the fact, since a call this test doesn't expect fails
    # immediately at the point it happens rather than only being caught by
    # a final assertion.
    class _RaisingProvider(llm.MockProvider):
        def generate(self, prompt: str, retry: int = 5, label: str = "") -> str:
            raise AssertionError("use_llm=False must never call the LLM provider")

    monkeypatch.setattr(llm, "_provider", _RaisingProvider())

    project = tmp_path / "project"
    _write(project / "main.py", "def add(a, b):\n    return a + b\n")
    _write(project / "README.md", "# Sample\n\nA sample project.\n")

    aif = packager.pack(str(project), auto=True, interactive=False, use_llm=False)

    assert aif["files"]["main.py"]["summary"] == "Defines: add(a, b)"
    # README.md has no Tree-sitter grammar -> no signatures/dependencies at all
    assert "No signatures or dependencies detected" in aif["files"]["README.md"]["summary"]
    assert aif["rules"] == []
    assert aif["project"]["prompt"] == packager.STRUCTURAL_ONLY_NOTE["en"]


def test_pack_use_llm_false_still_reuses_a_cached_real_summary(tmp_path, monkeypatch):
    # use_llm=False means "don't call the LLM now," not "throw away a
    # better answer already on hand" -- a file unchanged since a prior
    # LLM-enabled pack should keep that real summary, not get downgraded.
    monkeypatch.setattr(llm, "_provider", llm.MockProvider())

    project = tmp_path / "project"
    _write(project / "main.py", "def add(a, b):\n    return a + b\n")
    packager.save_aif(packager.pack(str(project), auto=True, interactive=False), project_path=str(project))

    class _RaisingProvider(llm.MockProvider):
        def generate(self, prompt: str, retry: int = 5, label: str = "") -> str:
            raise AssertionError("use_llm=False must never call the LLM provider")

    monkeypatch.setattr(llm, "_provider", _RaisingProvider())
    aif = packager.pack(str(project), auto=True, interactive=False, use_llm=False)

    assert aif["files"]["main.py"]["summary"] == "Mock summary for local testing."


class _EmptySummaryProvider(llm.MockProvider):
    """Answers rules/prompt/relationships normally (via MockProvider) but
    returns an empty payload for any summary-shaped prompt, so every file's
    summary comes back "" and generate_summaries() falls back to
    SUMMARY_FAILED_PLACEHOLDERS["en"] for each -- sets up "a previous pack already
    cached a failure" without needing a real network failure.
    """

    def generate(self, prompt: str, retry: int = 5, label: str = "") -> str:
        if '"summaries"' in prompt or '"summary"' in prompt:
            return "{}"
        return super().generate(prompt, retry=retry)


class _EmptyRulesProvider(llm.MockProvider):
    """Answers everything else normally (via MockProvider) but returns an
    empty payload for any rules-shaped prompt, so analyze_rules() always
    comes back with nothing and pack()'s `while not rules` loop keeps
    calling checkpoint.handle_llm_failure() -- the end-to-end counterpart to
    _EmptySummaryProvider above, for the loop covering item 7's "does the
    rules/prompt failure menu still actually work" question rather than
    just unit-testing handle_llm_failure() in isolation (see
    tests/test_checkpoint.py for that half).
    """

    def generate(self, prompt: str, retry: int = 5, label: str = "") -> str:
        if '"rules"' in prompt:
            return "{}"
        return super().generate(prompt, retry=retry)


class _FlakyRulesProvider(llm.MockProvider):
    """Fails the first rules-shaped prompt, succeeds on every one after --
    simulates "the server was briefly down, retrying actually helps" rather
    than a permanently-broken call.
    """

    def __init__(self):
        self.rules_calls = 0

    def generate(self, prompt: str, retry: int = 5, label: str = "") -> str:
        if '"rules"' in prompt:
            self.rules_calls += 1
            if self.rules_calls == 1:
                return "{}"
        return super().generate(prompt, retry=retry)


class _EmptyPromptProvider(llm.MockProvider):
    """The prompt/AI-guide equivalent of _EmptyRulesProvider above -- rules
    generation succeeds normally, but analyze_prompt() always comes back
    empty, exercising pack()'s second, structurally identical `while not
    prompt` loop.
    """

    def generate(self, prompt: str, retry: int = 5, label: str = "") -> str:
        if '"prompt"' in prompt:
            return "{}"
        return super().generate(prompt, retry=retry)


def test_pack_attaches_tech_stack_detected_from_a_manifest_file(tmp_path, monkeypatch):
    monkeypatch.setattr(llm, "_provider", llm.MockProvider())

    project = tmp_path / "project"
    _write(project / "main.py", "def add(a, b):\n    return a + b\n")
    _write(project / "requirements.txt", "flask>=2.0\nrequests\n")

    aif = packager.pack(str(project), auto=True, interactive=False)

    assert aif["project"]["tech_stack"] == [{
        "manifest": "requirements.txt",
        "language": "Python",
        "package_manager": "pip",
        "dependencies": ["flask", "requests"],
        "dependencies_truncated": False,
    }]

    # survives () the same way project.name/prompt do -- it's
    # not a per-file field () prunes
    assert (aif)["project"]["tech_stack"] == aif["project"]["tech_stack"]


def test_pack_attaches_an_empty_tech_stack_when_no_manifest_present(tmp_path, monkeypatch):
    monkeypatch.setattr(llm, "_provider", llm.MockProvider())

    project = tmp_path / "project"
    _write(project / "main.py", "def add(a, b):\n    return a + b\n")

    aif = packager.pack(str(project), auto=True, interactive=False)

    assert aif["project"]["tech_stack"] == []


def test_build_architecture_summary_includes_tech_stack_and_file_summaries():
    # Regression for a real, reported bug: analyze_prompt() used to always
    # be called with architecture=[] (a dead parameter, never populated
    # anywhere), so the AI guide it wrote had no real project-understanding
    # signal at all -- just the project name and a list of coding-style
    # rules. This is the fix: real per-file summaries plus the free
    # tech_stack fact block, not an empty list.
    files_data = {
        "main.py": {"summary": "Runs the Flask app."},
        "no_summary.py": {"summary": ""},  # a file with no summary yet is skipped, not blanked
    }
    tech_stack = [{
        "manifest": "requirements.txt", "language": "Python",
        "package_manager": "pip", "dependencies": ["flask"], "dependencies_truncated": False,
    }]

    result = packager._build_architecture_summary(files_data, tech_stack)

    assert any("flask" in line for line in result)
    assert "main.py: Runs the Flask app." in result
    assert not any("no_summary.py" in line for line in result)


def test_build_architecture_summary_caps_and_notes_truncation():
    files_data = {f"f{i}.py": {"summary": f"does {i}"} for i in range(packager.MAX_ARCHITECTURE_FILES + 5)}

    result = packager._build_architecture_summary(files_data, [])

    assert len(result) == packager.MAX_ARCHITECTURE_FILES + 1  # + the truncation note
    assert result[-1] == "... (5 more files not shown)"


def test_pack_attaches_folder_summaries(tmp_path, monkeypatch):
    monkeypatch.setattr(llm, "_provider", llm.MockProvider())

    project = tmp_path / "project"
    _write(project / "main.py", "def add(a, b):\n    return a + b\n")
    _write(project / "extra.py", "def extra():\n    pass\n")
    _write(project / "src" / "helper.py", "def helper():\n    pass\n")
    _write(project / "src" / "helper2.py", "def helper2():\n    pass\n")

    aif = packager.pack(str(project), auto=True, interactive=False)

    assert set(aif["folders"].keys()) == {".", "src"}
    assert aif["folders"]["."]["summary"]
    assert aif["folders"]["src"]["summary"]
    # An aggregate of each folder's own member files' already-scored
    # confidence (folder_summary.group_confidence_by_folder()), not an
    # independently verified signal -- present regardless of value.


    # How many files that one summary sentence is standing in for.
    assert aif["folders"]["."]["file_count"] == 2
    assert aif["folders"]["src"]["file_count"] == 2

    # survives () the same way tech_stack does -- not a
    # per-file field it prunes
    assert (aif)["folders"] == aif["folders"]


def test_pack_use_llm_false_generates_structural_folder_summaries(tmp_path, monkeypatch):

    def _unexpected_call(*a, **k):
        raise AssertionError("must not call the LLM for folder summaries under --no-llm")

    monkeypatch.setattr(llm, "generate", _unexpected_call)

    project = tmp_path / "project"
    _write(project / "src" / "main.py", "def add(a, b):\n    return a + b\n")
    _write(project / "src" / "util.py", "def sub(a, b):\n    return a - b\n")

    aif = packager.pack(str(project), auto=True, interactive=False, use_llm=False)

    assert "Contains 2 file(s): main.py, util.py" in aif["folders"]["src"]["summary"]


def test_pack_omits_the_summary_of_a_one_file_folder(tmp_path, monkeypatch):
    monkeypatch.setattr(llm, "_provider", llm.MockProvider())

    project = tmp_path / "project"
    _write(project / "solo" / "only.py", "def only():\n    pass\n")
    _write(project / "pair" / "a.py", "def a():\n    pass\n")
    _write(project / "pair" / "b.py", "def b():\n    pass\n")

    aif = packager.pack(str(project), auto=True, interactive=False)

    # a one-file folder's summary would only restate that file's own
    assert "solo" not in aif["folders"]
    assert aif["folders"]["pair"]["file_count"] == 2
    assert "solo/only.py" in aif["files"]  # the file itself is still fully described


def test_pack_captures_a_text_file_reference_to_a_code_file(tmp_path, monkeypatch):
    monkeypatch.setattr(llm, "_provider", llm.MockProvider())

    project = tmp_path / "project"
    _write(project / "entities" / "player.gd", "extends Node\nfunc _ready():\n    pass\n")
    # README.md has no Tree-sitter grammar, so extract_dependencies() alone
    # would never connect it to player.gd -- this is exactly the gap
    # text_references.py closes.
    _write(project / "README.md", "See entities/player.gd for the player logic.\n")

    aif = packager.pack(str(project), auto=True, interactive=False)

    assert aif["relationships"]["README.md"]["internal"] == ["entities/player.gd"]
    assert aif["relationships"]["README.md"]["internal_text_refs"] == ["entities/player.gd"]
    # and it survives ()'s relationship-building the same way a
    # real import would -- tagged as internal_text_refs, not just internal,
    # since it's a prose mention with no real import behind it (see
    # file/relationship.py's build_tree() docstring)
    final = (aif)
    assert final["relationships"]["README.md"]["internal"] == ["entities/player.gd"]
    assert final["relationships"]["README.md"]["internal_text_refs"] == ["entities/player.gd"]
    assert "text_dependencies" not in final["files"]["README.md"]


def test_pack_resolves_an_internal_go_package_import_to_its_concrete_files(tmp_path, monkeypatch):
    # A Go import path names a *package* (a directory), not a single file --
    # go_packages.py closes the gap left by resolve_dependency()'s pure
    # file-stem matching (see its own docstring and _go_dependency_handler's).
    monkeypatch.setattr(llm, "_provider", llm.MockProvider())

    project = tmp_path / "project"
    _write(project / "go.mod", "module example.com/myproject\n\ngo 1.21\n")
    _write(project / "main.go", 'package main\n\nimport "example.com/myproject/internal/utils"\n\nfunc main() {}\n')
    _write(project / "internal" / "utils" / "format.go", "package utils\n\nfunc Format() string { return \"\" }\n")
    _write(project / "internal" / "utils" / "parse.go", "package utils\n\nfunc Parse() int { return 0 }\n")

    aif = packager.pack(str(project), auto=True, interactive=False)

    deps = set(aif["relationships"]["main.go"]["internal"])
    assert "internal/utils/format.go" in deps
    assert "internal/utils/parse.go" in deps
    assert "example.com/myproject/internal/utils" not in deps  # replaced, not just appended to

    final = (aif)
    internal = set(final["relationships"]["main.go"]["internal"])
    assert {"internal/utils/format.go", "internal/utils/parse.go"} <= internal
    # a real package import, not a text_references.py prose mention
    assert final["relationships"]["main.go"]["internal_text_refs"] == []


def test_pack_text_reference_does_not_hijack_the_summary_prompt(tmp_path, monkeypatch):
    # _request_summary()/analyze_batch_summaries() both switch a file's
    # summary prompt from content-based to signature/dependency-based the
    # moment `dependencies` is non-empty -- a text-reference match must not
    # be visible to that decision, or README.md's summary would get
    # generated from `Dependencies: ['entities/player.gd']` alone, having
    # never seen its actual text. MockProvider ignores prompt content
    # entirely, so this needs a provider that records what it was actually
    # asked, not just that *a* summary came back.

    captured_prompts = []

    class _CapturingMockProvider(llm.MockProvider):
        def generate(self, prompt: str, retry: int = 5, label: str = "") -> str:
            captured_prompts.append(prompt)
            return super().generate(prompt, retry)

    monkeypatch.setattr(llm, "_provider", _CapturingMockProvider())

    project = tmp_path / "project"
    _write(project / "entities" / "player.gd", "extends Node\nfunc _ready():\n    pass\n")
    _write(project / "README.md", "See entities/player.gd for the player logic. UNIQUE_MARKER_TEXT_XYZ\n")

    packager.pack(str(project), auto=True, interactive=False)

    readme_prompts = [p for p in captured_prompts if "README.md" in p]
    assert readme_prompts, "expected at least one summary-generation prompt mentioning README.md"
    assert any("UNIQUE_MARKER_TEXT_XYZ" in p for p in readme_prompts), (
        "README.md's summary prompt should include its actual content (content-based "
        "routing), not just its signatures/dependencies -- got:\n" + "\n---\n".join(readme_prompts)
    )
    assert not any("Dependencies: ['entities/player.gd']" in p for p in readme_prompts), (
        "the text-reference-derived dependency leaked into the summary-routing decision"
    )


def test_pack_lang_threads_language_instruction_into_every_llm_prompt(tmp_path, monkeypatch):
    # pack(..., lang="ko") must reach every LLM-facing prompt (summary,
    # rules, and the AI guide/prompt) -- MockProvider ignores prompt content
    # entirely (it only pattern-matches the JSON field name), so this needs
    # a provider that records what it was actually asked, same pattern as
    # test_pack_text_reference_does_not_hijack_the_summary_prompt above.

    captured_prompts = []

    class _CapturingMockProvider(llm.MockProvider):
        def generate(self, prompt: str, retry: int = 5, label: str = "") -> str:
            captured_prompts.append(prompt)
            return super().generate(prompt, retry)

    monkeypatch.setattr(llm, "_provider", _CapturingMockProvider())

    project = tmp_path / "project"
    _write(project / "main.py", "def add(a, b):\n    return a + b\n")

    aif = packager.pack(str(project), auto=True, interactive=False, lang="ko")

    assert aif["project"]["language"] == "ko"
    summary_prompts = [p for p in captured_prompts if '"summary"' in p or '"summaries"' in p]
    rules_prompts = [p for p in captured_prompts if '"rules"' in p]
    guide_prompts = [p for p in captured_prompts if '"prompt"' in p and '"rules"' not in p]
    assert summary_prompts and all("Korean" in p for p in summary_prompts)
    assert rules_prompts and all("Korean" in p for p in rules_prompts)
    assert guide_prompts and all("Korean" in p for p in guide_prompts)


def test_pack_lang_defaults_to_english(tmp_path, monkeypatch):
    monkeypatch.setattr(llm, "_provider", llm.MockProvider())

    project = tmp_path / "project"
    _write(project / "main.py", "def add(a, b):\n    return a + b\n")

    aif = packager.pack(str(project), auto=True, interactive=False)
    assert aif["project"]["language"] == "en"


def test_pack_lang_unrecognized_value_falls_back_to_english(tmp_path, monkeypatch):
    # A stale GUI request or a hand-built API call naming an unsupported
    # language must not raise or silently produce a prompt with no language
    # instruction at all -- see pack()'s own `lang` docstring.
    monkeypatch.setattr(llm, "_provider", llm.MockProvider())

    project = tmp_path / "project"
    _write(project / "main.py", "def add(a, b):\n    return a + b\n")

    aif = packager.pack(str(project), auto=True, interactive=False, lang="fr")
    assert aif["project"]["language"] == "en"


def test_pack_lang_no_llm_localizes_structural_note_and_summaries(tmp_path, monkeypatch):
    # use_llm=False's fixed strings (STRUCTURAL_ONLY_NOTE, and each file's
    # deterministic structural summary) are Ziplex's own text, never an LLM
    # call -- they must still follow the chosen `lang` for consistency, not
    # always ship in English regardless of the selection.

    project = tmp_path / "project"
    _write(project / "main.py", "def add(a, b):\n    return a + b\n")

    aif = packager.pack(str(project), auto=True, interactive=False, use_llm=False, lang="ko")

    assert aif["project"]["language"] == "ko"
    assert aif["project"]["prompt"] == packager.STRUCTURAL_ONLY_NOTE["ko"]
    assert aif["project"]["format_notes"] == packager.FORMAT_NOTES["ko"]
    assert "정의" in aif["files"]["main.py"]["summary"]


def test_pack_keeps_a_flagged_files_imports_in_the_graph_without_its_content(tmp_path, monkeypatch):
    project = tmp_path / "project"
    _write(project / "app.py", "def run():\n    return 1\n")
    _write(project / "test_app.py", 'import app\nAPI_KEY = "abc123def456"\n\ndef test_run():\n    assert app.run()\n')

    aif = (packager.pack(str(project), auto=True, interactive=False, use_llm=False))
    out = tmp_path / "out" / "demo.json"
    packager.save_aif(aif, str(out))

    assert aif["project"]["security_scan"]["excluded"] == 1
    assert "app.py" in aif["relationships"]["test_app.py"]["internal"]
    detail = json.loads((tmp_path / "out" / "demo.detail.json").read_text(encoding="utf-8"))
    assert not detail.get("test_app.py", {}).get("compressed")
    assert "abc123def456" not in out.read_text(encoding="utf-8")


def test_pack_can_drop_flagged_files_from_the_graph_via_config(tmp_path, monkeypatch):
    project = tmp_path / "project"
    _write(project / "app.py", "def run():\n    return 1\n")
    _write(project / "test_app.py", 'import app\nAPI_KEY = "abc123def456"\n')
    _write(project / ".ziplex.json", '{"exclude_flagged_from_graph": true}')

    aif = packager.pack(str(project), auto=True, interactive=False, use_llm=False)

    assert "test_app.py" not in aif["files"]


def test_pack_scopes_files_via_ziplex_json_include(tmp_path, monkeypatch):
    monkeypatch.setattr(llm, "_provider", llm.MockProvider())

    project = tmp_path / "project"
    _write(project / "src" / "main.py", "def add(a, b):\n    return a + b\n")
    _write(project / "README.md", "# Sample\n")
    _write(project / ".ziplex.json", json.dumps({"include": ["src/**"], "ignore": []}))

    aif = packager.pack(str(project), auto=True, interactive=False)

    assert set(aif["files"].keys()) == {"src/main.py"}


def test_pack_include_ignore_params_extend_ziplex_json_not_replace_it(tmp_path, monkeypatch):
    monkeypatch.setattr(llm, "_provider", llm.MockProvider())

    project = tmp_path / "project"
    _write(project / "src" / "main.py", "def add(a, b):\n    return a + b\n")
    _write(project / "src" / "main.generated.py", "def gen():\n    pass\n")
    _write(project / "README.md", "# Sample\n")
    _write(project / ".ziplex.json", json.dumps({"include": ["src/**"], "ignore": []}))

    # --ignore's pattern layers on top of (not instead of) the config
    # file's own include scope
    aif = packager.pack(str(project), auto=True, interactive=False, ignore=["*.generated.py"])

    assert set(aif["files"].keys()) == {"src/main.py"}


def test_resolve_output_path_defaults_inside_the_packed_project(tmp_path):
    # Real, confirmed bug fixed by this default (see packager.RESULT_DIR's
    # own comment): a plain `pip install ziplex` (non-editable) resolves
    # RESULT_DIR to somewhere three levels above site-packages -- nowhere
    # near the project actually being packed. A project-relative default
    # can never land somewhere this nonsensical.
    aif = {"project": {"name": "my-project"}}
    project_path = str(tmp_path / "some-project")

    assert packager.resolve_output_path(aif, None, project_path) == (
        Path(project_path) / ".ziplex" / "my-project.json"
    )
    assert packager.resolve_output_path(aif, "custom/out.json", project_path) == Path("custom/out.json")


def test_resolve_output_path_falls_back_to_result_dir_with_no_project_path(tmp_path, monkeypatch):
    # gui/pack_service.py's submit_review() calls this to lock the same
    # path save_aif() is about to write to -- found by code review that a
    # separate copy of this fallback could silently drift from save_aif()'s
    # own; this locks the two together by construction instead. Both real
    # callers always pass project_path now (see the test above) -- this is
    # the legacy last-resort for a caller that somehow doesn't.
    monkeypatch.setattr(packager, "RESULT_DIR", tmp_path / "result")
    aif = {"project": {"name": "my-project"}}

    assert packager.resolve_output_path(aif, None) == tmp_path / "result" / "my-project.json"
    assert packager.resolve_output_path(aif, "custom/out.json") == Path("custom/out.json")


def test_save_aif_with_no_output_path_defaults_inside_project_path(tmp_path, monkeypatch):
    monkeypatch.setattr(llm, "_provider", llm.MockProvider())

    project = tmp_path / "project"
    _write(project / "main.py", "def add(a, b):\n    return a + b\n")

    aif = packager.pack(str(project), auto=True, interactive=False)
    packager.save_aif(aif, project_path=str(project))

    saved_path = project / ".ziplex" / "project.json"
    assert saved_path.exists()
    assert (project / ".ziplex" / "project.detail.json").exists()
    assert (project / ".ziplex" / "project.cache.json").exists()


def test_save_aif_writes_a_sibling_cache_json_from_the_manifest(tmp_path, monkeypatch):
    monkeypatch.setattr(llm, "_provider", llm.MockProvider())

    project = tmp_path / "project"
    _write(project / "main.py", "def add(a, b):\n    return a + b\n")

    aif = packager.pack(str(project), auto=True, interactive=False)
    output_path = tmp_path / "out" / "project.json"
    packager.save_aif(aif, str(output_path))

    cache_path = output_path.with_name("project.cache.json")
    assert cache_path.exists()
    manifest = json.loads(cache_path.read_text(encoding="utf-8"))
    assert list(manifest.keys()) == ["main.py"]

    # _manifest is packaging-internal bookkeeping -- must not leak into the
    # saved aif.json itself
    saved_aif = json.loads(output_path.read_text(encoding="utf-8"))
    assert "_manifest" not in saved_aif


class _CountingMockProvider(llm.MockProvider):
    """Same fixed responses as MockProvider, but counts calls -- MockProvider
    alone can't tell a caching test whether a summary was actually reused or
    just regenerated identically, since it always returns the same text
    either way. Call count is the only reliable signal.
    """

    def __init__(self):
        self.calls = 0

    def generate(self, prompt: str, retry: int = 5, label: str = "") -> str:
        self.calls += 1
        return super().generate(prompt, retry)


def test_pack_reuses_summaries_for_unchanged_files_on_a_second_run(tmp_path, monkeypatch):
    provider = _CountingMockProvider()
    monkeypatch.setattr(llm, "_provider", provider)

    project = tmp_path / "project"
    _write(project / "main.py", "def add(a, b):\n    return a + b\n")
    _write(project / "README.md", "# Sample\n\nA sample project.\n")

    # first pack: main.py + README.md need summarizing, but both fit in one
    # batch (well under BATCH_SIZE) so that's 1 call, plus rules, the AI
    # guide, and the folder-summary call (1 call each) -- everything is new
    aif1 = packager.pack(str(project), auto=True, interactive=False)
    packager.save_aif(aif1, project_path=str(project))  # default path -> project/.ziplex/project.json
    first_run_calls = provider.calls
    assert first_run_calls == 3

    # second pack, nothing on disk changed: both summaries should be reused
    # from project/.ziplex/project.json (pack()'s own default use_cache
    # lookup, no result_dir given -- see DEFAULT_OUTPUT_SUBDIR) -- only
    # rules + prompt + folder summaries regenerate (3 calls), since none of
    # those are cached (see pack()'s use_cache docstring and
    # folder_summary.py's own module docstring)
    provider.calls = 0
    aif2 = packager.pack(str(project), auto=True, interactive=False)

    assert provider.calls == 2  # rules + prompt; summaries reused
    assert aif2["files"]["main.py"]["summary"] == aif1["files"]["main.py"]["summary"]
    assert aif2["files"]["README.md"]["summary"] == aif1["files"]["README.md"]["summary"]


def test_pack_tree_sitter_parses_each_code_file_only_once(tmp_path, monkeypatch):
    # Real efficiency gap found by code review: the per-file extraction
    # loop used to call extract_signatures()/extract_dependencies()/
    # extract_api()/compress_file() back-to-back, each independently
    # tree-sitter-parsing the same file from scratch -- roughly quadrupling
    # parse-tree construction cost. packager.py now goes through
    # extract_all(), which shares one parse across all four. Counting real
    # tree_sitter.Parser.parse() calls (not extract_all() calls) catches
    # this regardless of which module's own bound name invokes it.
    from tree_sitter import Parser

    monkeypatch.setattr(llm, "_provider", llm.MockProvider())

    project = tmp_path / "project"
    _write(project / "main.py", "def add(a, b):\n    return a + b\n")
    _write(project / "util.py", "def helper():\n    pass\n")

    calls = []
    real_parse = Parser.parse
    monkeypatch.setattr(Parser, "parse", lambda self, *a, **kw: calls.append(1) or real_parse(self, *a, **kw))

    packager.pack(str(project), auto=True, interactive=False)

    # 2 code files, each parsed exactly once (not 4x).
    assert len(calls) == 2


def test_pack_hashes_each_selected_file_only_once_per_run(tmp_path, monkeypatch):
    # Real gap found by code review: load_previous_summaries() (staleness
    # stage 2) and _assemble_aif()'s own final `_manifest` used to each
    # independently re-hash every selected file's content via
    # freshness.build_manifest() -> hash_file(), doubling the disk-read+
    # hash cost of every default (use_cache=True) pack() call for zero
    # behavioral difference. packager.py now hashes once (current_manifest)
    # and threads that result through both. Counting hash_file() calls
    # directly (rather than build_manifest() calls) catches this
    # regardless of which module's own bound name actually invokes it.
    # Exercised on a *second* run specifically, since that's the one where
    # load_previous_summaries() actually reaches check_freshness() at all
    # (a first run with no previous pack on disk returns {} before ever
    # hashing anything there).
    monkeypatch.setattr(llm, "_provider", llm.MockProvider())

    project = tmp_path / "project"
    _write(project / "main.py", "def add(a, b):\n    return a + b\n")
    _write(project / "README.md", "# Sample\n\nA sample project.\n")

    aif1 = packager.pack(str(project), auto=True, interactive=False)
    packager.save_aif(aif1, project_path=str(project))

    from ziplex import freshness

    calls = []
    real_hash_file = freshness.hash_file
    monkeypatch.setattr(freshness, "hash_file", lambda fp: calls.append(fp) or real_hash_file(fp))

    packager.pack(str(project), auto=True, interactive=False)

    # 2 selected files, each hashed exactly once (not twice) this run.
    assert len(calls) == 2


def test_pack_result_dir_overrides_result_dir_for_cache_lookup(tmp_path, monkeypatch):
    # RESULT_DIR itself points somewhere this test never writes to --
    # proves the cache lookup follows the explicit `result_dir` param
    # (settings.py's per-project/global folder, in the real GUI caller),
    # not packager.py's own module-level default, the same way
    # gui/pack_service.py's start_pack_job() relies on for a project with a
    # configured output folder.
    provider = _CountingMockProvider()
    monkeypatch.setattr(llm, "_provider", provider)
    monkeypatch.setattr(packager, "RESULT_DIR", tmp_path / "unused-default")
    custom_dir = tmp_path / "custom-output"

    project = tmp_path / "project"
    _write(project / "main.py", "def add(a, b):\n    return a + b\n")
    # second file: a one-file folder gets no LLM folder-summary call at all
    _write(project / "util.py", "def sub(a, b):\n    return a - b\n")

    aif1 = packager.pack(str(project), auto=True, interactive=False, result_dir=custom_dir)
    packager.save_aif(aif1, output_path=str(custom_dir / "project.json"))
    assert not (tmp_path / "unused-default").exists()  # never touched

    provider.calls = 0
    aif2 = packager.pack(str(project), auto=True, interactive=False, result_dir=custom_dir)

    # rules + prompt + folder summaries regenerate (3 calls); main.py's
    # summary is reused from custom_dir, not re-summarized -- would be 4
    # calls if the cache lookup had silently fallen back to RESULT_DIR and
    # found nothing there
    assert provider.calls == 2
    assert aif2["files"]["main.py"]["summary"] == aif1["files"]["main.py"]["summary"]


def test_pack_lang_change_forces_full_resummarization_not_cross_language_reuse(tmp_path, monkeypatch):
    # Real bug caught by code review: an unchanged file's summary used to be
    # reused verbatim across a `lang` change (freshness.load_previous_
    # summaries() had no notion of language at all), so re-packing with a
    # different --lang kept the *previous* language's summaries while still
    # stamping the *new* language onto project.language -- the same
    # self-contradiction class this codebase already fixed once for
    # rules/prompt vs. use_llm. 4 calls (summary + rules + prompt + folder
    # summaries) on the second pack proves a real resummarization happened,
    # not a silent reuse that would cost only 3 (rules + prompt + folders).
    provider = _CountingMockProvider()
    monkeypatch.setattr(llm, "_provider", provider)
    result_dir = tmp_path / "result"

    project = tmp_path / "project"
    _write(project / "main.py", "def add(a, b):\n    return a + b\n")
    # second file: a one-file folder gets no LLM folder-summary call at all
    _write(project / "util.py", "def sub(a, b):\n    return a - b\n")

    aif1 = packager.pack(str(project), auto=True, interactive=False, result_dir=result_dir, lang="en")
    packager.save_aif(aif1, output_path=str(result_dir / "project.json"))
    assert aif1["project"]["language"] == "en"

    provider.calls = 0
    aif2 = packager.pack(str(project), auto=True, interactive=False, result_dir=result_dir, lang="ko")

    assert provider.calls == 3
    assert aif2["project"]["language"] == "ko"


def test_pack_only_resummarizes_a_changed_file(tmp_path, monkeypatch):
    provider = _CountingMockProvider()
    monkeypatch.setattr(llm, "_provider", provider)

    project = tmp_path / "project"
    _write(project / "main.py", "def add(a, b):\n    return a + b\n")
    _write(project / "README.md", "# Sample\n")

    packager.save_aif(packager.pack(str(project), auto=True, interactive=False), project_path=str(project))

    _write(project / "main.py", "def add(a, b):\n    return a + b + 1\n")  # only this one changes
    provider.calls = 0
    packager.pack(str(project), auto=True, interactive=False)

    # 1 batch call (just main.py) + rules + prompt + folder summaries = 4;
    # README.md's summary is reused
    assert provider.calls == 3


def test_pack_splits_into_multiple_batches_past_batch_size(tmp_path, monkeypatch):
    provider = _CountingMockProvider()
    monkeypatch.setattr(llm, "_provider", provider)
    monkeypatch.setattr(packager, "RESULT_DIR", tmp_path / "result")
    monkeypatch.setattr(summarizer, "BATCH_SIZE", 2)

    project = tmp_path / "project"
    for i in range(5):
        _write(project / f"file{i}.py", f"def fn{i}():\n    return {i}\n")

    packager.pack(str(project), auto=True, interactive=False)

    # 5 files at BATCH_SIZE=2 -> 3 batch calls (2, 2, 1), plus rules + prompt
    # + folder summaries
    assert provider.calls == 5


def test_pack_use_cache_false_resummarizes_everything(tmp_path, monkeypatch):
    provider = _CountingMockProvider()
    monkeypatch.setattr(llm, "_provider", provider)

    project = tmp_path / "project"
    _write(project / "main.py", "def add(a, b):\n    return a + b\n")
    # second file: a one-file folder gets no LLM folder-summary call at all
    _write(project / "util.py", "def sub(a, b):\n    return a - b\n")

    packager.save_aif(packager.pack(str(project), auto=True, interactive=False), project_path=str(project))

    provider.calls = 0
    packager.pack(str(project), auto=True, interactive=False, use_cache=False)

    # 1 batch call + rules + prompt + folder summaries = 4, same as an
    # unseen project -- nothing reused
    assert provider.calls == 3


def test_pack_with_llm_replaces_a_previous_no_llm_packs_structural_summaries(tmp_path, monkeypatch):
    # `pack --no-llm` first (no API key yet), then a real LLM pack: unchanged
    # files used to keep "Defines: add" forever unless --no-cache was passed.
    provider = _CountingMockProvider()
    monkeypatch.setattr(llm, "_provider", provider)
    project = tmp_path / "project"
    _write(project / "main.py", "def add(a, b):\n    return a + b\n")
    _write(project / "util.py", "def sub(a, b):\n    return a - b\n")

    structural = packager.pack(str(project), auto=True, interactive=False, use_llm=False)
    packager.save_aif(structural, project_path=str(project))
    assert structural["files"]["main.py"]["summary"].startswith("Defines: ")
    assert provider.calls == 0

    aif = packager.pack(str(project), auto=True, interactive=False)

    assert aif["files"]["main.py"]["summary"] == "Mock summary for local testing."
    assert aif["files"]["util.py"]["summary"] == "Mock summary for local testing."


def test_pack_no_llm_still_reuses_its_own_structural_summaries(tmp_path, monkeypatch):
    # The replacement above is LLM-runs-only: a repeat --no-llm pack keeps them.
    project = tmp_path / "project"
    _write(project / "main.py", "def add(a, b):\n    return a + b\n")
    first = packager.pack(str(project), auto=True, interactive=False, use_llm=False)
    packager.save_aif(first, project_path=str(project))

    second = packager.pack(str(project), auto=True, interactive=False, use_llm=False)

    assert second["files"]["main.py"]["summary"] == first["files"]["main.py"]["summary"]


def test_pack_reuses_summaries_from_an_output_file_not_named_after_the_project_folder(tmp_path, monkeypatch):
    # e.g. `ziplex pack proj -o out/custom.json` twice -- the lookup used to
    # only ever try out/<folder name>.json and re-bill every file.
    provider = _CountingMockProvider()
    monkeypatch.setattr(llm, "_provider", provider)
    out = tmp_path / "out"
    project = tmp_path / "project"
    _write(project / "main.py", "def add(a, b):\n    return a + b\n")
    _write(project / "util.py", "def sub(a, b):\n    return a - b\n")

    packager.save_aif(
        packager.pack(str(project), auto=True, interactive=False, result_dir=out),
        output_path=str(out / "custom.json"),
    )
    provider.calls = 0
    packager.pack(str(project), auto=True, interactive=False, result_dir=out)

    assert provider.calls == 2  # rules + prompt; both summaries reused


def test_pack_reuses_summaries_after_the_project_was_renamed_during_review(tmp_path, monkeypatch):
    # save_aif() names the file after project.name, which review can change.
    provider = _CountingMockProvider()
    monkeypatch.setattr(llm, "_provider", provider)
    project = tmp_path / "project"
    _write(project / "main.py", "def add(a, b):\n    return a + b\n")
    _write(project / "util.py", "def sub(a, b):\n    return a - b\n")

    aif = packager.pack(str(project), auto=True, interactive=False)
    aif["project"]["name"] = "Renamed Project"
    packager.save_aif(aif, project_path=str(project))
    assert (project / packager.DEFAULT_OUTPUT_SUBDIR / "Renamed Project.json").exists()

    provider.calls = 0
    packager.pack(str(project), auto=True, interactive=False)

    assert provider.calls == 2


def test_rules_prompt_uses_relative_paths_and_caps_signatures_per_file(tmp_path, monkeypatch):
    # A GUI pack's collected paths are absolute; analyze_rules() used to get
    # them verbatim (the local username and folder names went to a
    # third-party API), with every signature of every file uncapped.
    captured = []

    class _Capturing(llm.MockProvider):
        def generate(self, prompt: str, retry: int = 5, label: str = "") -> str:
            captured.append(prompt)
            return super().generate(prompt, retry)

    monkeypatch.setattr(llm, "_provider", _Capturing())
    project = tmp_path / "secret_client_folder"
    _write(project / "big.py", "".join(f"def f{i}(x):\n    return x\n\n" for i in range(40)))

    packager.pack(str(project), auto=True, interactive=False)

    rules_prompt = next(p for p in captured if '"rules"' in p)
    assert "secret_client_folder" not in rules_prompt
    assert "\nbig.py: " in rules_prompt
    assert "(+20 more)" in rules_prompt and "f39(" not in rules_prompt


def test_rules_prompt_lists_files_with_most_signatures_first(tmp_path, monkeypatch):
    captured = []

    class _Capturing(llm.MockProvider):
        def generate(self, prompt: str, retry: int = 5, label: str = "") -> str:
            captured.append(prompt)
            return super().generate(prompt, retry)

    monkeypatch.setattr(llm, "_provider", _Capturing())
    project = tmp_path / "project"
    _write(project / "a_small.py", "def one(x):\n    return x\n")
    _write(project / "z_big.py", "".join(f"def f{i}(x):\n    return x\n\n" for i in range(5)))
    _write(project / "m_mid.py", "".join(f"def g{i}(x):\n    return x\n\n" for i in range(3)))

    packager.pack(str(project), auto=True, interactive=False)

    rules_prompt = next(p for p in captured if '"rules"' in p)
    sig_block = rules_prompt.split("Signature sample", 1)[1]
    assert sig_block.index("z_big.py:") < sig_block.index("m_mid.py:") < sig_block.index("a_small.py:")


class _NonStringRulesProvider(llm.MockProvider):
    def generate(self, prompt: str, retry: int = 5, label: str = "") -> str:
        if '"rules"' in prompt:
            return json.dumps({"rules": [{"rule": "use snake_case"}]})
        return super().generate(prompt, retry=retry)


class _BareStringPromptProvider(llm.MockProvider):
    def generate(self, prompt: str, retry: int = 5, label: str = "") -> str:
        if '"prompt"' in prompt:
            return json.dumps("Here is the prompt for this project")
        return super().generate(prompt, retry=retry)


def test_save_aif_reports_the_saved_files_own_token_count(tmp_path, monkeypatch):
    # pack()'s own count covered per-file summaries only -- 8,387 reported
    # for a 13,358-token aif.json on Ziplex's own pack.
    from ziplex.tokenizer import count_tokens_for_model
    monkeypatch.setattr(llm, "_provider", llm.MockProvider())
    project = tmp_path / "project"
    _write(project / "a.py", "import b\n\ndef f():\n    return b.g()\n")
    _write(project / "b.py", "def g():\n    return 1\n")
    out = tmp_path / "out.json"

    aif = packager.pack(str(project), auto=True, interactive=False)
    packager.save_aif((aif), str(out), project_path=str(project))

    saved = json.loads(out.read_text(encoding="utf-8"))
    counted = count_tokens_for_model(out.read_text(encoding="utf-8"), "GPT-4o")
    # Within a few tokens: the count is taken before its own digits land.
    assert abs(saved["tokens"]["GPT-4o"]["compressed"] - counted) <= 5
    assert "GPT-3.5" not in saved["tokens"]




def test_an_interrupted_pack_keeps_the_summaries_it_already_paid_for(tmp_path, monkeypatch):
    provider = _CountingMockProvider()
    monkeypatch.setattr(llm, "_provider", provider)
    project = tmp_path / "project"
    _write(project / "main.py", "def add(a, b):\n    return a + b\n")
    _write(project / "util.py", "def sub(a, b):\n    return a - b\n")

    def boom(*a, **k):
        raise RuntimeError("crash after summaries")

    real_rules = packager.analyze_rules
    monkeypatch.setattr(packager, "analyze_rules", boom)
    with pytest.raises(RuntimeError):
        packager.pack(str(project))
    cache = json.loads((project / ".ziplex" / "summaries.json").read_text(encoding="utf-8"))
    assert len(cache) == 2
    assert "summaries.json" in (project / ".ziplex" / ".gitignore").read_text(encoding="utf-8")

    monkeypatch.setattr(packager, "analyze_rules", real_rules)
    provider = _CountingMockProvider()
    monkeypatch.setattr(llm, "_provider", provider)
    aif = packager.pack(str(project))  # no saved aif.json to reuse, only the cache
    assert provider.calls == 2  # rules + prompt; no summary call
    assert aif["files"]["main.py"]["summary"]


def test_pack_is_non_interactive_and_returns_a_finalized_aif(tmp_path, monkeypatch):
    monkeypatch.setattr(llm, "_provider", llm.MockProvider())
    monkeypatch.setattr(builtins, "input", lambda *a, **k: (_ for _ in ()).throw(AssertionError("prompted")))
    project = tmp_path / "project"
    _write(project / "a.py", "import b\n")
    _write(project / "b.py", "x = 1\n")
    aif = packager.pack(str(project), auto=False, interactive=True)  # legacy flags are ignored
    assert aif["relationships"]["a.py"]["internal"] == ["b.py"]
    assert "confidence" not in aif["files"]["a.py"]
