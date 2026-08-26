"""Guards the "supported packed-content languages" concept against drifting
out of sync with itself. Flagged by code review (2026-08-26): the set of
supported languages is duplicated across five independent dicts in two
modules (llm.LANGUAGE_NAMES, packager.STRUCTURAL_ONLY_NOTE/FORMAT_NOTES,
summarizer.SUMMARY_FAILED_PLACEHOLDERS/_STRUCTURAL_LABELS), each falling
back to "en" independently rather than sharing one canonical registry.
Consolidating them into a single source of truth is a larger refactor left
for later (or never, if it stays this small) -- this test is the cheap
mitigation in the meantime: `llm.LANGUAGE_NAMES` is the one dict that
actually gates CLI/GUI input, so adding a language there without updating
every sibling dict now fails a test immediately instead of silently
shipping English text for that language in some fixed strings and the
requested language in others.
"""

from ziplex import llm, packager, summarizer


def test_every_fixed_string_dict_covers_every_supported_language():
    supported = set(llm.LANGUAGE_NAMES)
    assert set(packager.STRUCTURAL_ONLY_NOTE) == supported
    assert set(packager.FORMAT_NOTES) == supported
    assert set(summarizer.SUMMARY_FAILED_PLACEHOLDERS) == supported
    assert set(summarizer._STRUCTURAL_LABELS) == supported


def test_structural_labels_have_the_same_three_keys_for_every_language():
    expected = {"defines", "references", "none"}
    for lang, labels in summarizer._STRUCTURAL_LABELS.items():
        assert set(labels) == expected, f"lang={lang!r} is missing/has extra structural-label keys"
