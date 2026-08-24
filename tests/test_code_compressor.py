from ziplex.extract.code.compressor import compress_code, MARKER


def test_python_function_body_is_stripped():
    code = "def add(a, b):\n    total = a + b\n    return total\n"
    result = compress_code(code, ".py")

    assert "def add(a, b):" in result
    assert MARKER.strip() in result
    assert "total = a + b" not in result
    assert "return total" not in result


def test_java_method_body_is_stripped_but_brace_lines_kept():
    code = (
        "class Foo {\n"
        "    int add(int a, int b) {\n"
        "        int total = a + b;\n"
        "        return total;\n"
        "    }\n"
        "}\n"
    )
    result = compress_code(code, ".java")

    assert "int add(int a, int b) {" in result
    assert "total = a + b" not in result
    # the closing brace of the method is kept, not swallowed by the marker
    assert "}" in result


def test_lua_function_body_is_stripped_but_end_kept():
    code = (
        "local function add(a, b)\n"
        "    local total = a + b\n"
        "    return total\n"
        "end\n"
    )
    result = compress_code(code, ".lua")

    assert "local function add(a, b)" in result
    assert MARKER.strip() in result
    assert "total = a + b" not in result
    # Lua's `end` isn't part of the body node at all (unlike a brace
    # language's `}`), so there's nothing to specially exclude -- it should
    # just survive untouched as its own line.
    assert "end" in result


def test_gdscript_function_body_is_stripped():
    code = (
        "func take_damage(amount: int) -> void:\n"
        "    var total = health - amount\n"
        "    health = total\n"
    )
    result = compress_code(code, ".gd")

    assert "func take_damage(amount: int) -> void:" in result
    assert MARKER.strip() in result
    assert "total = health - amount" not in result


def test_gdscript_constructor_body_is_stripped():
    # _init() parses as its own constructor_definition node type, distinct
    # from function_definition -- without both types in GDScript's
    # function_types, this would ship uncompressed while every sibling
    # method got body-stripped.
    code = (
        "func _init(x, y):\n"
        "    var total = x + y\n"
        "    print(total)\n"
    )
    result = compress_code(code, ".gd")

    assert "func _init(x, y):" in result
    assert MARKER.strip() in result
    assert "total = x + y" not in result


def test_typescript_arrow_function_body_is_stripped():
    # arrow_function wasn't in .ts's function_types at all before this fix --
    # a block-bodied arrow shipped fully uncompressed, unlike an equivalent
    # function_declaration/method_definition.
    code = "const add = (a, b) => {\n    return a + b;\n};\n"
    result = compress_code(code, ".ts")

    assert "const add = (a, b) => {" in result
    assert MARKER.strip() in result
    assert "return a + b" not in result
    assert "}" in result


def test_typescript_concise_arrow_body_is_left_alone():
    # `x => x * 2` -- a single-line expression body with no braces. The
    # existing "body starts on the same line as the signature" check already
    # covers this for free: stripping it would blank the whole statement,
    # not just an implementation detail, so nothing should be removed.
    code = "const double = x => x * 2;\n"
    result = compress_code(code, ".ts")

    assert result == code.rstrip("\n")


def test_go_function_and_method_bodies_are_stripped():
    # A brace language like Java/JS -- the opening "{" sits on the same
    # line as the signature (both function_declaration and, for a receiver
    # method, method_declaration), and the closing "}" survives.
    code = (
        "func Add(a int, b int) int {\n"
        "    total := a + b\n"
        "    return total\n"
        "}\n\n"
        "func (s *Server) Start() error {\n"
        "    return nil\n"
        "}\n"
    )
    result = compress_code(code, ".go")

    assert "func Add(a int, b int) int {" in result
    assert "func (s *Server) Start() error {" in result
    assert MARKER.strip() in result
    assert "total := a + b" not in result
    assert result.count("}") == 2


def test_cpp_function_and_method_bodies_are_stripped():
    # A brace language -- covers a free function, an in-class method, and
    # an out-of-class Class::method definition all via the one
    # function_definition node type this grammar uses uniformly.
    code = (
        "int Add(int a, int b) {\n"
        "    return a + b;\n"
        "}\n\n"
        "class Server {\n"
        "public:\n"
        "    void Start() {\n"
        "        run();\n"
        "    }\n"
        "};\n\n"
        "void Server::Stop() {\n"
        "    halt();\n"
        "}\n"
    )
    result = compress_code(code, ".cpp")

    assert "int Add(int a, int b) {" in result
    assert "void Start() {" in result
    assert "void Server::Stop() {" in result
    assert MARKER.strip() in result
    assert "run();" not in result
    assert "halt();" not in result
    # class body itself (field declarations, access specifiers) survives --
    # only function_definition bodies get stripped
    assert "public:" in result
    assert "};" in result


def test_rust_function_and_method_bodies_are_stripped():
    # A brace language -- covers a free function and an impl method via
    # the one function_item node type this grammar uses uniformly.
    code = (
        "fn add(a: i32, b: i32) -> i32 {\n"
        "    a + b\n"
        "}\n\n"
        "impl Server {\n"
        "    pub fn start(&mut self) {\n"
        "        run();\n"
        "    }\n"
        "}\n"
    )
    result = compress_code(code, ".rs")

    assert "fn add(a: i32, b: i32) -> i32 {" in result
    assert "pub fn start(&mut self) {" in result
    assert MARKER.strip() in result
    assert "run();" not in result
    assert "impl Server {" in result


def test_rust_trait_method_with_no_body_is_left_alone():
    # function_signature_item (a trait's own method declaration) has no
    # "body" field at all -- _collect_bodies' `if body:` guard must find
    # nothing to strip here, leaving the one-line declaration untouched.
    code = "trait Greet {\n    fn greet(&self) -> String;\n}\n"
    result = compress_code(code, ".rs")

    assert result == code.rstrip("\n")


def test_csharp_method_and_constructor_bodies_are_stripped():
    # A brace language -- covers a constructor and a regular method via
    # constructor_declaration/method_declaration.
    code = (
        "public class Server\n"
        "{\n"
        "    public Server(string name)\n"
        "    {\n"
        "        this.name = name;\n"
        "    }\n\n"
        "    public void Start()\n"
        "    {\n"
        "        Run();\n"
        "    }\n"
        "}\n"
    )
    result = compress_code(code, ".cs")

    assert "public Server(string name)" in result
    assert "public void Start()" in result
    assert MARKER.strip() in result
    assert "this.name = name;" not in result
    assert "Run();" not in result
    assert "public class Server" in result


def test_csharp_interface_method_with_no_body_is_left_alone():
    # An interface's own method_declaration (no body, just a signature +
    # ";") -- _collect_bodies' `if body:` guard must find nothing to
    # strip here, leaving the one-line declaration untouched.
    code = "public interface IGreet\n{\n    string Greet();\n}\n"
    result = compress_code(code, ".cs")

    assert result == code.rstrip("\n")


def test_php_function_and_method_bodies_are_stripped():
    # A brace language -- covers a free function and a class method via
    # the two function_types node types this grammar uses.
    code = (
        "<?php\n\n"
        "class Server\n"
        "{\n"
        "    public function start(): void\n"
        "    {\n"
        "        run();\n"
        "    }\n"
        "}\n\n"
        "function add($a, $b)\n"
        "{\n"
        "    return $a + $b;\n"
        "}\n"
    )
    result = compress_code(code, ".php")

    assert "public function start(): void" in result
    assert "function add($a, $b)" in result
    assert MARKER.strip() in result
    assert "run();" not in result
    assert "return $a + $b;" not in result
    assert "class Server" in result


def test_php_interface_method_with_no_body_is_left_alone():
    # An interface's own method_declaration (no body, just a signature +
    # ";") -- _collect_bodies' `if body:` guard must find nothing to
    # strip here, leaving the one-line declaration untouched.
    code = "<?php\n\ninterface Greetable {\n    public function greet(): string;\n}\n"
    result = compress_code(code, ".php")

    assert result == code.rstrip("\n")


def test_php_one_liner_body_is_left_alone():
    # A body that opens and closes on the same line as the signature
    # (constructor-property-promotion's common `{}` shape, or any other
    # brace-language one-liner) has nothing meaningful to strip -- the
    # same-line guard must leave it untouched rather than blanking the
    # signature's own line.
    code = "<?php\n\nclass Foo {\n    public function __construct(private int $x) {}\n}\n"
    result = compress_code(code, ".php")

    assert result == code.rstrip("\n")


def test_ruby_method_body_is_stripped_but_end_kept():
    # Ruby's "end" isn't part of the body node at all (unlike a brace
    # language's "}") -- a sibling of the body, not a child of it -- so
    # there's nothing to specially exclude, the same shape Lua's own "end"
    # already has.
    code = "def add(a, b)\n  total = a + b\n  total\nend\n"
    result = compress_code(code, ".rb")

    assert "def add(a, b)" in result
    assert MARKER.strip() in result
    assert "total = a + b" not in result
    assert "end" in result


def test_ruby_method_with_no_parens_body_is_stripped():
    # A parens-less zero-arg method -- has no "parameters" field at all,
    # but still has a real multi-line "body" field, so compression works
    # exactly the same as every other method here regardless of
    # zero_arg_types (a signature-extraction-only concern).
    code = "def get_name\n  format(@first, @last)\nend\n"
    result = compress_code(code, ".rb")

    assert "def get_name" in result
    assert MARKER.strip() in result
    assert "format(@first, @last)" not in result
    assert "end" in result


def test_ruby_endless_method_is_left_alone():
    # Ruby 3.0+'s `def name(...) = expr` form has no "end" at all and its
    # body sits on the same line as the signature -- the existing
    # same-line guard already leaves a one-liner like this untouched, no
    # special-casing needed.
    code = "def square(x) = x * x\n"
    result = compress_code(code, ".rb")

    assert result == code.rstrip("\n")


def test_unsupported_extension_returns_none():
    assert compress_code("whatever content", ".xyz") is None


def test_function_with_no_body_content_is_left_alone():
    # a one-line function (body and signature on the same line has nothing to
    # elide) shouldn't produce a dangling marker
    code = "def noop(): pass\n"
    result = compress_code(code, ".py")
    assert "def noop(): pass" in result
