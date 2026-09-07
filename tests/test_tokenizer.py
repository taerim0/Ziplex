from ziplex.tokenizer import (
    count_tokens,
    analyze_tokens_with_compression,
    analyze_tokens_with_payload,
    is_approx_model,
    MODEL_ENCODINGS,
    MODEL_MAX_TOKENS,
)


def test_count_tokens_is_monotonic_with_length():
    short = count_tokens("hello", "cl100k_base")
    long = count_tokens("hello " * 50, "cl100k_base")
    assert short > 0
    assert long > short


def test_analyze_tokens_with_compression_shrinks_a_compressible_file(tmp_path):
    file_path = tmp_path / "big.py"
    file_path.write_text(
        "def f():\n" + "    x = 1\n" * 50 + "    return x\n",
        encoding="utf-8",
    )

    results, _ = analyze_tokens_with_compression([str(file_path)])

    for model in MODEL_ENCODINGS:
        assert results[model]["compressed"] < results[model]["original"]
        assert results[model]["saved_pct"] > 0


def test_analyze_tokens_with_payload_counts_only_the_summary(tmp_path):
    file_path = tmp_path / "big.py"
    file_path.write_text("x = 1\n" * 200, encoding="utf-8")

    files_data = {
        str(file_path): {
            "summary": "A short summary.",
            # none of these should affect the count -- they're pruned before
            # aif.json is ever saved (see edits.finalize_aif / packager.save_aif)
            "signatures": ["def whatever()"],
            "dependencies": ["os"],
            "api": [],
            "compressed": "x = 1\n" * 200,
        }
    }

    results, payload_text = analyze_tokens_with_payload([str(file_path)], files_data)

    for model in MODEL_ENCODINGS:
        assert results[model]["original"] > results[model]["compressed"]
        assert results[model]["saved_pct"] > 0

    assert "A short summary." in payload_text
    assert "whatever" not in payload_text
    assert "x = 1" not in payload_text


def test_analyze_tokens_with_payload_still_counts_a_media_files_summary(tmp_path):
    # a media asset (file/media.py) has no readable "original" text -- it
    # correctly contributes 0 there -- but its real, shipped `summary` must
    # still be counted on the payload side, or the CI --max-tokens guard
    # silently undercounts the actual packed payload for any project with
    # media assets in it
    media_path = tmp_path / "logo.png"
    media_path.write_bytes(bytes(range(256)))  # not valid UTF-8

    files_data = {
        str(media_path): {"summary": "[image asset, 64x32, 24B]"},
    }

    results, payload_text = analyze_tokens_with_payload([str(media_path)], files_data)

    assert "[image asset, 64x32, 24B]" in payload_text
    for model in MODEL_ENCODINGS:
        # nothing readable as "original" text for a binary file, but the
        # payload side must still reflect the real summary that ships
        assert results[model]["original"] == 0
        assert results[model]["compressed"] > 0


def test_is_approx_model_flags_only_models_without_a_tiktoken_encoding():
    for model in MODEL_ENCODINGS:
        assert is_approx_model(model) is False
    for model in MODEL_MAX_TOKENS:
        if model not in MODEL_ENCODINGS:
            assert is_approx_model(model) is True


def test_analyze_tokens_with_compression_covers_claude_and_gemini_too(tmp_path):
    # Claude/Gemini have no public tiktoken encoding -- their count is a
    # character-based approximation (see APPROX_CHARS_PER_TOKEN), but they
    # must still show up in the results with the same shape as every
    # tiktoken-backed model, flagged as approximate rather than silently
    # omitted or presented as an exact count.
    file_path = tmp_path / "big.py"
    file_path.write_text(
        "def f():\n" + "    x = 1\n" * 50 + "    return x\n",
        encoding="utf-8",
    )

    results, _ = analyze_tokens_with_compression([str(file_path)])

    for model in ("Claude", "Gemini"):
        assert model in results
        assert results[model]["approx"] is True
        assert results[model]["compressed"] < results[model]["original"]
        assert results[model]["saved_pct"] > 0

    for model in MODEL_ENCODINGS:
        assert results[model]["approx"] is False
