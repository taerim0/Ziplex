from ziplex.tokenizer import count_tokens, analyze_tokens_with_compression, analyze_tokens_with_payload, MODEL_ENCODINGS


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
