import json

# Arrays: beyond this many items, keep only the leading ones and elide the rest with a marker.
MAX_ARRAY_ITEMS = 3
# Strings: beyond this length, truncate and append a marker.
MAX_STRING_LEN = 200

MARKER = "⋮----"


def compress_json(text: str) -> str:
    """Compresses JSON by keeping structure (keys) and eliding repetitive/long content.

    Same idea as code compression, which keeps function signatures and blanks only
    the body: dict keys, short values, and short arrays are kept as-is, while
    - an array longer than MAX_ARRAY_ITEMS keeps only the leading items, replacing
      the rest with a marker
    - a string longer than MAX_STRING_LEN is truncated with a marker appended
    Indentation is also stripped, cutting pure formatting tokens too.

    Returns the original text unchanged if it isn't valid JSON (parsing failed).
    """
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return text

    compressed = _compress_value(data)
    return json.dumps(compressed, ensure_ascii=False, separators=(",", ":"))


def _compress_value(value):
    if isinstance(value, dict):
        return {key: _compress_value(v) for key, v in value.items()}

    if isinstance(value, list):
        if len(value) <= MAX_ARRAY_ITEMS:
            return [_compress_value(v) for v in value]

        kept = [_compress_value(v) for v in value[:MAX_ARRAY_ITEMS]]
        remaining = len(value) - MAX_ARRAY_ITEMS
        kept.append(f"{MARKER} ({remaining}개 항목 생략)")
        return kept

    if isinstance(value, str) and len(value) > MAX_STRING_LEN:
        return value[:MAX_STRING_LEN] + f" {MARKER} (생략)"

    return value
