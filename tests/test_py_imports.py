from ziplex.extract.code.extractor import extract_dependencies
from ziplex.py_imports import resolve_python_module, rewrite_python_imports


FASTAPI_LIKE = {
    "backend/app/__init__.py", "backend/app/api/__init__.py", "backend/app/api/main.py",
    "backend/app/api/routes/__init__.py", "backend/app/api/routes/items.py",
    "backend/app/api/routes/login.py", "backend/app/core/__init__.py", "backend/app/core/config.py",
    "backend/app/core/security.py", "backend/tests/utils/user.py", "frontend/tests/utils/user.ts",
}

# modelcontextprotocol/python-sdk's shape: mcp/server/__init__.py re-exports
# `Server` from its own submodule mcp/server/lowlevel/.
MCP_LIKE = {
    "mcp/__init__.py", "mcp/server/__init__.py", "mcp/server/session.py",
    "mcp/server/lowlevel/__init__.py", "mcp/server/lowlevel/server.py",
    "mcp/client/client.py",
}


def _rewrite(source: str, code: str, names: set, tmp_path) -> list[str]:
    path = tmp_path / "m.py"
    path.write_text(code, encoding="utf-8")
    return rewrite_python_imports(source, extract_dependencies(str(path)), names)


def test_from_import_keeps_submodule_files_and_symbols_land_on_their_module():
    # fastapi/full-stack-fastapi-template: `from app.api.routes import items, login`
    # imports two files; `from app.core.config import settings` imports a symbol.
    deps = [
        "fastapi::APIRouter",
        "app.api.routes::items", "app.api.routes::login",
        "app.core.config::settings",
    ]
    assert rewrite_python_imports("backend/app/api/main.py", deps, FASTAPI_LIKE) == [
        "fastapi", "backend/app/api/routes/items.py", "backend/app/api/routes/login.py",
        "backend/app/core/config.py",
    ]


def test_absolute_import_resolves_against_an_ancestor_root_and_never_crosses_languages():
    got = resolve_python_module("tests.utils.user", "backend/tests/api/test_x.py", FASTAPI_LIKE)
    assert got == "backend/tests/utils/user.py"


def test_submodule_only_import_has_no_package_init_edge():
    assert rewrite_python_imports(
        "backend/app/api/deps.py", ["app.core::security"], FASTAPI_LIKE
    ) == ["backend/app/core/security.py"]
    assert rewrite_python_imports("backend/app/api/deps.py", ["app.core"], FASTAPI_LIKE) == [
        "backend/app/core/__init__.py"
    ]


def test_symbol_only_import_is_an_edge_to_the_package_init(tmp_path):
    got = _rewrite("mcp/client/client.py", "from mcp.server import Server\n", MCP_LIKE, tmp_path)
    assert got == ["mcp/server/__init__.py"]


def test_sibling_submodule_import_never_suppresses_a_symbol_import(tmp_path):
    # The python-sdk miss: a second statement importing a submodule of the
    # same package used to drop the re-exported symbol's __init__ edge.
    code = (
        "from mcp.server import Server\n"
        "from mcp.server.lowlevel import NotificationOptions\n"
        "from mcp.server import session\n"
    )
    got = _rewrite("mcp/client/client.py", code, MCP_LIKE, tmp_path)
    assert sorted(got) == [
        "mcp/server/__init__.py", "mcp/server/lowlevel/__init__.py", "mcp/server/session.py",
    ]


def test_mixed_names_in_one_statement_give_the_submodule_and_the_package(tmp_path):
    got = _rewrite("mcp/client/client.py", "from mcp.server import Server, session\n", MCP_LIKE, tmp_path)
    assert got == ["mcp/server/__init__.py", "mcp/server/session.py"]
    got = _rewrite("mcp/client/client.py", "from mcp.server import session\n", MCP_LIKE, tmp_path)
    assert got == ["mcp/server/session.py"]


def test_plain_package_import_and_submodule_import_both_keep_their_edges(tmp_path):
    code = "import mcp.server\nfrom mcp.server import session\nimport mcp.server.lowlevel.server\n"
    got = _rewrite("mcp/client/client.py", code, MCP_LIKE, tmp_path)
    assert got == ["mcp/server/__init__.py", "mcp/server/session.py", "mcp/server/lowlevel/server.py"]


def test_relative_symbol_and_submodule_imports(tmp_path):
    names = MCP_LIKE | {"mcp/server/lowlevel/helper.py"}
    src = "mcp/server/lowlevel/server.py"
    # `from .. import Server` (symbol) + `from .. import session` (submodule).
    got = _rewrite(src, "from .. import Server\nfrom .. import session\n", names, tmp_path)
    assert got == ["mcp/server/__init__.py", "mcp/server/session.py"]
    # `from . import helper` -> sibling file; `from .helper import X` -> that file.
    got = _rewrite(src, "from . import helper\nfrom .helper import X\n", names, tmp_path)
    assert got == ["mcp/server/lowlevel/helper.py"]
    # `from .lowlevel import Thing` (symbol of a subpackage) beside a submodule import.
    got = _rewrite(
        "mcp/server/session.py", "from .lowlevel import Thing\nfrom .lowlevel import server\n", names, tmp_path
    )
    assert got == ["mcp/server/lowlevel/__init__.py", "mcp/server/lowlevel/server.py"]


def test_src_layout_falls_back_to_a_unique_module_path_suffix():
    # tests/ importing `from ziplex import tech_stack` -- src/ is no ancestor of tests/.
    names = {"src/ziplex/__init__.py", "src/ziplex/tech_stack.py", "tests/test_tech_stack.py"}
    assert rewrite_python_imports("tests/test_tech_stack.py", ["ziplex::tech_stack"], names) == [
        "src/ziplex/tech_stack.py"
    ]
    assert rewrite_python_imports(
        "tests/test_tech_stack.py", ["ziplex.tech_stack::detect_tech_stack"], names
    ) == ["src/ziplex/tech_stack.py"]


def test_an_ambiguous_suffix_is_not_guessed():
    names = {"a/utils.py", "b/utils.py", "main.py"}
    assert resolve_python_module("utils", "main.py", names) is None


def test_unresolved_imports_pass_through_and_non_python_sources_are_untouched():
    names = {"pkg/__init__.py", "pkg/paths.py", "pkg/cli.py"}
    assert rewrite_python_imports("pkg/cli.py", [".paths::REPO_ROOT"], names) == ["pkg/paths.py"]
    assert rewrite_python_imports("pkg/cli.py", ["pytest", "fastapi::A", "fastapi::B"], names) == [
        "pytest", "fastapi",
    ]
    assert rewrite_python_imports("web/app.ts", ["app.core"], FASTAPI_LIKE) == ["app.core"]


def test_extractor_emits_one_entry_per_from_import_name(tmp_path):
    path = tmp_path / "m.py"
    path.write_text(
        "from app.api.routes import items, login as lg\nfrom os import path\nfrom x import *\n"
        "from . import a as b\nimport y.z as yz\n",
        encoding="utf-8",
    )
    assert extract_dependencies(str(path)) == [
        "app.api.routes::items", "app.api.routes::login", "x", ".::a", "y.z",
    ]


def test_relative_symbol_import_is_an_edge_to_the_package_not_an_external():
    # `from . import __version__` must not surface as an external ".__version__".
    names = {"src/pkg/__init__.py", "src/pkg/cli.py", "src/pkg/llm.py"}
    deps = [".::__version__", ".::llm", ".missing::X"]
    assert rewrite_python_imports("src/pkg/cli.py", deps, names) == [
        "src/pkg/__init__.py", "src/pkg/llm.py", ".missing",
    ]
    # A bare relative package that resolves to nothing is dropped, and an
    # `__init__.py` never gains an edge to itself.
    assert rewrite_python_imports("x.py", [".::y"], {"x.py"}) == []
    assert rewrite_python_imports("src/pkg/__init__.py", [".::__version__"], names) == []
