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
import tree_sitter_cpp as tscpp
import tree_sitter_rust as tsrust

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


def _cpp_dependency_handler(node: Node, results: list) -> bool:
    """`#include` is its own preproc_include node (a preprocessor
    directive, not part of the language grammar proper the way an
    import_statement is) with a "path" field holding either a
    system_lib_string (`<iostream>`, angle-bracket-delimited, no nested
    content node -- the delimiters are stripped directly) or a
    string_literal (`"utils.h"`, quoted, with the usual nested
    string_content child every other quoted string in this grammar has).

    Normalized to the bare stem (Path(path).stem), the same restraint
    _gdscript_dependency_handler already takes for its own path-based
    imports: a local include's path is relative to whatever the compiler's
    own -I search paths are, which won't generally match the file's own
    collected relative-key path exactly (`#include "models/user.hpp"`
    doesn't guarantee the project's own layout puts user.hpp under a
    models/ directory from the collection root) -- resolve_dependency()'s
    exact-stem-match branch is the one this needs to hit, not its raw-
    dotted-path fallback (which assumes "." separates module segments, not
    a file extension).
    """
    if node.type != "preproc_include":
        return False
    path_node = node.child_by_field_name("path")
    if path_node is not None:
        if path_node.type == "system_lib_string":
            results.append(Path(path_node.text.decode().strip("<>")).stem)
        elif path_node.type == "string_literal":
            for child in path_node.children:
                if child.type == "string_content":
                    results.append(Path(child.text.decode()).stem)
                    break
    return True


def _append_rust_use_paths(path_text: str, results: list) -> None:
    """Expands one use_declaration argument's raw text into one dotted
    dependency path per imported symbol, recursively -- text-based rather
    than a node-type traversal (like _java_dependency_handler's own raw-
    text stripping) since this grammar's argument shape varies more than
    any other supported language's import syntax: a plain path
    (scoped_identifier), an aliased path (use_as_clause, `as new_name`), a
    glob (use_wildcard, `foo::*`), and -- unique to Rust among supported
    languages -- a single statement naming several sibling paths under one
    shared prefix (scoped_use_list, `use std::{fmt, collections::HashMap}`),
    which can itself nest arbitrarily deep.

    "::" is Rust's path separator, normalized to "." throughout so the
    result matches every other handler's dotted-path convention --
    resolve_dependency()'s existing split-on-"."-take-last-segment
    fallback then applies unchanged, no Rust-specific matching needed
    there.

    An alias (`as baz`) is dropped -- the alias name isn't a real path
    segment resolve_dependency() could ever match against a file stem, so
    keeping the original path is what best doubles as a guess.
    """
    path_text = path_text.split(" as ")[0].strip()

    if path_text.endswith("::*"):
        path_text = path_text[:-3].strip()
        if path_text:
            results.append(path_text.replace("::", "."))
        return

    if path_text.endswith("}") and "{" in path_text:
        prefix, _, group = path_text.partition("{")
        prefix = prefix.rstrip(":").strip()
        inner = group[:-1]

        # Comma-split the group's own items, but only at depth 0 -- an
        # item can itself be a nested group (`fmt::{self, Display}`
        # inside an outer group), whose internal commas must not split
        # the outer list.
        items, item, depth = [], "", 0
        for ch in inner:
            if ch == "{":
                depth += 1
                item += ch
            elif ch == "}":
                depth -= 1
                item += ch
            elif ch == "," and depth == 0:
                items.append(item)
                item = ""
            else:
                item += ch
        if item.strip():
            items.append(item)

        for raw_item in items:
            raw_item = raw_item.strip()
            if not raw_item:
                continue
            combined = f"{prefix}::{raw_item}" if prefix else raw_item
            _append_rust_use_paths(combined, results)
        return

    if path_text:
        results.append(path_text.replace("::", "."))


def _rust_dependency_handler(node: Node, results: list) -> bool:
    """Two distinct dependency shapes in Rust, both handled here:

    `mod foo;` (mod_item, no body -- a body-having `mod foo { ... }` is an
    inline submodule, not a file reference, so only the field-declaration
    form is a real file dependency) declares a submodule backed by its own
    file (foo.rs or foo/mod.rs) -- the single most precise, directly
    file-stem-resolvable dependency shape Rust has, read straight off the
    "name" field with no path unwrapping needed at all.

    `use path::to::Item;` (use_declaration) is a symbol import, not
    necessarily a file reference -- see _append_rust_use_paths()'s own
    docstring for the shapes its argument can take. Known, accepted
    limitation shared with Go's own dependency_handler: a use path's last
    segment often names a *type* (PascalCase, e.g. `crate::models::User`),
    not the *file* that defines it (typically snake_case, user.rs) --
    resolve_dependency()'s stem-matching will frequently miss this the
    same way Go's package-vs-file granularity mismatch does. mod_item
    resolution above is what actually carries most of Rust's real
    internal file-dependency precision; use_declaration capture is best
    treated as a bonus, not the primary signal.
    """
    if node.type == "mod_item":
        name = node.child_by_field_name("name")
        if name:
            results.append(name.text.decode())
        return True

    if node.type == "use_declaration":
        argument = node.child_by_field_name("argument")
        if argument is not None:
            _append_rust_use_paths(argument.text.decode().strip(), results)
        return True

    return False


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


# `.cpp`/`.cc`/`.cxx` all parse with the same grammar, so all three
# extension keys below share this exact config object -- unlike
# `.ts`/`.js` (genuinely different grammar variants, TS vs. TSX, each
# needing its own Language()/config), there's no per-extension difference
# here to justify separate instances.
#
# function_definition covers every function shape in this grammar
# uniformly -- a free function, an in-class method, an out-of-class
# `ReturnType ClassName::method(...)` definition, a constructor, and a
# destructor all parse as the same node type, unlike Java/Go's separate
# method_declaration. What's genuinely new here (not covered by any
# existing per-language quirk): a function's actual name and parameter
# list aren't direct fields of function_definition at all -- they're
# nested one level inside a "declarator" sub-node (function_declarator),
# which extractor.py's _unwrap_declarator()/_resolve_signature_name()
# handle generically (see their own docstrings), not as a per-language
# hardcode -- any grammar with this same declarator-indirection shape
# (the wider C family) would resolve the same way.
#
# Deliberately scoped to implementation files only (.cpp/.cc/.cxx), not
# .h/.hpp headers: a header with only prototype declarations (no function
# body -- `void foo();`, or a pure virtual `virtual void foo() = 0;`)
# parses as a plain `declaration`/`field_declaration`, never a
# function_definition, so header-only files would extract zero signatures
# under this config anyway. Whether/how to also cover declaration-only
# headers (and the real C-vs-C++ ambiguity a bare .h extension carries)
# is left as a follow-up decision, not folded into this pass.
_cpp_config = LanguageConfig(
    language=Language(tscpp.language()),
    function_types=["function_definition"],
    dependency_handler=_cpp_dependency_handler,
)


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
    # See _cpp_config's own definition above for what .cpp/.cc/.cxx share
    # and why they're all one config object.
    ".cpp": _cpp_config,
    ".cc": _cpp_config,
    ".cxx": _cpp_config,
    ".rs": LanguageConfig(
        language=Language(tsrust.language()),
        # Unlike C++, this grammar exposes a function's "name"/"parameters"/
        # "return_type"/"body" as direct fields of function_item itself --
        # no declarator indirection, so no extractor.py changes were needed
        # at all (not even a second return-type field name: "return_type"
        # already matches Python/TS/GDScript's own convention). One
        # function_item covers every shape uniformly: a free function, an
        # inline impl method, and a trait-impl method all parse identically
        # (the enclosing impl_item/trait_item is simply never read).
        # function_signature_item (a trait's own method declaration with no
        # body, `fn greet(&self) -> String;`) is included too -- it still
        # has "name"/"parameters"/"return_type" for a real signature, and
        # having no "body" field is harmless for compression (_collect_
        # bodies' `if body:` guard just finds nothing to strip, correctly
        # leaving the one-line declaration untouched).
        function_types=["function_item", "function_signature_item"],
        dependency_handler=_rust_dependency_handler,
    ),
}


def get_language_config(ext: str) -> LanguageConfig | None:
    return LANGUAGE_CONFIGS.get(ext)
