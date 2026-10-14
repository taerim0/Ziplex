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


def test_count_tokens_treats_special_token_text_as_ordinary_text():
    # tiktoken raises ValueError on "<|endoftext|>" by default -- after every
    # LLM call of a pack was already paid for.
    assert count_tokens("before <|endoftext|> after", "o200k_base") > 0
    assert count_tokens("before <|endoftext|> after", "cl100k_base") > 0


def test_unloadable_encoding_falls_back_to_the_char_estimate(tmp_path, monkeypatch):
    # tiktoken downloads an encoding on a cold cache; offline that raises.
    import tiktoken
    from ziplex import tokenizer

    def _offline(name):
        raise ConnectionError("no network")

    tokenizer._get_encoding.cache_clear()
    monkeypatch.setattr(tiktoken, "get_encoding", _offline)
    try:
        text = "hello world " * 10
        assert count_tokens(text, "o200k_base") == len(text) // tokenizer.APPROX_CHARS_PER_TOKEN
        assert is_approx_model("GPT-4o") is True

        file_path = tmp_path / "a.py"
        file_path.write_text("x = 1\n" * 20, encoding="utf-8")
        results, _ = analyze_tokens_with_payload([str(file_path)], {str(file_path): {"summary": "s"}})
        assert results["GPT-4o"]["approx"] is True
        assert results["GPT-4o"]["original"] == results["Claude"]["original"]
    finally:
        tokenizer._get_encoding.cache_clear()


def test_analyze_tokens_joins_files_in_order(tmp_path):
    from ziplex.tokenizer import analyze_tokens

    paths = []
    for i in range(3):
        p = tmp_path / f"f{i}.txt"
        p.write_text(f"body{i}", encoding="utf-8")
        paths.append(str(p))

    _, combined = analyze_tokens(paths)

    assert combined == "".join(f"\n### {p}\nbody{i}\n" for i, p in enumerate(paths))


def test_counting_a_list_of_parts_matches_the_joined_text_closely():
    from ziplex.tokenizer import count_tokens

    parts = [f"\n### f{i}.py\ndef f{i}(x):\n    return x * {i}\n" for i in range(200)]
    joined = count_tokens("".join(parts), "o200k_base")
    split = count_tokens(parts, "o200k_base")
    assert abs(split - joined) <= len(parts)
