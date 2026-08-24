import json

from ziplex.extract.text.json import compress_json, MAX_STRING_LEN, MAX_ARRAY_ITEMS
from ziplex.extract.text.markdown import compress_markdown, MAX_PARAGRAPH_LEN, MAX_LIST_ITEMS
from ziplex.extract.text.txt import compress_txt, MAX_LINE_LEN, MAX_BLOCK_LINES


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
