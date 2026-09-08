import json

from ruamel.yaml import YAML

from ziplex.extract.text.dockerfile import (
    compress_dockerfile,
    MARKER,
    MAX_LINE_LEN as DOCKERFILE_MAX_LINE_LEN,
)
from ziplex.extract.text.json import compress_json, MAX_STRING_LEN, MAX_ARRAY_ITEMS
from ziplex.extract.text.markdown import compress_markdown, MAX_PARAGRAPH_LEN, MAX_LIST_ITEMS
from ziplex.extract.text.txt import compress_txt, MAX_LINE_LEN, MAX_BLOCK_LINES
from ziplex.extract.text.yaml import (
    compress_yaml,
    MAX_ARRAY_ITEMS as YAML_MAX_ARRAY_ITEMS,
    MAX_STRING_LEN as YAML_MAX_STRING_LEN,
)

_yaml = YAML()


def test_json_keeps_short_values_untouched():
    text = json.dumps({"name": "ziplex", "count": 3})
    result = compress_json(text)
    assert json.loads(result) == {"name": "ziplex", "count": 3}


def test_json_truncates_long_strings():
    long_value = "x" * (MAX_STRING_LEN + 50)
    result = compress_json(json.dumps({"key": long_value}))
    decoded = json.loads(result)
    assert len(decoded["key"]) < len(long_value)
    assert decoded["key"].startswith("x" * MAX_STRING_LEN)


def test_json_elides_long_arrays():
    result = compress_json(json.dumps({"items": list(range(MAX_ARRAY_ITEMS + 10))}))
    decoded = json.loads(result)
    items = decoded["items"]
    # kept items + one marker entry describing how many were elided
    assert len(items) == MAX_ARRAY_ITEMS + 1
    assert items[:MAX_ARRAY_ITEMS] == list(range(MAX_ARRAY_ITEMS))


def test_json_invalid_input_returned_unchanged():
    invalid = "{not valid json"
    assert compress_json(invalid) == invalid


def test_markdown_keeps_headings_and_short_paragraphs():
    text = "# Title\n\nA short paragraph.\n"
    result = compress_markdown(text)
    assert "# Title" in result
    assert "A short paragraph." in result


def test_markdown_truncates_long_paragraph():
    paragraph = "word " * (MAX_PARAGRAPH_LEN // 4)  # comfortably over the limit
    result = compress_markdown(paragraph)
    assert len(result.splitlines()[0]) < len(paragraph)
    assert "(생략)" in result


def test_markdown_elides_long_list():
    items = "\n".join(f"- item {i}" for i in range(MAX_LIST_ITEMS + 5))
    result = compress_markdown(items)
    assert result.count("- item") == MAX_LIST_ITEMS
    assert "생략" in result


def test_markdown_codeblock_reuses_code_compressor_by_language():
    text = "```python\ndef add(a, b):\n    total = a + b\n    return total\n```\n"
    result = compress_markdown(text)
    assert "def add(a, b):" in result
    assert "total = a + b" not in result


def test_txt_truncates_long_line():
    line = "x" * (MAX_LINE_LEN + 20)
    result = compress_txt(line)
    assert len(result.splitlines()[0]) < len(line)


def test_txt_elides_long_block_but_keeps_blank_lines_as_structure():
    block = "\n".join(f"line {i}" for i in range(MAX_BLOCK_LINES + 5))
    text = f"header\n\n{block}\n"
    result = compress_txt(text)
    assert "header" in result
    assert "생략" in result
    assert "" in result.splitlines()  # the blank separator line survives


def test_yaml_keeps_short_values_untouched():
    result = compress_yaml("name: ziplex\ncount: 3\n")
    assert _yaml.load(result) == {"name": "ziplex", "count": 3}


def test_yaml_truncates_long_strings():
    long_value = "x" * (YAML_MAX_STRING_LEN + 50)
    result = compress_yaml(f"key: {long_value}\n")
    decoded = _yaml.load(result)
    assert len(decoded["key"]) < len(long_value)
    assert decoded["key"].startswith("x" * YAML_MAX_STRING_LEN)


def test_yaml_elides_long_arrays():
    items = "\n".join(f"  - {i}" for i in range(YAML_MAX_ARRAY_ITEMS + 10))
    result = compress_yaml(f"items:\n{items}\n")
    decoded = _yaml.load(result)
    kept = decoded["items"]
    assert len(kept) == YAML_MAX_ARRAY_ITEMS + 1
    assert kept[:YAML_MAX_ARRAY_ITEMS] == list(range(YAML_MAX_ARRAY_ITEMS))


def test_yaml_invalid_input_returned_unchanged():
    invalid = "key: [unclosed\n"
    assert compress_yaml(invalid) == invalid


def test_yaml_empty_input_returned_unchanged():
    assert compress_yaml("") == ""
    assert compress_yaml("   \n") == "   \n"


def test_yaml_does_not_corrupt_ambiguous_unquoted_scalars():
    # The "Norway problem": PyYAML's safe_load/safe_dump round-trip turns an
    # unquoted `3.10` into the float 3.1 and an unquoted `NO` into `False` --
    # a real, silent value-corruption bug (confirmed live before choosing
    # ruamel.yaml over PyYAML for this module). Neither should happen here,
    # even though neither value is anywhere near MAX_STRING_LEN and so is
    # never "touched" by the compression logic itself -- this is purely a
    # question of whether round-tripping alone corrupts an untouched value.
    result = compress_yaml("version: 3.10\ncountry: NO\n")
    assert "3.10" in result
    assert "3.1\n" not in result  # would indicate silent float coercion
    assert "NO" in result
    assert "false" not in result.lower()


def test_yaml_preserves_comments():
    result = compress_yaml("key: value  # keep this\n")
    assert "# keep this" in result


def test_yaml_preserves_key_order_without_alphabetizing():
    # PyYAML's safe_dump defaults to sort_keys=True, which would reorder this
    # to apiVersion/kind/metadata -- a real, confusing bug for YAML formats
    # (Kubernetes manifests) with a conventional field order.
    result = compress_yaml("kind: Pod\napiVersion: v1\nmetadata:\n  name: x\n")
    assert result.index("kind:") < result.index("apiVersion:")


def test_yaml_does_not_duplicate_merge_derived_keys():
    # A CommentedMap materialized via `<<: *anchor` already contains every
    # merged-in key in its own storage -- reassigning one (even to the same
    # value, which the recursive compress pass does for every key it visits)
    # makes ruamel treat it as newly explicit and spell it out redundantly
    # alongside the `<<:` reference, undoing the whole anchor-preservation
    # point above. Confirmed live before this test existed: a real
    # docker-compose.yml fixture got "restart: always" duplicated under
    # every service using `<<: *defaults`.
    text = "defaults: &defaults\n  restart: always\n  env: prod\nweb:\n  <<: *defaults\n  image: nginx\n"
    result = compress_yaml(text)
    assert result.count("restart:") == 1
    assert result.count("env:") == 1


def test_yaml_does_not_double_compress_an_aliased_container():
    # In ruamel's round-trip mode, a plain `*alias` to a whole list/map isn't
    # a copy -- it's the exact same Python object as the `&anchor` it points
    # to. Without a visited-object guard, the shared list gets compressed
    # twice: the second pass sees the marker string the first pass already
    # appended as if it were real data and re-truncates around it,
    # understating how much was actually elided. Caught by /code-review low
    # (reported "1개 항목 생략" instead of the correct "2개 항목 생략" for a
    # 5-item list aliased twice) and reproduced live before fixing.
    text = "shared: &s\n  - 1\n  - 2\n  - 3\n  - 4\n  - 5\nother: *s\n"
    result = compress_yaml(text)
    assert "(2개 항목 생략)" in result
    assert "(1개 항목 생략)" not in result


def test_yaml_preserves_anchors_instead_of_expanding_them():
    # PyYAML's safe_load fully expands `<<: *defaults` merges into duplicated
    # key/value pairs per service -- for an anchor-heavy file (common in
    # Docker Compose / CI matrices), that can make "compression" produce a
    # *larger* file than the original. ruamel's round-trip mode should keep
    # the anchor/alias as a reference instead.
    text = "defaults: &defaults\n  image: nginx\nservice_a:\n  <<: *defaults\n"
    result = compress_yaml(text)
    assert "&defaults" in result
    assert "<<: *defaults" in result


def test_yaml_compresses_each_document_and_rejoins_multi_document_files():
    long_value = "x" * (YAML_MAX_STRING_LEN + 50)
    text = f"kind: Pod\nname: a\n---\nkind: Service\nvalue: {long_value}\n"
    result = compress_yaml(text)
    docs = list(_yaml.load_all(result))
    assert len(docs) == 2
    assert docs[0] == {"kind": "Pod", "name": "a"}
    assert len(docs[1]["value"]) < len(long_value)


def test_yaml_single_document_has_no_leading_separator():
    result = compress_yaml("kind: Pod\n")
    assert not result.startswith("---")


def test_dockerfile_keeps_short_instructions_untouched():
    text = "FROM node:20-alpine\nWORKDIR /app\nCOPY . .\nCMD [\"node\", \"index.js\"]\n"
    result = compress_dockerfile(text)
    assert result == text.rstrip("\n")


def test_dockerfile_elides_a_long_multiline_run_instruction():
    # A RUN's shell command chained across several `\`-continued physical
    # lines is one logical instruction, judged as a whole against
    # MAX_INSTRUCTION_LINES -- not blindly line-by-line the way a generic
    # block-length check with no idea where the instruction ends would.
    lines = ["RUN apt-get update && \\"] + [f"    pkg{i} \\" for i in range(10)] + ["    && rm -rf /var/lib/apt/lists/*"]
    text = "FROM alpine\n" + "\n".join(lines) + "\nCMD [\"true\"]\n"

    result = compress_dockerfile(text)

    assert "FROM alpine" in result
    assert MARKER.strip() in result
    assert "pkg9" not in result
    assert 'CMD ["true"]' in result
    # kept lines: the RUN instruction's own MAX_INSTRUCTION_LINES leading
    # lines, not the whole 12-line block.
    assert result.count("pkg") < 10


def test_dockerfile_truncates_an_individually_long_line():
    long_label = "LABEL description=" + '"' + ("x" * (DOCKERFILE_MAX_LINE_LEN + 50)) + '"'
    text = f"FROM alpine\n{long_label}\n"

    result = compress_dockerfile(text)

    assert MARKER.strip() in result
    assert long_label not in result


def test_dockerfile_recognizes_multi_stage_builds():
    text = (
        "FROM node:20-alpine AS builder\n"
        "WORKDIR /app\n"
        "FROM node:20-alpine\n"
        "COPY --from=builder /app/dist ./dist\n"
    )
    result = compress_dockerfile(text)
    assert result == text.rstrip("\n")


def test_dockerfile_invalid_input_returned_unchanged():
    # A parse failure (or anything else going wrong) must never corrupt
    # the file -- same "don't guess, don't corrupt" contract every other
    # text compressor here already has.
    assert compress_dockerfile("") == ""
