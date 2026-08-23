from extract.code.extractor import extract_signatures, extract_dependencies, extract_api


def test_extract_signatures_from_python_file(tmp_path):
    file_path = tmp_path / "mod.py"
    file_path.write_text(
        "def add(a, b):\n    return a + b\n\n\ndef sub(a, b):\n    return a - b\n",
        encoding="utf-8",
    )

    sigs = extract_signatures(str(file_path))
    assert "add(a, b)" in sigs
    assert "sub(a, b)" in sigs


def test_extract_dependencies_from_python_file(tmp_path):
    file_path = tmp_path / "mod.py"
    file_path.write_text("import os\nfrom pathlib import Path\n\nx = 1\n", encoding="utf-8")

    deps = extract_dependencies(str(file_path))
    assert "os" in deps
    assert "pathlib" in deps


def test_extract_signatures_from_lua_file(tmp_path):
    file_path = tmp_path / "mod.lua"
    file_path.write_text(
        "local function greet(name)\n    return name\nend\n\n"
        "function M.doThing(x, y)\n    return x + y\nend\n\n"
        "function M:method(a)\n    return a * 2\nend\n",
        encoding="utf-8",
    )

    sigs = extract_signatures(str(file_path))
    assert "greet(name)" in sigs
    # dotted (table field) and colon (method) forms both fold into the same
    # function_declaration node type -- the field text itself already
    # includes the qualifier, so no extra per-form handling was needed.
    assert "M.doThing(x, y)" in sigs
    assert "M:method(a)" in sigs


def test_extract_dependencies_from_lua_file(tmp_path):
    file_path = tmp_path / "mod.lua"
    file_path.write_text(
        'local utils = require("mymod.utils")\n'
        'local other = require "mymod.other"\n'  # parenthesis-less call form
        "return utils\n",
        encoding="utf-8",
    )

    deps = extract_dependencies(str(file_path))
    assert "mymod.utils" in deps
    assert "mymod.other" in deps


def test_extract_dependencies_ignores_non_require_calls(tmp_path):
    # require() has to be recognized by name, unlike an import keyword -- a
    # same-shaped call to anything else must not be mistaken for one.
    file_path = tmp_path / "mod.lua"
    file_path.write_text('print("mymod.utils")\n', encoding="utf-8")

    assert extract_dependencies(str(file_path)) == []


def test_extract_signatures_from_gdscript_file(tmp_path):
    file_path = tmp_path / "player.gd"
    file_path.write_text(
        "func _ready() -> void:\n    pass\n\n"
        "func take_damage(amount: int) -> void:\n    pass\n\n"
        "class Inner:\n    func inner_method():\n        pass\n",
        encoding="utf-8",
    )

    sigs = extract_signatures(str(file_path))
    assert "_ready() -> void" in sigs
    assert "take_damage(amount: int) -> void" in sigs
    # nested inside a `class Inner:` block -- still found, since the
    # traversal recurses regardless of nesting depth.
    assert "inner_method()" in sigs


def test_extract_dependencies_from_gdscript_file(tmp_path):
    file_path = tmp_path / "player.gd"
    file_path.write_text(
        'extends "res://base_entity.gd"\n'
        'const Config = preload("res://config.gd")\n'
        'var Utils = load("res://utils.gd")\n',
        encoding="utf-8",
    )

    deps = extract_dependencies(str(file_path))
    # normalized to bare stems (not the full res:// path), matching what
    # file/relationship.py's resolve_dependency() actually matches against.
    assert "base_entity" in deps
    assert "config" in deps
    assert "utils" in deps


def test_extract_dependencies_ignores_class_name_extends(tmp_path):
    # `extends Node` (a built-in engine class, not a project file) has no
    # resolvable path -- unlike `extends "res://Base.gd"`, it must not be
    # reported as a dependency at all.
    file_path = tmp_path / "player.gd"
    file_path.write_text("extends Node\n", encoding="utf-8")

    assert extract_dependencies(str(file_path)) == []


def test_extract_dependencies_from_gdscript_recognizes_qualified_load(tmp_path):
    # ResourceLoader.load(...) parses as attribute > attribute_call, not a
    # top-level `call` the way bare load(...)/preload(...) do -- both forms
    # must resolve to the same dependency.
    file_path = tmp_path / "player.gd"
    file_path.write_text('var scene = ResourceLoader.load("res://x.tscn")\n', encoding="utf-8")

    assert "x" in extract_dependencies(str(file_path))


def test_extract_signatures_from_typescript_arrow_functions(tmp_path):
    # arrow_function/function_expression have no "name" field of their own
    # when anonymous -- extract_signatures() has to recover a readable name
    # from the wrapping variable_declarator/public_field_definition/pair
    # node's own "name"/"key" field instead.
    file_path = tmp_path / "mod.ts"
    file_path.write_text(
        "const add = (a, b) => {\n  return a + b;\n};\n\n"
        "const obj = {\n  greet: () => {\n    return 1;\n  },\n};\n\n"
        "class Foo {\n  method = () => {\n    return 1;\n  };\n}\n\n"
        "const namedExpr = function bar(a) {\n  return a;\n};\n",
        encoding="utf-8",
    )

    sigs = extract_signatures(str(file_path))
    assert "add(a, b)" in sigs           # variable_declarator's "name" field
    assert "greet()" in sigs             # pair's "key" field
    assert "method()" in sigs            # public_field_definition's "name" field
    assert "bar(a)" in sigs              # function_expression's own "name" field


def test_extract_signatures_skips_bare_single_param_arrow(tmp_path):
    # `x => x * 2` has no "parameters" field at all (the bare identifier is
    # an unnamed positional child, not wrapped in formal_parameters) -- a
    # documented, deliberate residual gap rather than teaching the generic
    # traversal a per-node-type positional fallback.
    file_path = tmp_path / "mod.ts"
    file_path.write_text("const double = x => x * 2;\n", encoding="utf-8")

    assert extract_signatures(str(file_path)) == []


def test_extract_api_detects_decorator_based_routes(tmp_path):
    file_path = tmp_path / "app.py"
    file_path.write_text(
        "@app.get('/users')\n"
        "def list_users():\n"
        "    return []\n",
        encoding="utf-8",
    )

    api = extract_api(str(file_path))
    assert "GET /users" in api


def test_unsupported_extension_returns_empty_lists(tmp_path):
    file_path = tmp_path / "notes.xyz"
    file_path.write_text("whatever", encoding="utf-8")

    assert extract_signatures(str(file_path)) == []
    assert extract_dependencies(str(file_path)) == []
    assert extract_api(str(file_path)) == []
