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
    """
    own_name = node.child_by_field_name("name")
    if own_name:
        return own_name.text.decode()
    if parent is not None:
        wrapper_name = parent.child_by_field_name("name") or parent.child_by_field_name("key")
        if wrapper_name:
            return wrapper_name.text.decode()
    return implicit_names.get(node.type)


def _traverse_signatures(node, results: list, node_types: list, implicit_names: dict, parent):
    if node.type in node_types:
        name   = _resolve_signature_name(node, parent, implicit_names)
        params = node.child_by_field_name("parameters")
        # "return_type" covers Python/TS/GDScript's own field name for this;
        # Go's grammar names the equivalent field "result" instead -- not a
        # per-language hardcode, just a second known field-naming
        # convention, the same restraint _resolve_signature_name() already
        # takes for "name" vs. "key".
        ret    = node.child_by_field_name("return_type") or node.child_by_field_name("result")

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