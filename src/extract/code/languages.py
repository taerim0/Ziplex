"""Collects per-language Tree-sitter processing config in one place.

Supporting a new extension means adding one LanguageConfig entry here (plus a
dependency handler if needed) — nothing else. extractor.py / compressor.py only
ever reference this config; they don't hardcode per-language node types themselves.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from tree_sitter import Language, Node
import tree_sitter_python as tspython
import tree_sitter_java as tsjava
import tree_sitter_typescript as tstypescript
import tree_sitter_lua as tslua

# GDScript has no dedicated tree-sitter-gdscript PyPI package (as of this
# writing) the way the languages above do -- only a community grammar
# (PrestonKnopp/tree-sitter-gdscript) with no packaged Python binding of its
# own. tree-sitter-language-pack bundles a prebuilt copy of it (among ~370
# others) behind get_language(name), so that's used instead of a dedicated
# import; everything downstream still just gets a plain Language object,
# same as every other entry below.
from tree_sitter_language_pack import get_language as _get_bundled_language

# When an import-statement node is hit, fills results with the module name and
# returns True (handled, stop recursing into children). Returns False for
# non-import nodes so traversal continues. Import syntax differs too much between
# languages (field presence, node names, etc.) to share one generic routine, so
# each language gets its own small handler instead.
#
# The strings a handler appends are matched later by file/relationship.py's
# resolve_dependency() -- most languages append a raw dotted module path
# (Python/Java/Lua), but a handler for a language whose imports are file
# *paths* rather than module names should normalize to the bare stem first
# (see _gdscript_dependency_handler and resolve_dependency()'s own
# docstring for why the two shapes need different matching).
DependencyHandler = Callable[[Node, list], bool]


def _py_dependency_handler(node: Node, results: list) -> bool:
    if node.type == "import_from_statement":
        module = node.child_by_field_name("module_name")
        if module:
            results.append(module.text.decode())
        return True

    if node.type == "import_statement":
        for child in node.children:
            if child.type == "dotted_name":
                results.append(child.text.decode())
        return True

    return False


def _java_dependency_handler(node: Node, results: list) -> bool:
    if node.type == "import_declaration":
        # no field for this, so strip "import"/"static"/";" from the raw text to
        # leave just the module path.
        text = node.text.decode().strip().rstrip(";").strip()
        text = text.removeprefix("import").strip()
        text = text.removeprefix("static").strip()
        if text:
            results.append(text)
        return True

    return False


def _ts_dependency_handler(node: Node, results: list) -> bool:
    if node.type == "import_statement":
        source = node.child_by_field_name("source")
        if source:
            results.append(source.text.decode().strip("'\""))
        return True

    return False


def _lua_dependency_handler(node: Node, results: list) -> bool:
    # Lua has no import statement -- `require(...)` is an ordinary function
    # call, so the handler has to recognize it by name rather than by a
    # dedicated node type. Only literal string arguments are resolvable
    # statically (`require(modname_expr)` is left alone, same as any other
    # language skips a dynamically-computed import path); either way, a
    # `function_call` node is always fully handled here, never worth
    # recursing into further (there's nothing else import-shaped inside its
    # own arguments).
    if node.type != "function_call":
        return False
    callee = node.child_by_field_name("name")
    if callee is None or callee.type != "identifier" or callee.text.decode() != "require":
        return False
    args_node = node.child_by_field_name("arguments")
    if args_node is not None:
        for child in args_node.children:
            if child.type == "string":
                for grandchild in child.children:
                    if grandchild.type == "string_content":
                        results.append(grandchild.text.decode())
                        break
    return True


def _append_gdscript_path_stems(container: Node, results: list) -> None:
    """Shared by every branch below: scans container's direct children for
    string literals and appends each one's bare stem to results.

    Normalizing to the stem (not the raw "res://scripts/config.gd" text)
    matters beyond tidiness: a preload/load/extends argument is a
    res://-prefixed *file path with an extension*, unlike a dotted Lua/
    Python/Java module path -- file/relationship.py's resolve_dependency()
    now special-cases an exact stem_map-key match for exactly this shape
    (see its docstring), but only once it's actually been reduced to a
    bare stem here. A preload of a non-script resource (a .tscn scene, a
    .png) just resolves as "external" like any dependency string that
    doesn't match a collected file -- not a special case, the existing
    internal/external split already handles it.
    """
    for child in container.children:
        if child.type == "string":
            path = child.text.decode().strip("'\"")
            results.append(Path(path).stem)


def _gdscript_dependency_handler(node: Node, results: list) -> bool:
    # preload("res://...")/load("res://...") are ordinary calls, not a
    # dedicated import-statement node -- same idea as Lua's require(). Two
    # node types both need this: a bare `load(...)` parses as `call`, but a
    # qualified `ResourceLoader.load(...)` parses as `attribute(identifier,
    # ".", attribute_call(...))` -- the attribute_call is what actually
    # holds the callee name + arguments, shaped identically to `call`
    # (first child is the callee identifier, no field name for it in this
    # grammar -- unlike Lua's function_call, which has a "name" field, so
    # it's read positionally). `attribute` itself isn't matched, so
    # traversal still recurses into it and reaches the attribute_call child.
    if node.type in ("call", "attribute_call"):
        callee = node.children[0] if node.children else None
        if callee is not None and callee.type == "identifier" and callee.text.decode() in ("preload", "load"):
            args_node = node.child_by_field_name("arguments")
            if args_node is not None:
                _append_gdscript_path_stems(args_node, results)
            return True
        return False

    # `extends "res://Base.gd"` -- path-based inheritance, as real a
    # dependency as an import. `extends Node`/`extends SomeClass` (a bare
    # identifier/type, referencing a built-in engine class or a
    # class_name declared elsewhere) isn't resolvable to a file path from
    # syntax alone, so it's deliberately left alone -- the same "only
    # capture what's a real path" restraint every other handler here has.
    if node.type == "extends_statement":
        _append_gdscript_path_stems(node, results)
        return True

    return False


@dataclass(frozen=True)
class LanguageConfig:
    language: Language
    function_types: list[str]              # node types targeted for signature extraction + body compression
    dependency_handler: DependencyHandler   # strategy for extracting import statements


LANGUAGE_CONFIGS: dict[str, LanguageConfig] = {
    ".py": LanguageConfig(
        language=Language(tspython.language()),
        function_types=["function_definition"],
        dependency_handler=_py_dependency_handler,
    ),
    ".java": LanguageConfig(
        language=Language(tsjava.language()),
        function_types=["method_declaration"],
        dependency_handler=_java_dependency_handler,
    ),
    ".ts": LanguageConfig(
        language=Language(tstypescript.language_typescript()),
        # arrow_function/function_expression cover the two anonymous-function
        # shapes function_declaration/method_definition miss entirely --
        # `const add = (a, b) => ...`, `const handler = function(req, res)
        # {...}`, object-literal methods, class-field arrows. Neither has a
        # "name" field of its own when anonymous; extractor.py's
        # _resolve_signature_name() falls back to the wrapping
        # variable_declarator/public_field_definition/pair node's own
        # "name"/"key" field to recover one. One known residual gap: a bare
        # single-identifier-parameter arrow with no parens (`x => x * 2`)
        # has no "parameters" field at all (the identifier is an unnamed
        # positional child) and so still produces no signature -- left
        # undone rather than teaching the generic extractor a
        # per-node-type positional fallback, same restraint as GDScript's
        # constructor_definition gap below. Doesn't affect body compression
        # either way, which only needs a "body" field.
        function_types=["function_declaration", "method_definition", "arrow_function", "function_expression"],
        dependency_handler=_ts_dependency_handler,
    ),
    ".js": LanguageConfig(
        language=Language(tstypescript.language_tsx()),
        function_types=["function_declaration", "method_definition", "arrow_function", "function_expression"],
        dependency_handler=_ts_dependency_handler,
    ),
    ".lua": LanguageConfig(
        language=Language(tslua.language()),
        # Covers all three named forms Lua's grammar folds into one node
        # type: `local function f()`, `function Tbl.f()`, and the
        # colon/method form `function Tbl:f()` -- child_by_field_name("name")
        # returns "f", "Tbl.f", and "Tbl:method" respectively, all already
        # readable as a signature with no extra handling needed.
        function_types=["function_declaration"],
        dependency_handler=_lua_dependency_handler,
    ),
    ".gd": LanguageConfig(
        language=_get_bundled_language("gdscript"),
        # `class_definition` (a nested `class Inner:` block) isn't in here --
        # only function/constructor definitions -- matching every other
        # language: methods/functions are extracted, type declarations
        # aren't. A method inside a nested class is still found regardless,
        # since extractor.py's traversal is fully recursive rather than one
        # level deep.
        #
        # constructor_definition is its own node type, not a
        # function_definition -- Godot's `_init` (its standard constructor)
        # parses distinctly, so without this it would ship fully
        # uncompressed while every sibling method got body-stripped. It
        # doesn't help extract_signatures() the same way: this node has no
        # "name" field at all (there's nothing to name -- it's always
        # _init), so _traverse_signatures()'s `if name and params` guard
        # skips it and no signature entry is produced. Left as a known,
        # minor gap rather than teaching the generic (language-agnostic)
        # extractor.py about a GDScript-specific fallback name.
        function_types=["function_definition", "constructor_definition"],
        dependency_handler=_gdscript_dependency_handler,
    ),
}


def get_language_config(ext: str) -> LanguageConfig | None:
    return LANGUAGE_CONFIGS.get(ext)
