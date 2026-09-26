from ziplex.extract.code.extractor import extract_dependencies
from ziplex.py_imports import resolve_python_module, rewrite_python_imports


FASTAPI_LIKE = {
    "backend/app/__init__.py", "backend/app/api/__init__.py", "backend/app/api/main.py",
    "backend/app/api/routes/__init__.py", "backend/app/api/routes/items.py",
    "backend/app/api/routes/login.py", "backend/app/core/__init__.py", "backend/app/core/config.py",
    "backend/app/core/security.py", "backend/tests/utils/user.py", "frontend/tests/utils/user.ts",
}


def test_from_import_keeps_submodule_files_and_drops_symbols():
    # fastapi/full-stack-fastapi-template: `from app.api.routes import items, login`
    # imports two files; `from app.core.config import settings` imports a symbol.
    deps = [
        "fastapi", "fastapi.APIRouter",
        "app.api.routes", "app.api.routes.items", "app.api.routes.login",
        "app.core.config", "app.core.config.settings",
    ]
    assert rewrite_python_imports("backend/app/api/main.py", deps, FASTAPI_LIKE) == [
        "fastapi", "backend/app/api/routes/items.py", "backend/app/api/routes/login.py",
        "backend/app/core/config.py",
    ]


def test_absolute_import_resolves_against_an_ancestor_root_and_never_crosses_languages():
    got = resolve_python_module("tests.utils.user", "backend/tests/api/test_x.py", FASTAPI_LIKE)
    assert got == "backend/tests/utils/user.py"


def test_package_import_without_submodule_names_keeps_the_init_edge():
    deps = ["app.core", "app.core.security"]
    assert rewrite_python_imports("backend/app/api/deps.py", deps, FASTAPI_LIKE) == ["backend/app/core/security.py"]
    assert rewrite_python_imports("backend/app/api/deps.py", ["app.core"], FASTAPI_LIKE) == ["backend/app/core/__init__.py"]


def test_src_layout_falls_back_to_a_unique_module_path_suffix():
    # tests/ importing `from ziplex import tech_stack` -- src/ is no ancestor of tests/.
    names = {"src/ziplex/__init__.py", "src/ziplex/tech_stack.py", "tests/test_tech_stack.py"}
    deps = ["ziplex", "ziplex.tech_stack", "ziplex.tech_stack.detect_tech_stack"]
    assert rewrite_python_imports("tests/test_tech_stack.py", deps, names) == ["src/ziplex/tech_stack.py"]


def test_an_ambiguous_suffix_is_not_guessed():
    names = {"a/utils.py", "b/utils.py", "main.py"}
    assert resolve_python_module("utils", "main.py", names) is None


def test_relative_imports_and_non_python_sources_pass_through_correctly():
    names = {"pkg/__init__.py", "pkg/paths.py", "pkg/cli.py"}
    assert rewrite_python_imports("pkg/cli.py", [".paths", ".paths.REPO_ROOT"], names) == ["pkg/paths.py"]
    assert rewrite_python_imports("web/app.ts", ["app.core"], FASTAPI_LIKE) == ["app.core"]


def test_extractor_emits_from_import_names_as_candidates(tmp_path):
    path = tmp_path / "m.py"
    path.write_text(
        "from app.api.routes import items, login as lg\nfrom os import path\nfrom x import *\n",
        encoding="utf-8",
    )
    assert extract_dependencies(str(path)) == [
        "app.api.routes", "app.api.routes.items", "app.api.routes.login", "x",
    ]
