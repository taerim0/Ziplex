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

    tree = parser.parse(bytes(code, "utf8"))
    results = []
    _traverse_signatures(tree.root_node, results, node_types, None)
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

    tree = parser.parse(bytes(code, "utf8"))
    results = []
    _traverse_api(tree.root_node, results)
    return results


def _resolve_signature_name(node, parent):
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
    """
    own_name = node.child_by_field_name("name")
    if own_name:
        return own_name.text.decode()
    if parent is not None:
        wrapper_name = parent.child_by_field_name("name") or parent.child_by_field_name("key")
        if wrapper_name:
            return wrapper_name.text.decode()
    return None


def _traverse_signatures(node, results: list, node_types: list, parent):
    if node.type in node_types:
        name   = _resolve_signature_name(node, parent)
        params = node.child_by_field_name("parameters")
        ret    = node.child_by_field_name("return_type")

        if name and params:
            sig = f"{name}{params.text.decode()}"
            if ret:
                sig += f" -> {ret.text.decode()}"
            results.append(sig)
        return

    for child in node.children:
        _traverse_signatures(child, results, node_types, node)


def _traverse_dependencies(node, results: list, handler):
    if handler(node, results):
        return

    for child in node.children:
        _traverse_dependencies(child, results, handler)


def _traverse_api(node, results: list):
    if node.type == "decorated_definition":
        decorator = None
        for child in node.children:
            if child.type == "decorator":
                decorator = child
                break

        if decorator:
            method = None
            path = None

            for n in _walk(decorator):
                if n.type == "attribute":
                    attr_text = n.text.decode()
                    if ".get"      in attr_text: method = "GET"
                    elif ".post"   in attr_text: method = "POST"
                    elif ".put"    in attr_text: method = "PUT"
                    elif ".delete" in attr_text: method = "DELETE"
                    elif ".patch"  in attr_text: method = "PATCH"
                    break

            for n in _walk(decorator):
                if n.type == "string_content":
                    path = n.text.decode()
                    break

            if method and path:
                results.append(f"{method} {path}")
        return

    for child in node.children:
        _traverse_api(child, results)


def _walk(node):
    yield node
    for child in node.children:
        yield from _walk(child)


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