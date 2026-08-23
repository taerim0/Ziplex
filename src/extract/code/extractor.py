from extract.code.languages import get_language_config
from extract.code.parser import get_parser
from file.textutil import read_text
from pathlib import Path

def extract_signatures(file_path: str) -> list[str]:
    parser = get_parser(file_path)
    if not parser:
        return []

    code = read_text(file_path)
    if code is None:
        return []

    config = get_language_config(Path(file_path).suffix)
    node_types = config.function_types if config else []
    implicit_names = config.implicit_names if config else {}

    tree = parser.parse(bytes(code, "utf8"))
    results = []
    _traverse_signatures(tree.root_node, results, node_types, implicit_names, None)
    return results


def extract_dependencies(file_path: str) -> list[str]:
    parser = get_parser(file_path)
    if not parser:
        return []

    code = read_text(file_path)
    if code is None:
        return []

    config = get_language_config(Path(file_path).suffix)
    if not config:
        return []

    tree = parser.parse(bytes(code, "utf8"))
    results = []
    _traverse_dependencies(tree.root_node, results, config.dependency_handler)
    return results


def extract_api(file_path: str) -> list[str]:
    parser = get_parser(file_path)
    if not parser:
        return []

    code = read_text(file_path)
    if code is None:
        return []

    config = get_language_config(Path(file_path).suffix)
    handler = config.api_handler if config else None
    if handler is None:
        # No established web-routing convention for this language
        # (Java/Lua/GDScript as of this writing) -- same "nothing to look
        # for" short-circuit extract_dependencies() takes for a language
        # with no config at all, just one level narrower (a config can
        # exist, with no api_handler specifically).
        return []

    tree = parser.parse(bytes(code, "utf8"))
    results = []
    _traverse_api(tree.root_node, results, handler)
    return results


def _resolve_signature_name(node, parent, implicit_names):
    """A matched function-type node's own "name" field, when it has one
    (function_declaration, method_definition, Lua's/GDScript's function
    nodes -- every language where a function names itself directly).

    Falls back to the *parent* node's "name" field, then its "key" field,
    when the matched node has neither -- the shape an anonymous
    arrow_function/function_expression takes once it's assigned somewhere:
    `const add = (a, b) => ...` (parent is a variable_declarator, "name"
    field is the identifier), `class C { method = () => ... }` (parent is
    a public_field_definition, also "name"), and `{ greet: () => ... }`
    (parent is a pair -- object-literal properties use "key", not "name").
    Not a per-language hardcode despite being motivated by TS/JS: any
    grammar with an equivalent assignment-shaped wrapper around an
    otherwise-anonymous function resolves the same way.

    Falls back once more to implicit_names (from the language's own
    LanguageConfig) keyed by node.type, for a node whose grammar gives it
    neither of the above -- GDScript's constructor_definition, whose only
    "name" is the fixed keyword token "_init" itself, not an identifier
    under a field.

    The "declarator" fallback on the own-node check is for C-family
    grammars (C++): called here with an already-unwrapped function_
    declarator node (see _unwrap_declarator()), whose own identifier --
    plain identifier, field_identifier for an in-class method,
    qualified_identifier for an out-of-class `Server::Stop`,
    destructor_name for `~Server` -- sits under a field called
    "declarator" too, not "name". Harmless for every other language,
    which has no field by that name at all on a function-type node.
    """
    own_name = node.child_by_field_name("name") or node.child_by_field_name("declarator")
    if own_name:
        return own_name.text.decode()
    if parent is not None:
        wrapper_name = parent.child_by_field_name("name") or parent.child_by_field_name("key")
        if wrapper_name:
            return wrapper_name.text.decode()
    return implicit_names.get(node.type)


def _unwrap_declarator(node):
    """C-family grammars (C++) separate a function's actual identifier and
    parameter list into a distinct "declarator" sub-node from the
    definition node itself, which instead carries the return type and
    body directly -- the same "declarator" concept those grammars reuse
    for variable declarations too, so a function's name/parameters aren't
    direct fields of function_definition the way they are for every other
    language here. If `node` has a function_declarator under its own
    "declarator" field, that inner node is what actually carries
    "parameters" directly and the identifier one level further in (under
    its own "declarator" field -- see _resolve_signature_name()) -- return
    that instead so name/parameter resolution both read from the right
    place. Every other currently-supported language has no such
    indirection (name/parameters/body all sit directly on the matched
    node), so this returns `node` unchanged for them.
    """
    declarator = node.child_by_field_name("declarator")
    if declarator is not None and declarator.type == "function_declarator":
        return declarator
    return node


def _traverse_signatures(node, results: list, node_types: list, implicit_names: dict, parent):
    if node.type in node_types:
        sig_node = _unwrap_declarator(node)
        name   = _resolve_signature_name(sig_node, parent, implicit_names)
        params = sig_node.child_by_field_name("parameters")
        # "return_type" covers Python/TS/GDScript's own field name for this;
        # Go's grammar names the equivalent field "result" instead. Read
        # off `node` (not `sig_node`): the return type sits on the outer
        # definition node even for C++, never on the inner declarator.
        ret = node.child_by_field_name("return_type") or node.child_by_field_name("result")
        if sig_node is not node:
            # "type" is C++'s own name for this field -- but it is NOT
            # checked unconditionally the way "return_type"/"result" are:
            # Java's method_declaration also has a field literally named
            # "type" (its return type), and Java's node is never unwrapped
            # (it has no "declarator" field, so _unwrap_declarator() always
            # returns it unchanged) -- checking "type" unconditionally
            # silently changed every Java signature from "add(int a, int
            # b)" to "add(int a, int b) -> int", an unrelated, undocumented
            # behavior change caught by code review before merge. Gating
            # this on sig_node being an actually-unwrapped declarator
            # (currently only ever true for C++'s function_declarator)
            # scopes "type" to the grammar that actually needs it.
            ret = ret or node.child_by_field_name("type")

        if name and params:
            sig = f"{name}{params.text.decode()}"
            if ret:
                sig += f" -> {ret.text.decode()}"
            results.append(sig)
        return

    for child in node.children:
        _traverse_signatures(child, results, node_types, implicit_names, node)


def _traverse_dependencies(node, results: list, handler):
    if handler(node, results):
        return

    for child in node.children:
        _traverse_dependencies(child, results, handler)


def _traverse_api(node, results: list, handler):
    if handler(node, results):
        return

    for child in node.children:
        _traverse_api(child, results, handler)


def debug_tree(file_path: str):
    parser = get_parser(file_path)
    if not parser:
        print(f"⚠️  지원하지 않는 파일 형식입니다: {file_path}")
        return

    code = read_text(file_path)
    if code is None:
        print(f"⚠️  텍스트로 읽을 수 없는 파일입니다: {file_path}")
        return

    tree = parser.parse(bytes(code, "utf8"))
    _print_tree(tree.root_node, 0)


def _print_tree(node, depth: int):
    indent = "  " * depth
    print(f"{indent}{node.type}: {repr(node.text.decode()[:30])}")
    for child in node.children:
        _print_tree(child, depth + 1)