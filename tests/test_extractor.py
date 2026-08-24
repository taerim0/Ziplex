from ziplex.extract.code.extractor import extract_signatures, extract_dependencies, extract_api


def test_extract_signatures_from_python_file(tmp_path):
    file_path = tmp_path / "mod.py"
    file_path.write_text(
        "def add(a, b):\n    return a + b\n\n\ndef sub(a, b):\n    return a - b\n",
        encoding="utf-8",
    )

    sigs = extract_signatures(str(file_path))
    assert "add(a, b)" in sigs
    assert "sub(a, b)" in sigs


def test_extract_signatures_from_java_file(tmp_path):
    # Java's method_declaration names its return-type field "type" -- the
    # same field name C++'s function_definition happens to use too. A
    # naive unconditional "type" fallback (added generically for C++)
    # would silently start appending " -> int" to every Java signature,
    # since Java's node is never run through the C-family declarator
    # unwrap -- caught by code review before merge, guarded here so it
    # can't silently regress again.
    file_path = tmp_path / "Mod.java"
    file_path.write_text(
        "public class Mod {\n"
        "    public int add(int a, int b) {\n"
        "        return a + b;\n"
        "    }\n"
        "}\n",
        encoding="utf-8",
    )

    sigs = extract_signatures(str(file_path))
    assert "add(int a, int b)" in sigs
    assert not any("->" in s for s in sigs)


def test_extract_dependencies_from_python_file(tmp_path):
    # Not stdlib names -- see test_extract_dependencies_excludes_stdlib_
    # imports() below for the "os"/"pathlib" case this used to (wrongly)
    # assert on.
    file_path = tmp_path / "mod.py"
    file_path.write_text("import requests\nfrom mypackage.utils import helper\n\nx = 1\n", encoding="utf-8")

    deps = extract_dependencies(str(file_path))
    assert "requests" in deps
    assert "mypackage.utils" in deps


def test_extract_dependencies_excludes_stdlib_imports(tmp_path):
    # A plain `import json` used to resolve as an internal dependency on
    # any local project file that happened to share that stem (a real bug,
    # not hypothetical: found packing Ziplex's own repo, where dozens of
    # files' `import json` all wrongly resolved onto
    # extract/text/json.py) -- resolve_dependency()'s bare-stem fallback
    # can't tell "a genuine local file reference" apart from "an absolute
    # import of a same-named standard-library module" once both have been
    # reduced to the same bare string. sys.stdlib_module_names filters
    # these out before they're ever appended as a raw dependency string,
    # for both the bare (import_statement) and from-import
    # (import_from_statement) shapes, and for a multi-segment dotted
    # absolute import too (checked by its *first* segment, "os" in
    # "os.path" -- resolve_dependency()'s own fallback would otherwise
    # re-split a survivor on "." and try to match "path"/"ElementTree").
    file_path = tmp_path / "mod.py"
    file_path.write_text(
        "import json\n"
        "import os\n"
        "import xml.etree.ElementTree\n"
        "from pathlib import Path\n"
        "from os.path import join\n"
        "import requests\n",
        encoding="utf-8",
    )

    deps = extract_dependencies(str(file_path))
    assert "json" not in deps
    assert "os" not in deps
    assert "xml.etree.ElementTree" not in deps
    assert "pathlib" not in deps
    assert "os.path" not in deps
    assert "requests" in deps  # not stdlib -- kept


def test_extract_dependencies_keeps_relative_imports_named_like_stdlib(tmp_path):
    # A relative import's module text always starts with "." -- its first
    # dotted segment (the text before that leading dot) is an empty
    # string, which never matches a real stdlib module name, so this is
    # never filtered regardless of what the referenced module is actually
    # named. Ziplex's own extract/text/registry.py has exactly this shape
    # for real (a relative import of its own json.py compressor module),
    # which is what motivated checking this case explicitly rather than
    # assuming it.
    file_path = tmp_path / "mod.py"
    file_path.write_text("from .json import compress_json\n", encoding="utf-8")

    assert ".json" in extract_dependencies(str(file_path))


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


def test_extract_signatures_from_go_file(tmp_path):
    file_path = tmp_path / "main.go"
    file_path.write_text(
        "package main\n\n"
        "func Add(a int, b int) int {\n    return a + b\n}\n\n"
        "func (s *Server) Start() error {\n    return nil\n}\n\n"
        "func NoReturn() {\n}\n",
        encoding="utf-8",
    )

    sigs = extract_signatures(str(file_path))
    assert "Add(a int, b int) -> int" in sigs
    # method_declaration -- a receiver method, not a plain function; its
    # "receiver" field ((s *Server)) is simply never read, only "name"/
    # "parameters"/"result"/"body".
    assert "Start() -> error" in sigs
    # no "result" field at all when a function has no return value --
    # must not append a dangling " -> " with nothing after it.
    assert "NoReturn()" in sigs


def test_extract_dependencies_from_go_file(tmp_path):
    # Covers all four real import shapes: single, grouped, aliased, blank
    # (`_`) -- import_spec is the common unit underneath every one of them.
    file_path = tmp_path / "main.go"
    file_path.write_text(
        'package main\n\n'
        'import "fmt"\n'
        'import (\n'
        '\t"strings"\n'
        '\tmyalias "path/filepath"\n'
        '\t_ "encoding/json"\n'
        ')\n',
        encoding="utf-8",
    )

    deps = extract_dependencies(str(file_path))
    assert "fmt" in deps
    assert "strings" in deps
    assert "path/filepath" in deps
    assert "encoding/json" in deps


def test_extract_signatures_from_cpp_file(tmp_path):
    # C++'s grammar nests a function's actual name+parameters one level
    # inside a "declarator" sub-node (function_declarator), rather than
    # exposing them directly on function_definition the way every other
    # supported language does -- extractor.py's _unwrap_declarator()
    # handles this. function_definition covers every shape uniformly: a
    # free function, an in-class method, and an out-of-class
    # `Class::method` definition.
    file_path = tmp_path / "server.cpp"
    file_path.write_text(
        "int Add(int a, int b) {\n    return a + b;\n}\n\n"
        "class Server {\npublic:\n    void Start() {}\n};\n\n"
        "void Server::Stop() {\n}\n",
        encoding="utf-8",
    )

    sigs = extract_signatures(str(file_path))
    assert "Add(int a, int b) -> int" in sigs
    assert "Start() -> void" in sigs
    # qualified_identifier -- the class-qualifier is part of the readable
    # name, same as Lua's "Tbl:method" qualified text.
    assert "Server::Stop() -> void" in sigs


def test_extract_signatures_from_cpp_constructor_and_destructor(tmp_path):
    # Neither has a "type" field at all (there's no return type to have) --
    # must not append a dangling " -> " with nothing after it.
    file_path = tmp_path / "widget.cpp"
    file_path.write_text(
        "class Widget {\npublic:\n    Widget() {}\n    virtual ~Widget() {}\n};\n",
        encoding="utf-8",
    )

    sigs = extract_signatures(str(file_path))
    assert "Widget()" in sigs
    assert "~Widget()" in sigs


def test_extract_dependencies_from_cpp_file(tmp_path):
    # Both #include shapes: a system header (angle-bracket-delimited, no
    # nested string content node) and a local header (quoted, with the
    # usual nested string_content) -- both normalized to their bare stem,
    # same restraint _gdscript_dependency_handler already takes for its
    # own path-based imports.
    file_path = tmp_path / "main.cpp"
    file_path.write_text(
        '#include <iostream>\n'
        '#include "utils.h"\n',
        encoding="utf-8",
    )

    deps = extract_dependencies(str(file_path))
    assert "iostream" in deps
    assert "utils" in deps


def test_extract_signatures_from_rust_file(tmp_path):
    # function_item exposes "name"/"parameters"/"return_type"/"body" as
    # direct fields -- unlike C++, no declarator unwrapping is needed. One
    # node type covers a free function, an inline impl method, and a
    # trait-impl method uniformly (the enclosing impl_item/trait_item is
    # never read).
    file_path = tmp_path / "server.rs"
    file_path.write_text(
        "fn add(a: i32, b: i32) -> i32 {\n    a + b\n}\n\n"
        "struct Server;\n\n"
        "impl Server {\n"
        "    pub fn new() -> Self {\n        Server\n    }\n\n"
        "    fn no_return(&self) {\n    }\n"
        "}\n",
        encoding="utf-8",
    )

    sigs = extract_signatures(str(file_path))
    assert "add(a: i32, b: i32) -> i32" in sigs
    assert "new() -> Self" in sigs
    # no "return_type" field at all -- must not append a dangling " -> "
    # with nothing after it.
    assert "no_return(&self)" in sigs


def test_extract_signatures_from_rust_trait_method(tmp_path):
    # function_signature_item -- a trait's own method declaration, no
    # body at all (just a signature + ";"). Still has "name"/"parameters"/
    # "return_type", so it should still produce a real signature; having
    # no "body" field is harmless for compression (see the matching
    # compressor test).
    file_path = tmp_path / "greet.rs"
    file_path.write_text("trait Greet {\n    fn greet(&self) -> String;\n}\n", encoding="utf-8")

    assert "greet(&self) -> String" in extract_signatures(str(file_path))


def test_extract_dependencies_from_rust_file(tmp_path):
    # mod_item (a real file-backed submodule declaration) plus every real
    # use_declaration shape: plain path, grouped list (one entry per
    # sibling), wildcard (module path only, "*" dropped), and an aliased
    # path (alias dropped, original path kept).
    file_path = tmp_path / "lib.rs"
    file_path.write_text(
        "mod inner;\n"
        "use std::collections::{HashMap, HashSet};\n"
        "use foo::*;\n"
        "use foo::bar as baz;\n"
        "use crate::models::User;\n",
        encoding="utf-8",
    )

    deps = extract_dependencies(str(file_path))
    assert "inner" in deps
    assert "std.collections.HashMap" in deps
    assert "std.collections.HashSet" in deps
    assert "foo" in deps
    assert "foo.bar" in deps
    assert "crate.models.User" in deps


def test_extract_dependencies_from_rust_use_group_with_aliased_sibling(tmp_path):
    # A group whose own siblings can carry an "as" alias
    # (`foo::{bar as baz, qux}`) -- code review caught a real bug here:
    # splitting off " as " from the *whole* argument text before checking
    # for a group truncated it mid-string ("foo::{bar", losing "qux"
    # entirely and leaving a dangling "{"). Group-splitting now happens
    # first, with each sibling's own alias stripped one level down.
    file_path = tmp_path / "lib.rs"
    file_path.write_text("use foo::{bar as baz, qux};\n", encoding="utf-8")

    deps = extract_dependencies(str(file_path))
    assert "foo.bar" in deps
    assert "foo.qux" in deps
    assert not any("{" in d for d in deps)


def test_extract_dependencies_recurses_into_inline_rust_module(tmp_path):
    # `mod tests { ... }` (a body-having inline submodule, the ordinary
    # `#[cfg(test)] mod tests { ... }` pattern) is not itself a file
    # reference -- code review caught the handler matching it
    # unconditionally, both emitting a bogus "tests" dependency and (since
    # it returned True) never recursing into the body at all, silently
    # hiding every real use/mod declaration nested inside it.
    file_path = tmp_path / "lib.rs"
    file_path.write_text(
        "mod tests {\n"
        "    use crate::utils::helper;\n"
        "    fn test_it() {}\n"
        "}\n"
        "mod real_mod;\n",
        encoding="utf-8",
    )

    deps = extract_dependencies(str(file_path))
    assert "crate.utils.helper" in deps
    assert "real_mod" in deps
    assert "tests" not in deps


def test_extract_signatures_from_csharp_file(tmp_path):
    # method_declaration/constructor_declaration expose "name"/"parameters"/
    # "body" as direct fields, same shape as Java/Go -- no declarator
    # unwrapping needed. The return-type field is named "returns", a fourth
    # naming convention alongside return_type/result/type.
    file_path = tmp_path / "Server.cs"
    file_path.write_text(
        "public class Server\n"
        "{\n"
        "    public Server(string name) {}\n\n"
        "    public static int Add(int a, int b)\n"
        "    {\n"
        "        return a + b;\n"
        "    }\n\n"
        "    public void Start() {}\n"
        "}\n",
        encoding="utf-8",
    )

    sigs = extract_signatures(str(file_path))
    # constructor -- no return type at all, must not append a dangling " -> ".
    assert "Server(string name)" in sigs
    assert "Add(int a, int b) -> int" in sigs
    assert "Start() -> void" in sigs


def test_extract_signatures_from_csharp_interface_method(tmp_path):
    # An interface's own method declaration (no body, just a signature +
    # ";") is the *same* method_declaration node type as a class method --
    # still has "name"/"parameters"/"returns", so it should still produce a
    # real signature (compression is separately verified to leave it alone).
    file_path = tmp_path / "IGreet.cs"
    file_path.write_text("public interface IGreet\n{\n    string Greet();\n}\n", encoding="utf-8")

    assert "Greet() -> string" in extract_signatures(str(file_path))


def test_extract_signatures_from_csharp_destructor_is_distinguished_from_constructor(tmp_path):
    # destructor_declaration's own "name" field is the bare identifier with
    # no "~" -- the token that actually distinguishes it from a same-named
    # constructor is a separate, unnamed sibling child, not part of the
    # field. Without LanguageConfig.name_prefixes, a class defining both
    # would produce two identical "Widget()" signatures.
    file_path = tmp_path / "Widget.cs"
    file_path.write_text(
        "class Widget\n{\n    public Widget() {}\n    ~Widget() {}\n}\n",
        encoding="utf-8",
    )

    sigs = extract_signatures(str(file_path))
    assert "Widget()" in sigs
    assert "~Widget()" in sigs


def test_extract_dependencies_from_csharp_file(tmp_path):
    # Every real using_directive shape: plain, multi-segment, static,
    # aliased (the real path, not the alias name, must be captured).
    file_path = tmp_path / "Server.cs"
    file_path.write_text(
        "using System;\n"
        "using System.Collections.Generic;\n"
        "using static System.Math;\n"
        "using Alias = MyApp.Models.Settings;\n",
        encoding="utf-8",
    )

    deps = extract_dependencies(str(file_path))
    assert "System" in deps
    assert "System.Collections.Generic" in deps
    assert "System.Math" in deps
    # the real path, not the "Alias" name bound to using_directive's own
    # "name" field.
    assert "MyApp.Models.Settings" in deps
    assert "Alias" not in deps


def test_extract_dependencies_ignores_csharp_non_path_type_aliases(tmp_path):
    # A type alias whose right-hand side isn't itself an identifier/
    # qualified_name (a primitive, array, or nullable type -- all real,
    # common alias targets) has no path-shaped child after "=" at all.
    # Code review caught the first version falling back to the *alias
    # name itself* (the identifier before "=") in this case, wrongly
    # emitting it as a bogus dependency -- must find nothing instead,
    # the same restraint every other handler here takes for an
    # unresolvable target.
    file_path = tmp_path / "Aliases.cs"
    file_path.write_text(
        "using MyInt = int;\n"
        "using IntArray = int[];\n"
        "using Nullable = System.Int32?;\n",
        encoding="utf-8",
    )

    deps = extract_dependencies(str(file_path))
    assert "MyInt" not in deps
    assert "IntArray" not in deps
    assert "Nullable" not in deps


def test_extract_signatures_from_php_file(tmp_path):
    # function_definition/method_declaration expose "name"/"parameters"/
    # "return_type"/"body" as direct fields -- the same convention Python/
    # TS/GDScript already use, so no extractor.py changes were needed.
    file_path = tmp_path / "UserService.php"
    file_path.write_text(
        "<?php\n\n"
        "class UserService\n"
        "{\n"
        "    public function getName(): string\n"
        "    {\n"
        "        return $this->name;\n"
        "    }\n"
        "}\n\n"
        "function standaloneHelper($x, $y = 10): int\n"
        "{\n"
        "    return $x + $y;\n"
        "}\n",
        encoding="utf-8",
    )

    sigs = extract_signatures(str(file_path))
    assert "getName() -> string" in sigs
    assert "standaloneHelper($x, $y = 10) -> int" in sigs


def test_extract_signatures_from_php_interface_method(tmp_path):
    # An interface's own method_declaration (no body, just a signature +
    # ";") is the same node type as a class method -- still has "name"/
    # "parameters"/"return_type", so it should still produce a real
    # signature (compression is separately verified to leave it alone).
    file_path = tmp_path / "Greetable.php"
    file_path.write_text(
        "<?php\n\ninterface Greetable {\n    public function greet(): string;\n}\n",
        encoding="utf-8",
    )

    assert "greet() -> string" in extract_signatures(str(file_path))


def test_extract_dependencies_from_php_file(tmp_path):
    # namespace_use_declaration: a plain use, an aliased use (the real
    # path, not the alias, must be captured), and a grouped use (the outer
    # namespace_name prefix re-attached to each of the group's own
    # siblings). Plus require_once/include -- real relative file paths,
    # normalized to their bare stem the same way GDScript's preload()/
    # load() already is.
    file_path = tmp_path / "UserService.php"
    file_path.write_text(
        "<?php\n\n"
        "use App\\Models\\User;\n"
        "use App\\Helpers\\{Formatter, Validator as V};\n"
        "require_once 'config.php';\n"
        "include 'helpers/legacy.php';\n",
        encoding="utf-8",
    )

    deps = extract_dependencies(str(file_path))
    assert "App.Models.User" in deps
    assert "App.Helpers.Formatter" in deps
    assert "App.Helpers.Validator" in deps
    assert "V" not in deps
    assert "config" in deps
    assert "legacy" in deps


def test_extract_dependencies_from_php_multiple_non_grouped_uses(tmp_path):
    # `use A, B as BB;` -- several direct namespace_use_clause siblings
    # under one namespace_use_declaration, no group/prefix involved.
    file_path = tmp_path / "mod.php"
    file_path.write_text("<?php\n\nuse App\\A, App\\B as BB;\n", encoding="utf-8")

    deps = extract_dependencies(str(file_path))
    assert "App.A" in deps
    assert "App.B" in deps
    assert "BB" not in deps


def test_extract_signatures_from_ruby_file(tmp_path):
    # method/singleton_method expose "name"/"parameters"/"body" as direct
    # fields -- no return_type field at all (vanilla Ruby has no
    # return-type syntax), which extract_signatures() already handles for
    # free (no dangling " -> " suffix).
    file_path = tmp_path / "user_service.rb"
    file_path.write_text(
        "class UserService\n"
        "  def initialize(user)\n"
        "    @user = user\n"
        "  end\n\n"
        "  def self.create(data)\n"
        "    new(data)\n"
        "  end\n"
        "end\n",
        encoding="utf-8",
    )

    sigs = extract_signatures(str(file_path))
    assert "initialize(user)" in sigs
    assert "create(data)" in sigs


def test_extract_signatures_from_ruby_method_with_no_parens(tmp_path):
    # Idiomatic Ruby very commonly omits parens for a zero-argument method
    # -- this grammar then has no "parameters" field at all (unlike most
    # grammars here, which always emit an empty parameter-list node even
    # for zero args). zero_arg_types is what makes this still produce a
    # real "()" signature instead of silently producing none.
    file_path = tmp_path / "mod.rb"
    file_path.write_text(
        "def get_name\n  @name\nend\n\n"
        "class Foo\n  def self.bar\n    42\n  end\nend\n",
        encoding="utf-8",
    )

    sigs = extract_signatures(str(file_path))
    assert "get_name()" in sigs
    assert "bar()" in sigs


def test_extract_signatures_skips_ruby_bare_single_param_arrow_equivalent(tmp_path):
    # Sanity check that zero_arg_types is opt-in per node type, not a
    # blanket "missing params means zero args" default -- a TS bare arrow
    # (a genuinely different language/config) must still produce no
    # signature, exactly as before this feature existed.
    file_path = tmp_path / "mod.ts"
    file_path.write_text("const double = x => x * 2;\n", encoding="utf-8")

    assert extract_signatures(str(file_path)) == []


def test_extract_dependencies_from_ruby_file(tmp_path):
    # require (an external gem/stdlib name) and require_relative (a real
    # relative file path, ".rb" conventionally omitted) are both ordinary
    # method calls, not a dedicated import-statement node -- matched by
    # callee name the same way Lua's require() is, and normalized to their
    # bare Path stem so a directory component doesn't block a match.
    file_path = tmp_path / "mod.rb"
    file_path.write_text(
        "require 'json'\nrequire_relative 'helpers/formatter'\n",
        encoding="utf-8",
    )

    deps = extract_dependencies(str(file_path))
    assert "json" in deps
    assert "formatter" in deps


def test_extract_dependencies_ignores_ruby_call_with_receiver(tmp_path):
    # A call with a receiver (Foo.require(...)) isn't a real require/
    # require_relative statement -- only a bare, receiver-less call counts.
    file_path = tmp_path / "mod.rb"
    file_path.write_text("Foo.require('json')\n", encoding="utf-8")

    assert extract_dependencies(str(file_path)) == []


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


def test_extract_api_detects_express_style_routes(tmp_path):
    # extract_api() used to run one hardcoded, Python-specific traversal
    # unconditionally against every file's own AST -- harmless for other
    # languages only because their AST never happened to contain a
    # decorated_definition node, not because it was actually generalized.
    file_path = tmp_path / "app.ts"
    file_path.write_text(
        'app.get("/users", (req, res) => { res.send([]); });\n'
        'router.post("/users", handler);\n'
        'router.delete("/users/:id", handler);\n',
        encoding="utf-8",
    )

    api = extract_api(str(file_path))
    assert "GET /users" in api
    assert "POST /users" in api
    assert "DELETE /users/:id" in api


def test_extract_api_detects_nested_express_routes_inside_a_callback(tmp_path):
    # A route-registration call's callback is an ordinary argument, not a
    # dead end -- an (unusual but possible) nested registration inside it
    # must still be found, unlike Python's decorator (which can't nest a
    # second route inside itself the same way).
    file_path = tmp_path / "app.ts"
    file_path.write_text(
        'app.get("/a", () => {\n  app.get("/b", handler);\n});\n',
        encoding="utf-8",
    )

    api = extract_api(str(file_path))
    assert "GET /a" in api
    assert "GET /b" in api


def test_extract_api_only_matches_the_actual_first_argument(tmp_path):
    # A string that isn't the call's first argument must not be mistaken
    # for the route path, even if it happens to start with "/".
    file_path = tmp_path / "app.ts"
    file_path.write_text('app.get(middleware, "/should-not-count");\n', encoding="utf-8")

    assert extract_api(str(file_path)) == []


def test_extract_api_ignores_non_route_get_calls(tmp_path):
    # A same-shaped .get(...) call on some unrelated object (a Map, a
    # config object) must not be mistaken for a route registration -- the
    # path argument has to actually look like a route ("/"-prefixed).
    file_path = tmp_path / "app.ts"
    file_path.write_text('const value = cache.get("someKey");\n', encoding="utf-8")

    assert extract_api(str(file_path)) == []


def test_extract_api_ignores_template_literal_paths(tmp_path):
    # Only a plain string-literal path is statically resolvable -- a
    # template literal with interpolation is left uncaptured, same
    # restraint _lua_dependency_handler takes for a dynamically-computed
    # require().
    file_path = tmp_path / "app.ts"
    file_path.write_text("app.put(`/users/${id}`, handler);\n", encoding="utf-8")

    assert extract_api(str(file_path)) == []


def test_extract_api_returns_empty_for_languages_with_no_routing_convention(tmp_path):
    # PHP and Ruby are here too, alongside Lua: both have real web
    # frameworks (Laravel/Symfony/Slim; Rails/Sinatra) whose route
    # registration conventions don't share one dominant shape the way
    # Flask's decorator or Express's .get() call do -- same "no
    # established convention" default as Java/Lua/GDScript/Rust/C#.
    for suffix, code in (
        (".lua", "local function greet(name)\n    return name\nend\n"),
        (".php", "<?php\nfunction greet($name) {\n    return $name;\n}\n"),
        (".rb", "def greet(name)\n  name\nend\n"),
    ):
        file_path = tmp_path / f"mod{suffix}"
        file_path.write_text(code, encoding="utf-8")
        assert extract_api(str(file_path)) == []


def test_extract_signatures_from_gdscript_constructor(tmp_path):
    # constructor_definition has no "name" field at all -- the grammar
    # types its own fixed keyword ("_init") as a literal token, not an
    # identifier under a field -- so LanguageConfig.implicit_names supplies
    # the fallback.
    file_path = tmp_path / "player.gd"
    file_path.write_text("func _init(x, y):\n    var total = x + y\n", encoding="utf-8")

    assert "_init(x, y)" in extract_signatures(str(file_path))


def test_unsupported_extension_returns_empty_lists(tmp_path):
    file_path = tmp_path / "notes.xyz"
    file_path.write_text("whatever", encoding="utf-8")

    assert extract_signatures(str(file_path)) == []
    assert extract_dependencies(str(file_path)) == []
    assert extract_api(str(file_path)) == []
