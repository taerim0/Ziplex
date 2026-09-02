"""Collects per-language Tree-sitter processing config in one place.

Supporting a new extension means adding one LanguageConfig entry here (plus a
dependency handler if needed) — nothing else. extractor.py / compressor.py only
ever reference this config; they don't hardcode per-language node types themselves.
"""

import sys
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
import tree_sitter_c_sharp as tscsharp
import tree_sitter_php as tsphp
import tree_sitter_ruby as tsruby
import tree_sitter_bash as tsbash

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

# Deliberately NOT the same (Node, list) -> bool "stop recursing" contract
# DependencyHandler/ApiHandler use: extractor.py's _traverse_signatures()
# always keeps recursing into a matched node's own children regardless of
# what this returns, since a field's initializer can itself legally contain
# a nested field_declaration (e.g. an anonymous class body) -- an import or
# a route declaration never nests a second one of itself the way a field
# initializer can, which is why those two get to stop early and this one
# can't. Recognizes a class-level field whose initializer is itself a call
# (`public static final Item RUBY_TOOL = register(...);`) -- a pattern
# extract_signatures()'s function-only traversal can never see on its own,
# since a field declaration isn't a function_types node. See
# _java_field_handler's own docstring for the real case that motivated
# this. Scoped to Java only for now (LanguageConfig.field_handler defaults
# to None, same "no handler at all" pattern api_handler already uses for a
# language with nothing to look for) -- the pattern is common enough in
# other languages too (a TS/JS module-level `const X = register(...)`, a
# Python module-level `X = register(...)`) to extend later, but each needs
# its own handler; a field declaration's grammar shape isn't shared the way
# function_types' basic name/parameters/body fields mostly are.
FieldHandler = Callable[[Node, list], None]


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


# The running interpreter's own authoritative module list (Python 3.10+,
# well under Ziplex's own requires-python = ">=3.11") -- always accurate for
# whatever Python version actually runs Ziplex, no hardcoded/maintained list
# to fall out of date across releases. Used by _is_stdlib_import() below to
# keep a plain `import json` from ever being handed to resolve_dependency()
# as a raw dependency string in the first place -- see that function's own
# docstring/comment for why this has to happen here, at extraction time, not
# by trying to teach the shared, cross-language resolve_dependency() to be
# more careful about a bare-stem match.
_PYTHON_STDLIB_MODULES = sys.stdlib_module_names


def _is_stdlib_import(text: str) -> bool:
    """True if text's first dotted segment names a real Python standard-
    library module -- e.g. "json" (bare) or "xml.etree.ElementTree" (whose
    first segment "xml" is what actually matters; resolve_dependency()'s own
    fallback would otherwise re-split this on "." and try to match its last
    segment, "ElementTree", against a project file). A relative import
    (".file.collector") always has an empty first segment (nothing precedes
    its leading dot), which never matches anything here -- this only ever
    filters *absolute* imports, the only shape that could plausibly name an
    external package rather than a project file.

    Found via a real bug, not written defensively in the abstract: packing
    Ziplex's own repo, a plain `import json` in dozens of files kept
    resolving as an internal dependency on `extract/text/json.py` purely
    because that local file's stem happens to also be "json" --
    resolve_dependency()'s bare-stem fallback (the same mechanism GDScript's
    preload()/C++'s #include correctly rely on for their own path-reduced-
    to-stem dependencies) has no way to tell "a genuinely local single-file
    reference" apart from "an absolute import of a same-named standard-
    library module" once both have been reduced to the same bare string.
    Scoped to the standard library specifically (not third-party packages
    too) because sys.stdlib_module_names is accurate regardless of which
    project Ziplex happens to be packing right now -- unlike a third-party
    package name, whose "is this actually installed" answer depends on the
    *target* project's own environment, which Ziplex never runs inside of
    (see tech_stack.py's own similarly deliberate scope limit).
    """
    return text.split(".")[0] in _PYTHON_STDLIB_MODULES


def _append_if_not_stdlib(text: str, results: list) -> None:
    """Shared by both import shapes _py_dependency_handler recognizes below
    -- factored out so the "decode, check stdlib, append" pattern lives in
    exactly one place, not duplicated identically in both branches (a
    future change to the filtering condition would otherwise risk the two
    copies drifting apart from each other).
    """
    if not _is_stdlib_import(text):
        results.append(text)


def _py_dependency_handler(node: Node, results: list) -> bool:
    if node.type == "import_from_statement":
        module = node.child_by_field_name("module_name")
        if module:
            _append_if_not_stdlib(module.text.decode(), results)
        return True

    if node.type == "import_statement":
        for child in node.children:
            if child.type == "dotted_name":
                _append_if_not_stdlib(child.text.decode(), results)
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


def _java_field_handler(node: Node, results: list) -> None:
    """Captures a class-level field whose initializer is itself a method
    call (`public static final Item RUBY_TOOL = register("ruby_tool", new
    ToolItem());`) -- a pattern extract_signatures()'s function-only
    traversal structurally can never see, since field_declaration isn't
    one of Java's function_types. This is often a file's *actual* primary
    behavior (a registry class defining a whole set of constants via a
    factory call), invisible to confidence.py's word-overlap scoring and a
    human review screen's "real signatures" display without it -- a real,
    observed case (not hypothetical): a mod's ModItems.java, whose accurate
    LLM summary ("Defines and registers custom items...") scored a
    misleadingly low confidence because the only thing extract_signatures()
    had ever captured from it was one incidental helper method, with none
    of the actual item-registration calls to check the summary against.

    Deliberately scoped to this node type only, not a generic "any
    identifier assigned from a call" search -- field_declaration only ever
    appears as a direct class-body member; a local variable inside a method
    body is a distinct local_variable_declaration node the grammar never
    confuses with this one, so this can't misfire on an incidental
    assignment deep inside a function body.

    Only a declarator whose value is itself a method_invocation counts --
    excludes a plain literal (`private int count = 0;`), a bare
    object-creation expression (`new ArrayList<>()`, extremely common and,
    unlike a *named* factory/registration call, not itself informative),
    and a field with no initializer at all. A single field_declaration can
    define several comma-separated declarators at once (`A = f(), B =
    g();`) -- children_by_field_name("declarator") (plural) is required
    here, not child_by_field_name (singular), which would silently return
    only the first and drop every one after it.

    No return value, unlike dependency_handler/api_handler's "stop
    recursing" bool -- _traverse_signatures() always keeps recursing into
    this node's own children regardless. That matters here specifically: a
    field's initializer *can* legally contain a nested field_declaration of
    its own (an anonymous class body, `new Comparator<Item>() { private
    final Item helper = register("helper"); ... };`), which an early "stop,
    fully handled" return would silently miss -- confirmed against the real
    grammar, not just a theoretical concern.

    The value's own text is whitespace-normalized (collapsed to single
    spaces) before appending -- a real registration call is routinely
    wrapped across several indented source lines (a lambda argument, e.g.
    `ITEMS.register("ruby",\n    () -> new Item(...))`), and every other
    signature this function produces is a single line; a raw multi-line
    entry would otherwise break corrector.py's one-line-per-signature
    review display. This collapses whitespace inside a string-literal
    argument too (a documented, accepted imprecision, not a correctness
    concern for what this exists to capture: the registration shape and
    its own identifier, not an argument's exact literal formatting).
    """
    if node.type != "field_declaration":
        return
    for declarator in node.children_by_field_name("declarator"):
        name = declarator.child_by_field_name("name")
        value = declarator.child_by_field_name("value")
        if name and value is not None and value.type == "method_invocation":
            value_text = " ".join(value.text.decode().split())
            results.append(f"{name.text.decode()} = {value_text}")


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

    Unlike every other language here, a Go import path
    (`"myproject/internal/utils"`) names a *package* (typically a whole
    directory of files sharing one namespace), not a single file -- this
    handler still only ever returns that raw path string as-is, same as
    any other language's import. What used to be a permanent, accepted
    limitation (an internal multi-package import always resolving as
    "external", since resolve_dependency() only ever matches file stems,
    never directories) is now closed one layer up, not here: `go_packages.
    py`'s `expand_go_dependencies()` -- called by every real caller of
    extract_dependencies() on a .go file (packager.py's per-file loop,
    cli.py's `tree` subcommand) -- reads go.mod's own module path and
    expands a package import into the concrete files that make it up
    before resolve_dependency() ever sees it. Kept as a separate,
    project-wide post-processing step rather than folded into this
    per-file, per-node handler because it needs information this handler
    structurally can't have: the whole project's collected .go file list
    and go.mod's module path, neither available from a single AST node.
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

    Group-splitting is checked *before* alias-stripping, not after: a
    group's own siblings can themselves carry an alias
    (`foo::{bar as baz, qux}`), and splitting " as " off the whole
    argument text first would truncate the group mid-string (into
    "foo::{bar", losing "qux" entirely and leaving a dangling "{") before
    the group was ever recognized as one. Each split-out sibling is fed
    back through this same function recursively, so a per-item alias
    (`bar as baz` above) still gets stripped correctly, just one level
    down instead of on the whole statement up front.
    """
    path_text = path_text.strip()

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

    path_text = path_text.split(" as ")[0].strip()

    if path_text.endswith("::*"):
        path_text = path_text[:-3].strip()
        if path_text:
            results.append(path_text.replace("::", "."))
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
    "name" field with no path unwrapping needed at all. An inline
    `mod foo { ... }` (real, common e.g. for `#[cfg(test)] mod tests {
    ... }`) is deliberately left unmatched -- its own name isn't a file
    reference at all, and its body can itself contain real use/mod
    declarations (a nested test module importing `crate::...` is the
    ordinary case), which still need to be found by recursing into it
    rather than having this handler swallow the whole subtree as one
    bogus dependency.

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
        if node.child_by_field_name("body") is not None:
            # inline submodule -- not a file reference; keep recursing so
            # any real use/mod declarations inside its body are still found
            return False
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


def _csharp_dependency_handler(node: Node, results: list) -> bool:
    """`using X;`/`using X.Y.Z;`/`using static X.Y;`/`global using X;`/
    `using Alias = X.Y;` all parse as one using_directive node type, but
    none of its shapes exposes the actually-imported path under a
    consistently-named field the way most other languages' import nodes
    do: a plain/static/global using has no field name at all for its path
    child (a bare positional "identifier" or "qualified_name"), and the
    aliased form's own "name" field actually names the *alias* itself,
    not the real path -- the real path is the qualified_name/identifier
    child that comes after "=" instead, with no field name of its own
    either. "static"/"global" are literal keyword tokens (their own
    node.type, not "identifier"), so they never get mistaken for a path.

    Read positionally instead, like Lua's/GDScript's own callee-name
    reads: the *last* identifier/qualified_name-typed child is the real
    path for a plain/static/global using (the only such child at all),
    and the one after "=" for an aliased using (since the alias
    identifier is always the *first* one, appearing before "="). Already
    dot-separated (a qualified_name's own text already uses "." as C#'s
    real namespace separator, unlike Rust's "::"), so no normalization is
    needed before appending -- resolve_dependency()'s existing
    split-on-"."-take-last-segment fallback applies directly, same as
    Java's own dotted import text.

    For an aliased using, the search is scoped to children *after* "="
    -- not simply "the last identifier/qualified_name in the whole
    node," which code review caught being wrong: a type alias whose
    right-hand side isn't itself an identifier/qualified_name (`using
    MyInt = int;`, `using IntArray = int[];`, `using Nullable =
    System.Int32?;` -- predefined_type/array_type/nullable_type/etc. are
    all real, common alias targets) has no path-shaped child after "="
    at all, so the whole-node search fell back to the *alias name*
    itself (the "identifier" before "="), wrongly emitting it as a
    bogus dependency. Scoping to after "=" makes that case correctly
    find nothing instead, the same "only capture what's a real path"
    restraint every other handler here already takes for an
    unresolvable target (e.g. GDScript's `extends Node`).
    """
    if node.type != "using_directive":
        return False

    equals_index = next((i for i, child in enumerate(node.children) if child.type == "="), None)
    search_children = node.children[equals_index + 1:] if equals_index is not None else node.children

    path_node = None
    for child in search_children:
        if child.type in ("identifier", "qualified_name"):
            path_node = child
    if path_node is not None:
        results.append(path_node.text.decode())
    return True


def _append_php_use_clause(clause: Node, prefix: str | None, results: list) -> None:
    """A single namespace_use_clause's real path, with an optional group
    prefix prepended (see _php_dependency_handler below for the two shapes
    this is called from). The clause's own children are either
    [qualified_name|name] for a plain `use Foo\Bar;`, or
    [name, "as", name(field="alias")] for an aliased `use Foo\Bar as B;" --
    either way the *first* qualified_name/name child is always the real
    path (the alias, when present, is always the second one), so taking
    the first match and returning immediately already skips the alias with
    no need to check its field name.

    "\\" is PHP's namespace separator, normalized to "." throughout so the
    result matches every other handler's dotted-path convention --
    resolve_dependency()'s existing split-on-"."-take-last-segment fallback
    then applies unchanged, the same normalization C#'s "::"-free
    qualified_name already gets for free and Rust's "::" needs explicitly.
    """
    for child in clause.children:
        if child.type in ("qualified_name", "name"):
            path = child.text.decode()
            if prefix:
                path = f"{prefix}\\{path}"
            results.append(path.replace("\\", "."))
            return


def _php_dependency_handler(node: Node, results: list) -> bool:
    """Two distinct dependency shapes in PHP, both handled here.

    `use Foo\Bar;` / `use Foo\Bar as B;` / a grouped `use Foo\{Bar, Baz as
    B};` all parse as one namespace_use_declaration node type, but with two
    different internal shapes: a plain or aliased use has its
    namespace_use_clause as a direct child (or several, comma-separated,
    for `use Foo\A, Foo\B;`); a grouped use instead has a namespace_name
    prefix sibling plus a namespace_use_group (field "body") wrapping one
    namespace_use_clause per sibling -- each needs the prefix re-attached
    since the group's own clauses only carry their own suffix
    ("Bar"/"Baz", not "Foo\Bar"/"Foo\Baz"). Handled by walking node's
    direct children once, tracking any namespace_name prefix seen along
    the way and dispatching each namespace_use_clause (direct or nested
    inside the group) through _append_php_use_clause with the right
    prefix.

    `require`/`require_once`/`include`/`include_once` are their own
    dedicated *_expression node types (not ordinary calls the way Lua's
    require() or Ruby's require/require_relative are) with a literal
    string argument that's an actual relative file *path*, not a dotted
    module name -- normalized to its bare stem (Path(path).stem) the same
    way GDScript's preload()/load() and C++'s #include are, so
    resolve_dependency()'s exact-stem-match branch is what actually
    resolves it rather than its dotted-path fallback. A non-literal
    argument (a variable, string concatenation) is left uncaptured, the
    same "only capture what's statically resolvable" restraint every other
    handler here already takes for its own language's dynamically
    computed import path.
    """
    if node.type == "namespace_use_declaration":
        prefix = None
        for child in node.children:
            if child.type == "namespace_name":
                prefix = child.text.decode()
            elif child.type == "namespace_use_clause":
                _append_php_use_clause(child, None, results)
            elif child.type == "namespace_use_group":
                for clause in child.children:
                    if clause.type == "namespace_use_clause":
                        _append_php_use_clause(clause, prefix, results)
        return True

    if node.type in ("require_once_expression", "require_expression",
                      "include_once_expression", "include_expression"):
        for child in node.children:
            if child.type == "string":
                for grandchild in child.children:
                    if grandchild.type == "string_content":
                        results.append(Path(grandchild.text.decode()).stem)
                        break
        return True

    return False


_RUBY_REQUIRE_CALLS = {"require", "require_relative"}


def _ruby_dependency_handler(node: Node, results: list) -> bool:
    """`require "json"` / `require_relative "helpers/formatter"` are
    ordinary method calls (a `call` node with no receiver), not a
    dedicated import-statement node -- the same shape Lua's require() is,
    matched by callee name rather than a distinct node type. Ruby's grammar
    names the callee field "method" (not "name" the way Lua's
    function_call does), and a call *with* a receiver (`Foo.require(...)`,
    vanishingly unlikely to be a real dependency but still a different
    call shape) is explicitly excluded rather than matched.

    A require_relative path is a real relative file path with the ".rb"
    extension conventionally omitted ("helpers/formatter", not
    "helpers/formatter.rb"); a plain require's argument is usually a
    gem/stdlib name instead ("json"), external either way. Both are
    normalized to their bare Path stem the same restrained way GDScript's
    preload()/load() and C++'s #include already are -- harmless for a
    slash-free gem name (its stem is itself), and what actually lets a
    relative path's *directory* component be dropped so
    resolve_dependency()'s exact-stem-match branch can find the file by
    name alone, the same reasoning C++'s #include normalization spells out
    for why a literal include path can't be trusted to match the
    project's own collected relative-key path.
    """
    if node.type != "call":
        return False
    if node.child_by_field_name("receiver") is not None:
        return False
    method = node.child_by_field_name("method")
    if method is None or method.text.decode() not in _RUBY_REQUIRE_CALLS:
        return False
    args = node.child_by_field_name("arguments")
    if args is not None:
        for child in args.children:
            if child.type == "string":
                for grandchild in child.children:
                    if grandchild.type == "string_content":
                        results.append(Path(grandchild.text.decode()).stem)
                        break
    return True


_BASH_SOURCE_COMMANDS = {"source", "."}


def _bash_dependency_handler(node: Node, results: list) -> bool:
    """`source file.sh` / `. file.sh` are ordinary commands, not a
    dedicated import-statement node -- the same shape Lua's require()/
    Ruby's require() are, matched by command name rather than a distinct
    node type. This grammar's "name" field on a `command` node is the
    command_name sub-node (its own raw text is the command word itself,
    e.g. "source" or "."), and "argument" is always just the *first*
    argument regardless of how many follow -- confirmed directly against
    the real grammar (`. lib/other.sh extra_arg1 extra_arg2` still yields
    exactly one "argument" child), which is exactly what's wanted here:
    a `source`d script's own extra positional args (passed through as
    $1/$2 inside it) aren't part of the sourced path.

    Only a literal word or quoted string counts as a resolvable path -- a
    dynamically computed one (a variable, command substitution) has no
    literal text worth capturing, the same "only capture what's
    statically resolvable" restraint every other handler here already
    takes for its own language's dynamic import shape.

    Normalized to the bare stem (Path(path).stem), the same restrained
    normalization GDScript's preload()/C++'s #include/PHP's require/
    Ruby's require_relative already apply for their own relative
    file-path dependencies -- a sourced script's path is relative to
    wherever it's actually invoked from, which won't generally match the
    project's own collected relative-key path exactly.
    """
    if node.type != "command":
        return False
    command_name = node.child_by_field_name("name")
    if command_name is None or command_name.text.decode() not in _BASH_SOURCE_COMMANDS:
        return False
    argument = node.child_by_field_name("argument")
    if argument is not None:
        if argument.type == "word":
            results.append(Path(argument.text.decode()).stem)
        elif argument.type == "string":
            for child in argument.children:
                if child.type == "string_content":
                    results.append(Path(child.text.decode()).stem)
                    break
    return True


@dataclass(frozen=True)
class LanguageConfig:
    language: Language
    function_types: list[str]              # node types targeted for signature extraction + body compression
    dependency_handler: DependencyHandler   # strategy for extracting import statements
    api_handler: ApiHandler | None = None   # strategy for extracting REST-route declarations, if this language has one
    field_handler: FieldHandler | None = None  # strategy for extracting call-initialized class-level fields (see FieldHandler's own comment)
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
    # Prefix to prepend to a function_types node's *already-found* name --
    # unlike implicit_names above (a substitute for a missing name), this
    # only ever applies on top of a real one. C#'s destructor_declaration is
    # the motivating case: its "name" field is the bare identifier
    # ("Widget"), with the "~" that actually distinguishes it from a
    # same-named constructor tokenized as a separate, unnamed sibling child
    # rather than being part of the field -- without this, a class defining
    # both would produce two identical "Widget()" signatures.
    name_prefixes: dict[str, str] = field(default_factory=dict)
    # function_types node types whose grammar omits the "parameters" field
    # entirely for a real zero-argument function/method (unlike most
    # grammars here, which always emit an empty parameter-list node even
    # for zero args) -- Ruby's `method`/`singleton_method` for a bare `def
    # foo` / `def self.foo` with no parens and no params at all. Deliberately
    # opt-in per node type rather than a blanket "missing params means zero
    # args" default: TS/JS's bare single-identifier arrow (`x => x * 2`)
    # also has no "parameters" field, but DOES take one argument Ziplex
    # just doesn't capture -- defaulting that case to "()" would misreport
    # its arity, which is worse than the documented no-signature gap it has
    # today. A node.type listed here is instead a case where "no parameters
    # field" is structurally guaranteed to mean real zero arity, not an
    # unresolved one.
    zero_arg_types: frozenset[str] = frozenset()


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

# `.sh`/`.bash` share this exact config object, same as .cpp/.cc/.cxx
# above -- both extensions parse with the identical grammar, no
# per-extension difference to justify separate instances.
#
# function_definition covers both of Bash's two equivalent function-
# declaration forms uniformly (`function deploy() { ... }` and the
# POSIX-compatible `deploy() { ... }`) -- both parse as the same node
# type, with "name"/"body" as direct fields (the same convention
# Python/TS/GDScript/PHP/Ruby already use). Genuinely new here: a shell
# function's `()` is fixed syntax, never an actual parameter list
# (arguments are read dynamically via $1/$2/$@ inside the body, not
# declared) -- this grammar therefore has NO "parameters" field at all,
# for any function, ever. zero_arg_types opts function_definition into
# the empty-"()" substitution the same way Ruby's parens-less `def foo`
# does, except here it's true unconditionally rather than only for a
# specific parens-omitted spelling.
_sh_config = LanguageConfig(
    language=Language(tsbash.language()),
    function_types=["function_definition"],
    dependency_handler=_bash_dependency_handler,
    zero_arg_types=frozenset({"function_definition"}),
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
        field_handler=_java_field_handler,
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
    ".cs": LanguageConfig(
        language=Language(tscsharp.language()),
        # method_declaration/constructor_declaration/destructor_declaration
        # all expose "name"/"parameters"/"body" as direct fields, same
        # shape as Java/Go -- no declarator indirection to unwrap.
        # constructor/destructor have no return type at all, correctly
        # producing no "-> ..." suffix (same as every other language's
        # constructor/destructor handling). An interface's own method
        # declaration (`string Greet();`, no body) is the *same*
        # method_declaration node type as a class method, just missing a
        # "body" field -- handled for free the same way Rust's
        # function_signature_item is: still produces a real signature,
        # and the missing body is harmless for compression.
        #
        # method_declaration's return-type field is named "returns" --
        # yet a fourth field-naming convention this grammar happens to
        # use, alongside Python/TS/GDScript's "return_type", Go's
        # "result", and C++'s "type" -- extractor.py's
        # _traverse_signatures() now checks all four generically, not a
        # per-language hardcode.
        function_types=["method_declaration", "constructor_declaration", "destructor_declaration"],
        dependency_handler=_csharp_dependency_handler,
        # See LanguageConfig.name_prefixes' own docstring: destructor_declaration's
        # "name" field is the bare identifier with no "~", which would
        # otherwise render identically to the class's own constructor.
        name_prefixes={"destructor_declaration": "~"},
    ),
    ".php": LanguageConfig(
        # language_php (not language_php_only) -- this grammar ships two
        # variants, and language_php is the one that actually parses a real
        # .php file's usual HTML-plus-<?php-tags shape; language_php_only
        # is for an already-isolated PHP fragment with no surrounding
        # markup, not what a collected project file looks like.
        language=Language(tsphp.language_php()),
        # function_definition (a free function) and method_declaration (a
        # class/interface/trait method) both expose "name"/"parameters"/
        # "return_type"/"body" as direct fields -- the same convention
        # Python/TS/GDScript already use, so no extractor.py changes were
        # needed at all. An interface's own method_declaration (no body,
        # just a signature + ";") is handled for free the same way Rust's
        # function_signature_item/C#'s interface method already are.
        # Deliberately excludes anonymous_function/arrow_function
        # (PHP's own closure and `fn() => ...` short-closure syntax) --
        # neither has a "name" field, and the wrapping assignment_expression
        # names its own target under a "left" field, not "name"/"key" the
        # way extract_signatures()'s existing wrapper-fallback checks for
        # TS/JS's own anonymous functions -- left as a documented, deferred
        # gap rather than broadening that fallback for one language's sake,
        # the same restraint TS/JS's own bare single-param arrow gap takes.
        function_types=["function_definition", "method_declaration"],
        dependency_handler=_php_dependency_handler,
    ),
    ".rb": LanguageConfig(
        language=Language(tsruby.language()),
        # method (a plain def, at module level or inside a class -- Ruby's
        # grammar doesn't distinguish the two the way Java/C# separate a
        # method from a free function) and singleton_method (`def
        # self.foo`/`def SomeClass.foo`, a "class method") both expose
        # "name"/"parameters"/"body" as direct fields -- no return_type
        # field at all (vanilla Ruby has no return-type syntax), which
        # extract_signatures() already handles for free (no "-> ..."
        # suffix when none of its four known return-type field names are
        # present). Neither node type's own "name" distinguishes a
        # singleton_method's receiver ("self" vs. a named class/module) --
        # deliberately left unprefixed, the same restraint Go's own
        # method_declaration takes for its receiver type.
        function_types=["method", "singleton_method"],
        dependency_handler=_ruby_dependency_handler,
        # Idiomatic Ruby very commonly omits parens for a zero-argument
        # method (`def initialize` / `def self.create`) -- unlike every
        # other language configured above, this grammar then has no
        # "parameters" field at all rather than an empty parameter-list
        # node, so without this a large, ordinary fraction of real Ruby
        # methods would silently produce no signature at all. See
        # LanguageConfig.zero_arg_types' own docstring for why this is
        # safe here specifically (real zero arity, not an unresolved one).
        zero_arg_types=frozenset({"method", "singleton_method"}),
    ),
    # See _sh_config's own definition above for what .sh/.bash share and
    # why they're both one config object.
    ".sh": _sh_config,
    ".bash": _sh_config,
}


def get_language_config(ext: str) -> LanguageConfig | None:
    return LANGUAGE_CONFIGS.get(ext)
