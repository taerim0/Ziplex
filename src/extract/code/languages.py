"""Collects per-language Tree-sitter processing config in one place.

Supporting a new extension means adding one LanguageConfig entry here (plus a
dependency handler if needed) — nothing else. extractor.py / compressor.py only
ever reference this config; they don't hardcode per-language node types themselves.
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from tree_sitter import Language, Node
import tree_sitter_python as tspython
import tree_sitter_java as tsjava
import tree_sitter_typescript as tstypescript
import tree_sitter_lua as tslua
import tree_sitter_go as tsgo

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

# Same (Node, list) -> bool shape and traversal contract as DependencyHandler
# above (True = handled, stop recursing into this node's children; False =
# not a route-shaped node, keep looking). A language with no established
# web-routing convention (Lua, GDScript) just has no handler at all
# (LanguageConfig.api_handler defaults to None) -- extract_api() then
# returns [] for it outright rather than running a traversal that could
# never match anything, same as extract_dependencies() would for a
# language with no imports of its own.
ApiHandler = Callable[[Node, list], bool]


def _walk_all(node: Node):
    """Every descendant of node, node itself included -- shared by the API
    handlers below to search a small subtree (a decorator, an arguments
    list) for a nested node type without writing a bespoke recursive
    search each time. Deliberately not extractor.py's own traversal
    (_traverse_dependencies/_traverse_api) -- this is unconditional (never
    stops early on a match), used to search *inside* a node a handler has
    already decided is relevant, not to decide relevance itself.
    """
    yield node
    for child in node.children:
        yield from _walk_all(child)


def _py_api_handler(node: Node, results: list) -> bool:
    """Flask-style route detection: a @app.get("/path")-shaped decorator
    on a function/class definition. Matches on ".get"/".post"/etc.
    appearing anywhere in the decorator's own text rather than requiring a
    specific object name (app/bp/blueprint/router all decorate routes this
    way in real Flask code) -- a heuristic, not a semantic guarantee, the
    same restraint every dependency_handler above already takes for its
    own language's import syntax.
    """
    if node.type != "decorated_definition":
        return False

    decorator = None
    for child in node.children:
        if child.type == "decorator":
            decorator = child
            break

    if decorator:
        method = None
        path = None

        for n in _walk_all(decorator):
            if n.type == "attribute":
                attr_text = n.text.decode()
                if ".get"      in attr_text: method = "GET"
                elif ".post"   in attr_text: method = "POST"
                elif ".put"    in attr_text: method = "PUT"
                elif ".delete" in attr_text: method = "DELETE"
                elif ".patch"  in attr_text: method = "PATCH"
                break

        for n in _walk_all(decorator):
            if n.type == "string_content":
                path = n.text.decode()
                break

        if method and path:
            results.append(f"{method} {path}")

    return True


_HTTP_METHOD_CALLS = {"get": "GET", "post": "POST", "put": "PUT", "delete": "DELETE", "patch": "PATCH"}


def _js_api_handler(node: Node, results: list) -> bool:
    """Express-style route detection: an `app.get("/path", ...)`-shaped
    call, matched the same way Flask's decorator is above -- by the
    property name (get/post/put/delete/patch) on whatever object it's
    called on (app/router/api all register routes this way in real
    Express code), not a specific variable name. Only a plain string-
    literal first argument counts as a resolvable path -- a template
    literal with interpolation (`/users/${id}`) is left uncaptured, same
    "only capture what's statically resolvable" restraint
    _lua_dependency_handler takes for a dynamically-computed require().
    A route path is additionally required to start with "/", the one
    signal that distinguishes an actual route registration from an
    unrelated same-shaped call (Map.get("key"), someMap.get(id)) that
    happens to use the same method name on some other kind of object --
    still a heuristic, not a semantic guarantee, since a legitimate
    non-routing `.get("/looks/like/a/path")` call can't be ruled out from
    syntax alone.

    Always returns False (keep recursing), unlike _py_api_handler's
    unconditional True for a matched decorated_definition -- a decorator
    can't meaningfully nest another route inside itself, but an Express
    callback body is an ordinary argument to this same call_expression, so
    a rare nested registration (`app.get("/a", () => { app.get("/b", ...)
    })`) is still worth finding rather than silently dropped just because
    the outer call already matched.
    """
    if node.type != "call_expression":
        return False

    func = node.child_by_field_name("function")
    if func is None or func.type != "member_expression":
        return False

    property_node = func.child_by_field_name("property")
    method = _HTTP_METHOD_CALLS.get(property_node.text.decode()) if property_node else None
    if method is None:
        return False

    args = node.child_by_field_name("arguments")
    if args is not None:
        # The first *argument*, not just the first string anywhere in the
        # arguments list -- a call whose first argument isn't a string but
        # a later one happens to be (and happens to start with "/") would
        # otherwise grab that unrelated string as the "path".
        non_syntax = [c for c in args.children if c.type not in ("(", ")", ",")]
        first_arg = non_syntax[0] if non_syntax else None
        if first_arg is not None and first_arg.type == "string":
            for n in _walk_all(first_arg):
                if n.type == "string_fragment":
                    path = n.text.decode()
                    if path.startswith("/"):
                        results.append(f"{method} {path}")
                    break

    return False


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


def _go_dependency_handler(node: Node, results: list) -> bool:
    """Matches on import_spec, not import_declaration -- Go's grammar gives
    both a single import (`import "fmt"`) and a grouped block (`import
    (...)`) the same import_spec unit underneath (directly under
    import_declaration for the single form, nested one level inside
    import_spec_list for the grouped form), so matching the inner node
    covers both shapes without needing to know which one wraps it.

    Only the "path" field's string content is captured -- an aliased
    (`myalias "path/filepath"`) or blank (`_ "encoding/json"`) import's own
    "name" field is irrelevant to dependency resolution, which only cares
    what's actually being imported.

    Known, accepted limitation: unlike every other language here, a Go
    import path (`"myproject/internal/utils"`) names a *package*
    (typically a whole directory of files sharing one namespace), not a
    single file -- Ziplex's dependency graph is file-to-file throughout
    (resolve_dependency() matches against file stems). An internal
    multi-package import will therefore usually resolve as "external"
    rather than being matched to a specific file, the same way it would
    for any language if resolve_dependency() only ever matched files, not
    directories. Not fixed here: doing so properly would need directory-
    level resolution (and reading go.mod for the module's own import
    prefix), a materially bigger change than a dependency_handler can
    make on its own. Many real Go projects are single-package anyway
    (no internal imports to resolve in the first place), which tempers
    how often this actually bites in practice.
    """
    if node.type != "import_spec":
        return False
    path_node = node.child_by_field_name("path")
    if path_node is not None:
        for child in path_node.children:
            if child.type == "interpreted_string_literal_content":
                results.append(child.text.decode())
                break
    return True


@dataclass(frozen=True)
class LanguageConfig:
    language: Language
    function_types: list[str]              # node types targeted for signature extraction + body compression
    dependency_handler: DependencyHandler   # strategy for extracting import statements
    api_handler: ApiHandler | None = None   # strategy for extracting REST-route declarations, if this language has one
    # Fallback name for a function_types node whose grammar gives it neither
    # its own "name" field nor a wrapping node with one -- e.g. GDScript's
    # constructor_definition, whose only "name" is the fixed keyword token
    # "_init" itself (a literal, not an identifier under a field), unlike
    # every other language here where a function names itself via a real
    # field. Keeps this per-language fact in languages.py, where every
    # other per-language quirk already lives, rather than teaching
    # extractor.py's generic _resolve_signature_name() a GDScript-specific
    # string.
    implicit_names: dict[str, str] = field(default_factory=dict)


LANGUAGE_CONFIGS: dict[str, LanguageConfig] = {
    ".py": LanguageConfig(
        language=Language(tspython.language()),
        function_types=["function_definition"],
        dependency_handler=_py_dependency_handler,
        api_handler=_py_api_handler,
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
        api_handler=_js_api_handler,
    ),
    ".js": LanguageConfig(
        language=Language(tstypescript.language_tsx()),
        function_types=["function_declaration", "method_definition", "arrow_function", "function_expression"],
        dependency_handler=_ts_dependency_handler,
        api_handler=_js_api_handler,
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
        # has no "name" field at all (there's nothing to name in the
        # grammar's own terms -- the keyword "_init" is a literal token,
        # not an identifier under a field), so implicit_names supplies the
        # one fixed name every constructor_definition in any GDScript file
        # always has.
        function_types=["function_definition", "constructor_definition"],
        dependency_handler=_gdscript_dependency_handler,
        implicit_names={"constructor_definition": "_init"},
    ),
    ".go": LanguageConfig(
        language=Language(tsgo.language()),
        # method_declaration covers a receiver method (`func (s *Server)
        # Start() ...`) separately from a plain function_declaration --
        # both expose "name"/"parameters"/"body" fields directly (no
        # anonymous-function parent-fallback needed, unlike TS/JS), and
        # method_declaration's "receiver" field (the `(s *Server)` part)
        # is simply never read, since extractor.py only ever looks at
        # "name"/"parameters"/"result"/"body".
        function_types=["function_declaration", "method_declaration"],
        dependency_handler=_go_dependency_handler,
    ),
}


def get_language_config(ext: str) -> LanguageConfig | None:
    return LANGUAGE_CONFIGS.get(ext)
