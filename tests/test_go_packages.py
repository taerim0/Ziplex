from ziplex.go_packages import (
    read_go_module_path, build_go_package_index, expand_go_dependencies,
    resolve_go_context, expand_dependencies_for_file,
)


def _write(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_read_go_module_path_from_a_real_go_mod(tmp_path):
    _write(tmp_path / "go.mod", "module github.com/example/myproject\n\ngo 1.21\n")
    assert read_go_module_path(str(tmp_path)) == "github.com/example/myproject"


def test_read_go_module_path_strips_a_trailing_line_comment(tmp_path):
    _write(tmp_path / "go.mod", "module github.com/example/myproject // pinned\n")
    assert read_go_module_path(str(tmp_path)) == "github.com/example/myproject"


def test_read_go_module_path_returns_none_when_go_mod_is_missing(tmp_path):
    assert read_go_module_path(str(tmp_path)) is None


def test_read_go_module_path_returns_none_when_no_module_line(tmp_path):
    _write(tmp_path / "go.mod", "go 1.21\n")
    assert read_go_module_path(str(tmp_path)) is None


def test_build_go_package_index_groups_by_directory():
    index = build_go_package_index([
        "main.go",
        "internal/utils/format.go",
        "internal/utils/parse.go",
        "internal/other/thing.go",
        "README.md",  # not a .go file -- must be ignored
    ])
    assert index == {
        ".": ["main.go"],
        "internal/utils": ["internal/utils/format.go", "internal/utils/parse.go"],
        "internal/other": ["internal/other/thing.go"],
    }


def test_expand_go_dependencies_resolves_an_internal_multi_file_package():
    index = {"internal/utils": ["internal/utils/format.go", "internal/utils/parse.go"]}
    deps = ["fmt", "github.com/example/myproject/internal/utils"]
    expanded = expand_go_dependencies(deps, "main.go", "github.com/example/myproject", index)

    assert "fmt" in expanded  # stdlib import passes through untouched
    assert set(expanded) == {"fmt", "internal/utils/format.go", "internal/utils/parse.go"}


def test_expand_go_dependencies_resolves_the_module_root_package():
    index = {".": ["main.go", "helpers.go"]}
    expanded = expand_go_dependencies(
        ["github.com/example/myproject"], "helpers.go", "github.com/example/myproject", index
    )
    assert expanded == ["main.go"]  # self excluded


def test_expand_go_dependencies_leaves_a_genuinely_external_import_untouched():
    expanded = expand_go_dependencies(
        ["github.com/someone-else/lib"], "main.go", "github.com/example/myproject", {}
    )
    assert expanded == ["github.com/someone-else/lib"]


def test_expand_go_dependencies_keeps_the_raw_string_when_the_package_has_no_collected_files():
    # rooted under module_path, but nothing was actually collected there
    # (not selected, or a genuine typo) -- must not vanish silently.
    expanded = expand_go_dependencies(
        ["github.com/example/myproject/internal/missing"], "main.go", "github.com/example/myproject", {}
    )
    assert expanded == ["github.com/example/myproject/internal/missing"]


def test_expand_go_dependencies_excludes_the_importing_file_itself():
    index = {"internal/utils": ["internal/utils/format.go"]}
    expanded = expand_go_dependencies(
        ["github.com/example/myproject/internal/utils"],
        "internal/utils/format.go",  # importing its own package, hypothetically
        "github.com/example/myproject",
        index,
    )
    assert expanded == ["github.com/example/myproject/internal/utils"]  # nothing left -> raw string kept


# resolve_go_context()/expand_dependencies_for_file() -- the shared wrappers
# packager.py's pack() and cli.py's `tree` subcommand both call instead of
# each re-inlining the setup+gate around the three functions above.

def test_resolve_go_context_bundles_module_path_and_package_index(tmp_path):
    _write(tmp_path / "go.mod", "module github.com/example/myproject\n")
    all_names = ["main.go", "internal/utils/format.go", "internal/utils/parse.go"]
    module_path, index = resolve_go_context(str(tmp_path), all_names)

    assert module_path == "github.com/example/myproject"
    assert index == {
        ".": ["main.go"],
        "internal/utils": ["internal/utils/format.go", "internal/utils/parse.go"],
    }


def test_resolve_go_context_returns_an_empty_index_for_a_non_go_project(tmp_path):
    # No go.mod at all -- read_go_module_path() -> None, so the package
    # index must never even attempt build_go_package_index() over a name
    # list that may not have a single .go file in it.
    module_path, index = resolve_go_context(str(tmp_path), ["main.py", "README.md"])
    assert module_path is None
    assert index == {}


def test_expand_dependencies_for_file_expands_a_go_file_under_a_resolved_module():
    index = {"internal/utils": ["internal/utils/format.go", "internal/utils/parse.go"]}
    deps = expand_dependencies_for_file(
        "internal/utils/format.go", "internal/utils/format.go",
        ["fmt", "github.com/example/myproject/internal/utils"],
        "github.com/example/myproject", index,
    )
    assert set(deps) == {"fmt", "internal/utils/parse.go"}  # self excluded


def test_expand_dependencies_for_file_leaves_a_non_go_file_untouched():
    # A .py file's raw deps must pass straight through even when a resolved
    # go_module_path exists (a mixed-language project) -- the extension
    # gate is per-file, not per-project.
    deps = expand_dependencies_for_file(
        "scripts/build.py", "scripts/build.py", ["os", "sys"],
        "github.com/example/myproject", {},
    )
    assert deps == ["os", "sys"]


def test_expand_dependencies_for_file_leaves_deps_untouched_with_no_go_module():
    # go_module_path is None (no go.mod) -- must not raise even for a .go
    # file, just pass the raw deps through unexpanded.
    deps = expand_dependencies_for_file(
        "main.go", "main.go", ["fmt", "github.com/someone-else/lib"], None, {},
    )
    assert deps == ["fmt", "github.com/someone-else/lib"]
