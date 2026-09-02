from ziplex.go_packages import read_go_module_path, build_go_package_index, expand_go_dependencies


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
