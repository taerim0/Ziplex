import json

from ziplex import tech_stack
from ziplex.tech_stack import detect_tech_stack, MAX_DEPENDENCIES


def _write(path, content):
    path.write_text(content, encoding="utf-8")


def test_returns_empty_list_when_no_manifest_present(tmp_path):
    assert detect_tech_stack(str(tmp_path)) == []


def test_detects_package_json(tmp_path):
    _write(tmp_path / "package.json", json.dumps({
        "dependencies": {"react": "^18.0.0"},
        "devDependencies": {"eslint": "^9.0.0"},
    }))
    stacks = detect_tech_stack(str(tmp_path))
    assert len(stacks) == 1
    assert stacks[0]["manifest"] == "package.json"
    assert stacks[0]["language"] == "JavaScript/TypeScript"
    assert stacks[0]["package_manager"] == "npm"
    assert set(stacks[0]["dependencies"]) == {"react", "eslint"}
    assert stacks[0]["dependencies_truncated"] is False


def test_detects_requirements_txt_and_strips_version_specifiers(tmp_path):
    _write(tmp_path / "requirements.txt", "\n".join([
        "flask[async]>=2.0",
        "requests==2.31.0",
        "# a comment",
        "",
        "-r other.txt",
        "numpy",
    ]))
    stacks = detect_tech_stack(str(tmp_path))
    assert len(stacks) == 1
    assert stacks[0]["dependencies"] == ["flask", "requests", "numpy"]


def test_detects_pyproject_toml_pep621_dependencies(tmp_path):
    _write(tmp_path / "pyproject.toml", '\n'.join([
        "[project]",
        'name = "x"',
        'dependencies = ["flask>=2.0", "requests"]',
    ]))
    stacks = detect_tech_stack(str(tmp_path))
    assert len(stacks) == 1
    assert set(stacks[0]["dependencies"]) == {"flask", "requests"}


def test_detects_pyproject_toml_poetry_dependencies_and_excludes_python_itself(tmp_path):
    _write(tmp_path / "pyproject.toml", '\n'.join([
        "[tool.poetry.dependencies]",
        'python = "^3.11"',
        'flask = "^2.0"',
    ]))
    stacks = detect_tech_stack(str(tmp_path))
    assert stacks[0]["dependencies"] == ["flask"]


def test_detects_cargo_toml(tmp_path):
    _write(tmp_path / "Cargo.toml", '\n'.join([
        "[dependencies]",
        'serde = "1.0"',
    ]))
    stacks = detect_tech_stack(str(tmp_path))
    assert stacks[0]["manifest"] == "Cargo.toml"
    assert stacks[0]["dependencies"] == ["serde"]


def test_detects_go_mod_require_block(tmp_path):
    _write(tmp_path / "go.mod", '\n'.join([
        "module example.com/x",
        "",
        "require (",
        "\tgithub.com/gin-gonic/gin v1.9.0",
        "\tgithub.com/stretchr/testify v1.8.0 // indirect",
        ")",
    ]))
    stacks = detect_tech_stack(str(tmp_path))
    assert stacks[0]["dependencies"] == ["github.com/gin-gonic/gin", "github.com/stretchr/testify"]


def test_detects_go_mod_single_line_require(tmp_path):
    _write(tmp_path / "go.mod", "module example.com/x\n\nrequire github.com/gin-gonic/gin v1.9.0\n")
    stacks = detect_tech_stack(str(tmp_path))
    assert stacks[0]["dependencies"] == ["github.com/gin-gonic/gin"]


def test_detects_gemfile(tmp_path):
    _write(tmp_path / "Gemfile", '\n'.join([
        'source "https://rubygems.org"',
        'gem "rails"',
        "gem 'pg'",
    ]))
    stacks = detect_tech_stack(str(tmp_path))
    assert set(stacks[0]["dependencies"]) == {"rails", "pg"}


def test_detects_composer_json_and_excludes_php_platform_entry(tmp_path):
    _write(tmp_path / "composer.json", json.dumps({
        "require": {"php": ">=8.0", "laravel/framework": "^10.0"},
    }))
    stacks = detect_tech_stack(str(tmp_path))
    assert stacks[0]["dependencies"] == ["laravel/framework"]


def test_detects_pom_xml_with_default_namespace(tmp_path):
    _write(tmp_path / "pom.xml", '\n'.join([
        '<project xmlns="http://maven.apache.org/POM/4.0.0">',
        "  <dependencies>",
        "    <dependency>",
        "      <groupId>org.springframework</groupId>",
        "      <artifactId>spring-core</artifactId>",
        "    </dependency>",
        "  </dependencies>",
        "</project>",
    ]))
    stacks = detect_tech_stack(str(tmp_path))
    assert stacks[0]["manifest"] == "pom.xml"
    assert stacks[0]["dependencies"] == ["spring-core"]


def test_dedupes_dependencies_appearing_in_multiple_sections(tmp_path):
    _write(tmp_path / "package.json", json.dumps({
        "dependencies": {"lodash": "^4.0.0"},
        "devDependencies": {"lodash": "^4.0.0"},
    }))
    stacks = detect_tech_stack(str(tmp_path))
    assert stacks[0]["dependencies"] == ["lodash"]


def test_truncates_past_max_dependencies_and_flags_it(tmp_path):
    deps = {f"pkg{i}": "1.0.0" for i in range(MAX_DEPENDENCIES + 5)}
    _write(tmp_path / "package.json", json.dumps({"dependencies": deps}))
    stacks = detect_tech_stack(str(tmp_path))
    assert len(stacks[0]["dependencies"]) == MAX_DEPENDENCIES
    assert stacks[0]["dependencies_truncated"] is True


def test_detects_multiple_manifests_in_stable_order(tmp_path):
    _write(tmp_path / "package.json", json.dumps({"dependencies": {}}))
    _write(tmp_path / "requirements.txt", "flask\n")
    stacks = detect_tech_stack(str(tmp_path))
    assert [s["manifest"] for s in stacks] == ["package.json", "requirements.txt"]


def test_skips_a_malformed_manifest_without_raising(tmp_path):
    _write(tmp_path / "package.json", "{ not valid json")
    stacks = detect_tech_stack(str(tmp_path))
    assert stacks == [{
        "manifest": "package.json",
        "language": "JavaScript/TypeScript",
        "package_manager": "npm",
        "dependencies": [],
        "dependencies_truncated": False,
    }]


def test_survives_a_manifest_that_isnt_valid_utf8(tmp_path):
    # A legacy Windows-authored manifest saved in a non-UTF-8 encoding used
    # to raise UnicodeDecodeError uncaught -- _read_text() only caught
    # OSError, breaking this module's own "never raises, by contract"
    # guarantee. Must degrade to an empty dependency list like any other
    # unreadable/malformed manifest, not crash pack()'s tech-stack step.
    (tmp_path / "requirements.txt").write_bytes("café==1.0\n".encode("latin-1"))
    stacks = detect_tech_stack(str(tmp_path))
    assert stacks == [{
        "manifest": "requirements.txt",
        "language": "Python",
        "package_manager": "pip",
        "dependencies": [],
        "dependencies_truncated": False,
    }]


def test_ignores_a_manifest_found_in_a_subdirectory(tmp_path):
    nested = tmp_path / "nested"
    nested.mkdir()
    _write(nested / "package.json", json.dumps({"dependencies": {"react": "1.0"}}))
    assert detect_tech_stack(str(tmp_path)) == []


# --- Regression tests for real bugs a code review found by executing the
# actual parsers against non-standard-but-legal manifest shapes -- each one
# reproduced a crash or a silent-corruption case before being fixed. ---

def test_pyproject_toml_survives_a_non_table_project_key(tmp_path):
    # `project = "foo"` is valid TOML; _dig() must not chain .get() onto a
    # string and raise AttributeError.
    _write(tmp_path / "pyproject.toml", 'project = "foo"\n')
    stacks = detect_tech_stack(str(tmp_path))
    assert stacks[0]["dependencies"] == []


def test_pyproject_toml_survives_a_non_table_tool_key(tmp_path):
    _write(tmp_path / "pyproject.toml", 'tool = "foo"\n')
    stacks = detect_tech_stack(str(tmp_path))
    assert stacks[0]["dependencies"] == []


def test_pyproject_toml_survives_a_non_list_dependencies_field(tmp_path):
    # A manifest typo -- `dependencies = "flask"` instead of a list -- must
    # not silently explode the string into per-character garbage names.
    _write(tmp_path / "pyproject.toml", '[project]\ndependencies = "flask"\n')
    stacks = detect_tech_stack(str(tmp_path))
    assert stacks[0]["dependencies"] == []


def test_pyproject_toml_skips_a_non_string_dependency_entry(tmp_path):
    _write(tmp_path / "pyproject.toml", '[project]\ndependencies = ["flask", 123]\n')
    stacks = detect_tech_stack(str(tmp_path))
    assert stacks[0]["dependencies"] == ["flask"]


def test_package_json_survives_a_non_dict_dependencies_value(tmp_path):
    _write(tmp_path / "package.json", json.dumps({"dependencies": True}))
    stacks = detect_tech_stack(str(tmp_path))
    assert stacks[0]["dependencies"] == []


def test_cargo_toml_survives_a_non_table_dependencies_value(tmp_path):
    _write(tmp_path / "Cargo.toml", "dependencies = true\n")
    stacks = detect_tech_stack(str(tmp_path))
    assert stacks[0]["dependencies"] == []


def test_composer_json_survives_a_non_dict_require_value(tmp_path):
    _write(tmp_path / "composer.json", json.dumps({"require": True}))
    stacks = detect_tech_stack(str(tmp_path))
    assert stacks[0]["dependencies"] == []


def test_go_mod_empty_inline_require_block_does_not_swallow_later_lines(tmp_path):
    # "require ()" puts both "(" and ")" on the opening line -- must not
    # leave the parser stuck "inside" the block for the rest of the file.
    _write(tmp_path / "go.mod", "\n".join([
        "module example.com/x",
        "",
        "require ()",
        "",
        "replace example.com/foo => ./local",
    ]))
    stacks = detect_tech_stack(str(tmp_path))
    assert stacks[0]["dependencies"] == []


def test_pom_xml_excludes_dependency_management_entries(tmp_path):
    # A <dependencyManagement> BOM import is not a real project dependency
    # -- only the top-level <dependencies> block is.
    _write(tmp_path / "pom.xml", "\n".join([
        '<project xmlns="http://maven.apache.org/POM/4.0.0">',
        "  <dependencyManagement>",
        "    <dependencies>",
        "      <dependency>",
        "        <groupId>org.springframework.boot</groupId>",
        "        <artifactId>spring-boot-dependencies</artifactId>",
        "      </dependency>",
        "    </dependencies>",
        "  </dependencyManagement>",
        "  <dependencies>",
        "    <dependency>",
        "      <groupId>org.springframework</groupId>",
        "      <artifactId>spring-core</artifactId>",
        "    </dependency>",
        "  </dependencies>",
        "</project>",
    ]))
    stacks = detect_tech_stack(str(tmp_path))
    assert stacks[0]["dependencies"] == ["spring-core"]


def test_requirements_txt_extracts_name_from_a_vcs_url_egg_fragment(tmp_path):
    _write(tmp_path / "requirements.txt", "git+https://github.com/foo/bar.git#egg=bar\n")
    stacks = detect_tech_stack(str(tmp_path))
    assert stacks[0]["dependencies"] == ["bar"]


def test_requirements_txt_extracts_name_from_an_editable_vcs_url(tmp_path):
    _write(tmp_path / "requirements.txt", "-e git+https://github.com/foo/bar.git#egg=bar\n")
    stacks = detect_tech_stack(str(tmp_path))
    assert stacks[0]["dependencies"] == ["bar"]


def test_requirements_txt_skips_a_vcs_url_with_no_egg_fragment(tmp_path):
    # No #egg= means no reliable package name -- must not fall through to
    # the version-specifier split and emit the mangled URL as a "name".
    _write(tmp_path / "requirements.txt", "git+https://github.com/foo/bar.git\n")
    stacks = detect_tech_stack(str(tmp_path))
    assert stacks[0]["dependencies"] == []


def test_detect_tech_stack_never_raises_on_an_unanticipated_parser_error(tmp_path, monkeypatch):
    def _boom(path):
        raise RuntimeError("simulated parser bug")

    monkeypatch.setattr(tech_stack, "_MANIFESTS", [
        ("package.json", "JavaScript/TypeScript", "npm", _boom),
    ])
    _write(tmp_path / "package.json", "{}")
    stacks = detect_tech_stack(str(tmp_path))
    assert stacks[0]["dependencies"] == []


def test_pyproject_toml_still_gets_an_entry_when_tomllib_is_unavailable(tmp_path, monkeypatch):
    # Simulates Python < 3.11 (no stdlib tomllib) -- the manifest's presence
    # is still detected (ecosystem is known from the filename alone), just
    # with an empty dependency list, same as any other unreadable manifest.
    monkeypatch.setattr(tech_stack, "tomllib", None)
    _write(tmp_path / "pyproject.toml", '[project]\ndependencies = ["flask"]\n')
    stacks = detect_tech_stack(str(tmp_path))
    assert stacks[0]["manifest"] == "pyproject.toml"
    assert stacks[0]["dependencies"] == []
