from ziplex.confidence import estimate_confidence, confidence_level, triage, REVIEW_THRESHOLD
from ziplex.summarizer import SUMMARY_FAILED_PLACEHOLDER


def test_estimate_confidence_high_when_summary_uses_signature_words():
    score = estimate_confidence(
        "Checks whether an item is a ruby tool.",
        ["isRubyTool(Item item)"],
    )
    assert score == 1.0


def test_estimate_confidence_matches_plural_and_verb_agreement_forms():
    # found via real Gemini output on testfiles/game-mod-project: a file
    # whose only signature is literally register() got a real summary
    # saying "...and registers configuration settings..." -- 0.0 before
    # stemming was added, a false positive from grammar alone, not an
    # actually questionable summary
    score = estimate_confidence(
        "Defines and registers configuration settings for the mod.",
        ["register()"],
    )
    assert score == 1.0


def test_estimate_confidence_low_when_summary_is_unrelated():
    score = estimate_confidence(
        "Configures the Gradle build settings for the mod.",
        ["isRubyTool(Item item)"],
    )
    assert score == 0.0


def test_estimate_confidence_defaults_to_high_with_no_signatures():
    # nothing to contradict -- e.g. README.md, mods.toml, other text-only files
    assert estimate_confidence("anything at all", []) == 1.0


def test_estimate_confidence_zero_for_failure_placeholder_even_with_no_signatures():
    # found via a real Gemini pack: a thin index.ts (top-level imports + one
    # bare call, no declared functions) has no signatures, so a failed
    # summary would otherwise fall into the "nothing to contradict" 1.0
    # shortcut above and never reach correct_aif()'s review -- exactly the
    # one case that fallback text needs to be caught, not skipped
    assert estimate_confidence(SUMMARY_FAILED_PLACEHOLDER, []) == 0.0


def test_estimate_confidence_full_credit_capped_at_three_matches():
    # a file with many functions shouldn't be penalized for a one-line
    # summary that abstracts over most of them instead of naming each one --
    # exact word forms on purpose (no "loads"/"saves"), since the tokenizer
    # does plain lowercasing, not stemming, and this test isolates the cap
    # behavior from that separate, known limitation
    signatures = ["load(Config c)", "save(Config c)", "validate(String s)", "render(Scene s)"]
    summary = "load save validate render everything"
    assert estimate_confidence(summary, signatures) == 1.0


def test_estimate_confidence_partial_overlap_is_between_zero_and_one():
    score = estimate_confidence(
        "Handles player interaction events.",
        ["onLivingDeath(LivingDeathEvent event)", "onPlayerInteract(PlayerInteractEvent event)"],
    )
    assert 0.0 < score < 1.0


def test_confidence_level_buckets():
    assert confidence_level(1.0) == "high"
    assert confidence_level(0.67) == "high"
    assert confidence_level(0.5) == "medium"
    assert confidence_level(REVIEW_THRESHOLD) == "medium"
    assert confidence_level(0.0) == "low"


def test_triage_splits_by_threshold_and_sorts_needs_review_ascending():
    files = {
        "a.py": {"confidence": 0.0},
        "b.py": {"confidence": 0.2},
        "c.py": {"confidence": 0.9},
        "d.py": {"confidence": 1.0},
    }
    needs_review, auto_kept = triage(files)
    assert needs_review == ["a.py", "b.py"]
    assert auto_kept == ["c.py", "d.py"]


def test_triage_treats_missing_confidence_as_fully_trusted():
    files = {"a.py": {"summary": "no confidence field at all"}}
    needs_review, auto_kept = triage(files)
    assert needs_review == []
    assert auto_kept == ["a.py"]
