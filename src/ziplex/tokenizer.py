import json

import tiktoken

from .extract.code.compressor import compress_file
from .file.textutil import read_text

MODEL_ENCODINGS = {
    "GPT-4o":  "o200k_base",
    "GPT-3.5": "cl100k_base",
    "GPT-4":   "cl100k_base",
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
    "GPT-3.5": 16_000,
    "GPT-4":   128_000,
    "Claude":  200_000,
    "Gemini":  1_000_000,
}

def count_tokens(text: str, encoding_name: str) -> int:
    enc = tiktoken.get_encoding(encoding_name)
    return len(enc.encode(text))


def is_approx_model(model: str) -> bool:
    """True for a model in MODEL_MAX_TOKENS with no tiktoken encoding of its
    own (Claude, Gemini) -- its token count is a character-based estimate,
    not an exact one. Exposed so a caller (cli.py's printer, a future GUI
    label) can mark an approximate figure as such instead of presenting it
    with the same confidence as a real tiktoken count.
    """
    return model not in MODEL_ENCODINGS


def _count_for_model(text: str, model: str) -> int:
    """Token count for `model` -- an exact tiktoken count when one of its
    own encodings exists (MODEL_ENCODINGS), otherwise APPROX_CHARS_PER_TOKEN
    applied to `text`'s length (see that constant's own comment for why)."""
    encoding = MODEL_ENCODINGS.get(model)
    if encoding is not None:
        return count_tokens(text, encoding)
    return len(text) // APPROX_CHARS_PER_TOKEN


def analyze_tokens(file_paths: list[str]) -> tuple:
    combined = ""
    for file_path in file_paths:
        content = read_text(file_path)
        if content is None:
            continue
        combined += f"\n### {file_path}\n{content}\n"

    results = {}
    for model, max_tokens in MODEL_MAX_TOKENS.items():
        token_count = _count_for_model(combined, model)
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
    original_text = ""
    compressed_text = ""

    for file_path in file_paths:
        content = read_text(file_path)
        if content is None:
            continue
        original_text += f"\n### {file_path}\n{content}\n"

        compressed = compress_file(file_path)
        compressed_text += f"\n### {file_path}\n{compressed}\n"

    results = {}
    for model, max_tokens in MODEL_MAX_TOKENS.items():
        original_count = _count_for_model(original_text, model)
        compressed_count = _count_for_model(compressed_text, model)

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
    original_text = ""
    payload_text = ""

    for file_path in file_paths:
        data = files_data.get(file_path, {})
        payload = {
            "summary": data.get("summary", ""),
        }
        payload_text += f"\n### {file_path}\n{json.dumps(payload, ensure_ascii=False)}\n"

        content = read_text(file_path)
        if content is not None:
            original_text += f"\n### {file_path}\n{content}\n"

    results = {}
    for model, max_tokens in MODEL_MAX_TOKENS.items():
        original_count = _count_for_model(original_text, model)
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