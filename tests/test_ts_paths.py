import json

from ziplex.import_context import resolve_import_context, expand_file_dependencies
from ziplex.ts_paths import _strip_jsonc, load_compiler_paths, resolve_ts_context


def _write(root, rel, text):
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_strip_jsonc_drops_comments_and_trailing_commas_but_not_string_contents():
    text = """{
      // line comment
      "compilerOptions": { /* block */
        "paths": { "@/*": ["./src/*"], "//x": ["a/*b"], },
      },
    }"""
    data = json.loads(_strip_jsonc(text))
    assert data["compilerOptions"]["paths"] == {"@/*": ["./src/*"], "//x": ["a/*b"]}


def test_rewrites_a_paths_alias_to_the_collected_file(tmp_path):
    _write(tmp_path, "tsconfig.json", '{"compilerOptions": {"baseUrl": ".", "paths": {"@/*": ["src/*"]}}}')
    names = ["src/app.ts", "src/utils/format.ts", "src/components/index.tsx"]
    index = resolve_ts_context(str(tmp_path), names)

    deps = index.rewrite("src/app.ts", ["@/utils/format", "@/components", "react", "./local"])
    assert deps == ["src/utils/format.ts", "src/components/index.tsx", "react", "./local"]


def test_paths_without_base_url_resolve_relative_to_the_declaring_config(tmp_path):
    _write(tmp_path, "web/tsconfig.json", '{"compilerOptions": {"paths": {"~lib/*": ["./lib/*"]}}}')
    index = resolve_ts_context(str(tmp_path), ["web/main.ts", "web/lib/db.ts"])
    assert index.rewrite("web/main.ts", ["~lib/db"]) == ["web/lib/db.ts"]


def test_base_url_alone_resolves_bare_specifiers_only_when_a_file_exists(tmp_path):
    _write(tmp_path, "tsconfig.json", '{"compilerOptions": {"baseUrl": "src"}}')
    index = resolve_ts_context(str(tmp_path), ["src/a.ts", "src/services/api.ts"])
    assert index.rewrite("src/a.ts", ["services/api", "lodash"]) == ["src/services/api.ts", "lodash"]


def test_longest_prefix_wins_and_later_substitutions_are_fallbacks(tmp_path):
    _write(tmp_path, "tsconfig.json", """{"compilerOptions": {"baseUrl": ".", "paths": {
        "@/*": ["src/*"],
        "@/ui/*": ["packages/ui/missing/*", "packages/ui/src/*"]
    }}}""")
    names = ["src/a.ts", "src/ui/button.ts", "packages/ui/src/button.ts"]
    index = resolve_ts_context(str(tmp_path), names)
    assert index.rewrite("src/a.ts", ["@/ui/button"]) == ["packages/ui/src/button.ts"]


def test_extends_chain_is_followed_and_child_overrides(tmp_path):
    _write(tmp_path, "tsconfig.base.json", '{"compilerOptions": {"baseUrl": ".", "paths": {"@shared/*": ["shared/*"]}}}')
    _write(tmp_path, "apps/web/tsconfig.json", '{"extends": "../../tsconfig.base.json", "compilerOptions": {}}')
    options = load_compiler_paths(str(tmp_path), "apps/web/tsconfig.json")
    assert options["paths_dir"] == "."

    index = resolve_ts_context(str(tmp_path), ["apps/web/page.tsx", "shared/date.ts"])
    assert index.rewrite("apps/web/page.tsx", ["@shared/date"]) == ["shared/date.ts"]


def test_package_extends_is_skipped_without_error(tmp_path):
    _write(tmp_path, "tsconfig.json", '{"extends": "@tsconfig/node20/tsconfig.json", "compilerOptions": {"paths": {"#/*": ["./src/*"]}}}')
    index = resolve_ts_context(str(tmp_path), ["src/a.ts", "src/b.ts"])
    assert index.rewrite("src/a.ts", ["#/b"]) == ["src/b.ts"]


def test_nearest_config_governs_and_jsconfig_is_honored(tmp_path):
    _write(tmp_path, "tsconfig.json", '{"compilerOptions": {"paths": {"@/*": ["./root/*"]}}}')
    _write(tmp_path, "site/jsconfig.json", '{"compilerOptions": {"paths": {"@/*": ["./src/*"]}}}')
    names = ["root/x.ts", "site/src/x.js", "site/src/page.js", "root/page.ts"]
    index = resolve_ts_context(str(tmp_path), names)
    assert index.rewrite("site/src/page.js", ["@/x"]) == ["site/src/x.js"]
    assert index.rewrite("root/page.ts", ["@/x"]) == ["root/x.ts"]


def test_alias_escaping_the_project_or_unreadable_config_passes_through(tmp_path):
    _write(tmp_path, "tsconfig.json", '{"compilerOptions": {"paths": {"@ext/*": ["../outside/*"]}}}')
    _write(tmp_path, "broken/tsconfig.json", "{ not json")
    index = resolve_ts_context(str(tmp_path), ["a.ts", "broken/b.ts"])
    assert index.rewrite("a.ts", ["@ext/x"]) == ["@ext/x"]
    assert index.rewrite("broken/b.ts", ["@ext/x"]) == ["@ext/x"]


def test_non_ts_project_gets_no_index_and_non_ts_files_are_untouched(tmp_path):
    assert resolve_ts_context(str(tmp_path), ["main.py"]) is None
    _write(tmp_path, "tsconfig.json", '{"compilerOptions": {"baseUrl": "."}}')
    index = resolve_ts_context(str(tmp_path), ["a.ts", "tool.py", "utils.py"])
    assert index.rewrite("tool.py", ["utils"]) == ["utils"]


def test_import_context_applies_ts_aliases_through_the_shared_entry_point(tmp_path):
    _write(tmp_path, "tsconfig.json", '{"compilerOptions": {"paths": {"@/*": ["./src/*"]}}}')
    names = ["src/a.ts", "src/b.ts"]
    ctx = resolve_import_context(str(tmp_path), names)
    assert expand_file_dependencies(str(tmp_path / "src/a.ts"), "src/a.ts", ["@/b"], ctx) == ["src/b.ts"]
