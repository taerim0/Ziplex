"""Heuristic confidence scoring for LLM-generated summaries, and using that
score to triage which ones actually need a human's attention.

There's no correctness verification anywhere in the pipeline today -- a
summary is trusted purely because a human reviewed it (correct_aif()), and
that review doesn't scale past a handful of files (nobody reads 500 one-line
summaries one by one). estimate_confidence() gives a free, no-LLM-call
signal by comparing a summary against the file's own extracted signatures
(ground truth from Tree-sitter, not LLM-guessed) -- if the summary's words
don't overlap at all with what the file's functions are actually named,
that's a real red flag. triage() then splits a project's files into "worth
reviewing" and "safe to auto-keep" by that score, so a human's limited
attention goes to the files most likely to be wrong instead of being spread
evenly (and thin) across everything.

Not a correctness guarantee: a summary can use different words for the same
concept and still be perfectly accurate, and this can't tell the
difference. It's a triage signal, not a substitute for reading the file.
"""

import re

from .summarizer import is_summary_failed_placeholder

# Below this, a file's summary gets flagged for review in correct_aif();
# at or above, it's auto-kept. Matches confidence_level()'s low/medium
# boundary, so "needs review" and "labeled low confidence" mean the same
# thing everywhere in the pipeline.
REVIEW_THRESHOLD = 0.34

_NON_WORD_RE = re.compile(r"[^A-Za-z0-9]+")
_CAMEL_SPLIT_RE = re.compile(r"[A-Z]+(?=[A-Z][a-z])|[A-Z]?[a-z]+|[A-Z]+|\d+")
_STOPWORDS = {
    "a", "an", "the", "is", "has", "get", "set", "of", "for",
    "and", "to", "in", "on", "this", "that", "with", "it",
}


def _stem(word: str) -> str:
    """Strips a single trailing "s" so "register"/"registers",
    "item"/"items" etc. count as the same word -- verified necessary
    against real Gemini output: "Defines and *registers* configuration..."
    for a file whose only extracted signature is literally `register()`
    scored 0.0 confidence without this, a false positive from grammatical
    agreement alone, not an actually questionable summary.

    Deliberately minimal: just the single most common mismatch, not real
    stemming (no "-ing"/"-ed"/"-ies" handling). Applied identically to both
    signature and summary tokens by _tokenize(), so correctness matters less
    than the two sides staying consistent with each other.
    """
    if len(word) > 3 and word.endswith("s") and not word.endswith("ss"):
        return word[:-1]
    return word


def _tokenize(text: str) -> set[str]:
    """Breaks a signature/summary string into lowercase word pieces: splits
    on non-alphanumeric characters first, then each resulting chunk by
    camelCase/PascalCase/SCREAMING_SNAKE boundaries (e.g.
    "isRubyTool(Item item)" -> {"is", "ruby", "tool", "item"}), then stems
    (see _stem()). Drops single-character pieces and common stopwords as
    too generic to carry any signal either way.
    """
    tokens = set()
    for chunk in _NON_WORD_RE.split(text):
        if not chunk:
            continue
        for word in _CAMEL_SPLIT_RE.findall(chunk):
            word = _stem(word.lower())
            if len(word) > 1 and word not in _STOPWORDS:
                tokens.add(word)
    return tokens


def estimate_confidence(summary: str, signatures: list[str]) -> float:
    """0.0-1.0: how much of the file's actual signature vocabulary shows up
    in its summary. 1.0 when there are no signatures to check against at
    all (nothing to contradict -- e.g. a README or config file) or when
    summary is empty and signatures is too.

    Full credit is capped at 3 matching words rather than requiring every
    signature word to appear -- a good one-line summary of a file with a
    dozen functions abstracts over most of them by design, and shouldn't
    read as "low confidence" just for not naming each one.

    Checked before the no-signatures shortcut below, not after: a file with
    no declared functions/classes (a thin index.ts that's just top-level
    imports and a call, for instance -- a real one hit in testing) has no
    source_tokens either, so generate_summaries()'s failure placeholder would
    otherwise get the same free 1.0 a legitimately trivial file gets,
    silently skipping the one review path (correct_aif(), see that
    placeholder's own docstring) that's actually supposed to catch it.
    """
    if is_summary_failed_placeholder(summary):
        return 0.0

    source_tokens = set()
    for sig in signatures:
        source_tokens |= _tokenize(sig)

    if not source_tokens:
        return 1.0

    summary_tokens = _tokenize(summary)
    overlap = source_tokens & summary_tokens
    expected = min(len(source_tokens), 3)
    return round(min(len(overlap), expected) / expected, 2)


def confidence_level(score: float) -> str:
    if score >= 0.67:
        return "high"
    if score >= REVIEW_THRESHOLD:
        return "medium"
    return "low"


def triage(files: dict, threshold: float = REVIEW_THRESHOLD) -> tuple[list[str], list[str]]:
    """Splits aif.json-shaped `files` (name -> {..., "confidence": float})
    into (needs_review, auto_kept) by each file's stored `confidence`
    (missing entries are treated as 1.0 -- nothing to flag). needs_review is
    sorted by confidence ascending, most suspicious first; auto_kept keeps
    encounter order, since nothing about it needs prioritizing.
    """
    scored = [(data.get("confidence", 1.0), name) for name, data in files.items()]
    needs_review = sorted(
        (score, name) for score, name in scored if score < threshold
    )
    auto_kept = [name for score, name in scored if score >= threshold]
    return [name for _, name in needs_review], auto_kept
