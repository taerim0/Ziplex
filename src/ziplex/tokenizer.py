import functools
import json
import os

from .extract.code.compressor import compress_file
from .file.textutil import read_text

MODEL_ENCODINGS = {
    "GPT-4o":  "o200k_base",
}

# tiktoken only ships encodings for OpenAI's own model family -- Claude and
# Gemini use their own, non-public tokenizers, so there's no exact count to
# get here. APPROX_CHARS_PER_TOKEN is a rough stand-in (Anthropic's own docs
# quote ~3.5-4 characters per token for English text as a reasonable
# estimate; Gemini's publicly documented rule of thumb is close enough that
# reusing the same constant for both beats pretending they're precise).
# Every model in MODEL_MAX_TOKENS but not in MODEL_ENCODINGS is counted this
# way -- see _count_for_model()/is_approx_model() below.
APPROX_CHARS_PER_TOKEN = 4

MODEL_MAX_TOKENS = {
    "GPT-4o":  128_000,
    "Claude":  200_000,
    "Gemini":  1_000_000,
}

@functools.lru_cache(maxsize=None)
def _get_encoding(encoding_name: str):
    """The tiktoken encoding, or None when it can't be loaded (on a cold
    cache tiktoken downloads it, which fails offline). Cached either way, so
    a failed load is attempted once per process."""
    try:
        import tiktoken
        return tiktoken.get_encoding(encoding_name)
    except Exception:
        return None


def count_tokens(text: str | list[str], encoding_name: str) -> int:
    """Exact tiktoken count, or the APPROX_CHARS_PER_TOKEN estimate when the
    encoding is unavailable (see _get_encoding()). A list is counted part by
    part on tiktoken's thread pool (a large project's text is tens of MB);
    its total can differ from the joined text's by a token per boundary."""
    enc = _get_encoding(encoding_name)
    if isinstance(text, list):
        if enc is None:
            return sum(len(part) for part in text) // APPROX_CHARS_PER_TOKEN
        return sum(len(t) for t in enc.encode_ordinary_batch(text, num_threads=os.cpu_count() or 4))
    if enc is None:
        return len(text) // APPROX_CHARS_PER_TOKEN
    # disallowed_special=(): count special-token text like "<|endoftext|>"
    # as ordinary text. tiktoken raises ValueError on it by default, and
    # pack() counts tokens only after every LLM call is paid for.
    return len(enc.encode(text, disallowed_special=()))


def is_approx_model(model: str) -> bool:
    """True for a model in MODEL_MAX_TOKENS with no tiktoken encoding of its
    own (Claude, Gemini), or whose encoding failed to load -- its token
    count is a character-based estimate, not an exact one. Exposed so a
    caller (cli.py's printer, a future GUI label) can mark an approximate
    figure as such instead of presenting it with the same confidence as a
    real tiktoken count.
    """
    encoding = MODEL_ENCODINGS.get(model)
    return encoding is None or _get_encoding(encoding) is None


def _count_for_model(text: str | list[str], model: str) -> int:
    """Token count for `model` -- an exact tiktoken count when one of its
    own encodings exists (MODEL_ENCODINGS), otherwise APPROX_CHARS_PER_TOKEN
    applied to `text`'s length (see that constant's own comment for why)."""
    encoding = MODEL_ENCODINGS.get(model)
    if encoding is not None:
        return count_tokens(text, encoding)
    length = sum(len(part) for part in text) if isinstance(text, list) else len(text)
    return length // APPROX_CHARS_PER_TOKEN


def count_tokens_for_model(text: str, model: str) -> int:
    """Public form of _count_for_model() -- cli.py's `--max-tokens` guard
    measures the saved aif.json itself with it."""
    return _count_for_model(text, model)


def analyze_tokens(file_paths: list[str]) -> tuple:
    parts = []
    for file_path in file_paths:
        content = read_text(file_path)
        if content is None:
            continue
        parts.append(f"\n### {file_path}\n{content}\n")
    combined = "".join(parts)

    results = {}
    for model, max_tokens in MODEL_MAX_TOKENS.items():
        token_count = _count_for_model(parts, model)
        percentage = (token_count / max_tokens) * 100
        filled = int(percentage / 10)
        bar = "█" * filled + "░" * (10 - filled)

        results[model] = {
            "tokens": token_count,
            "max": max_tokens,
            "percentage": round(percentage, 1),
            "bar": bar,
            "approx": is_approx_model(model),
        }

    return results, combined

def analyze_tokens_with_compression(file_paths: list[str]) -> tuple:
    original_parts = []
    compressed_parts = []

    for file_path in file_paths:
        content = read_text(file_path)
        if content is None:
            continue
        original_parts.append(f"\n### {file_path}\n{content}\n")

        compressed = compress_file(file_path)
        compressed_parts.append(f"\n### {file_path}\n{compressed}\n")

    original_text = "".join(original_parts)
    compressed_text = "".join(compressed_parts)

    results = {}
    for model, max_tokens in MODEL_MAX_TOKENS.items():
        original_count = _count_for_model(original_parts, model)
        compressed_count = _count_for_model(compressed_parts, model)

        saved = original_count - compressed_count
        saved_pct = round((saved / original_count) * 100, 1) if original_count > 0 else 0

        original_pct = (original_count / max_tokens) * 100
        compressed_pct = (compressed_count / max_tokens) * 100

        filled_o = int(original_pct / 10)
        filled_c = int(compressed_pct / 10)

        results[model] = {
            "original":       original_count,
            "compressed":     compressed_count,
            "saved":          saved,
            "saved_pct":      saved_pct,
            "max":            max_tokens,
            "original_bar":   "█" * filled_o + "░" * (10 - filled_o),
            "compressed_bar": "█" * filled_c + "░" * (10 - filled_c),
            "approx":         is_approx_model(model),
        }

    return results, compressed_text


def analyze_tokens_with_payload(file_paths: list[str], files_data: dict[str, dict]) -> tuple:
    """Like analyze_tokens_with_compression, but the "after" side is the actual
    aif.json payload an AI reads by default -- just the per-file summary, not
    the compressed body text.

    analyze_tokens_with_compression only measures how much compress_file() shrinks
    a file's body; it ignores that the final aif.json carries a summary instead
    of (not in addition to) that body. save_aif() splits `compressed` out to a
    sibling detail.json that isn't loaded up front -- fetching it on demand
    (e.g. via a future MCP tool, once a file's summary/relationships say it's
    worth a closer look) is future work, so it isn't part of what "reading
    aif.json" costs today. signatures/dependencies/api are pruned from the
    final output even earlier, in correct_aif() -- see corrector.py.

    files_data must already be fully populated (summaries included) -- call this
    after the LLM summary pass, using the same file_path keys as file_paths.

    A media asset (file/media.py) has no readable "original" text -- its
    contribution to original_text is correctly 0 -- but it does have a real
    `summary` that ships in aif.json exactly like any other file's, so it's
    still counted on the payload side unconditionally (not skipped the way
    original_text's own read_text()-is-None branch skips it). Missing this
    used to silently undercount the actual packed payload cli.py's
    `--max-tokens` CI guard checks against -- a project with several media
    assets could exceed its real token budget while the guard still passed.
    """
    original_parts = []
    payload_parts = []

    for file_path in file_paths:
        data = files_data.get(file_path, {})
        payload = {
            "summary": data.get("summary", ""),
        }
        payload_parts.append(f"\n### {file_path}\n{json.dumps(payload, ensure_ascii=False)}\n")

        content = read_text(file_path)
        if content is not None:
            original_parts.append(f"\n### {file_path}\n{content}\n")

    payload_text = "".join(payload_parts)

    results = {}
    for model, max_tokens in MODEL_MAX_TOKENS.items():
        original_count = _count_for_model(original_parts, model)
        payload_count = _count_for_model(payload_text, model)

        saved = original_count - payload_count
        saved_pct = round((saved / original_count) * 100, 1) if original_count > 0 else 0

        original_pct = (original_count / max_tokens) * 100
        payload_pct = (payload_count / max_tokens) * 100

        filled_o = int(original_pct / 10)
        filled_c = int(payload_pct / 10)

        results[model] = {
            "original":       original_count,
            "compressed":     payload_count,
            "saved":          saved,
            "saved_pct":      saved_pct,
            "max":            max_tokens,
            "original_bar":   "█" * filled_o + "░" * (10 - filled_o),
            "compressed_bar": "█" * filled_c + "░" * (10 - filled_c),
            "approx":         is_approx_model(model),
        }

    return results, payload_text