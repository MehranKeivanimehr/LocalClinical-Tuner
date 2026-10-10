"""Text normalization and matching helpers shared by data filtering and evaluation."""

from __future__ import annotations

import re
import unicodedata

_PUNCT_RE = re.compile(r"[^\w\s/%.+-]")
_WS_RE = re.compile(r"\s+")
_SENT_RE = re.compile(r"(?<=[.!?])\s+|\n+")

STOPWORDS = frozenset(
    """
a an the of in on at to for from by with and or but is are was were be been being as that this these those
it its his her their your my our he she they we you i me him them not no
""".split()
)


def normalize(text: str) -> str:
    """Lowercase, unicode-normalize, drop most punctuation, collapse whitespace."""
    text = unicodedata.normalize("NFKC", text or "").lower()
    text = text.replace("’", "'").replace("–", "-").replace("—", "-")
    text = _PUNCT_RE.sub(" ", text)
    text = re.sub(r"(?<!\d)[.](?!\d)", " ", text)  # keep decimal points ("0.5 mg"), drop sentence periods
    return _WS_RE.sub(" ", text).strip()


def word_count(text: str) -> int:
    return len((text or "").split())


def sentences(text: str) -> list[str]:
    return [s.strip() for s in _SENT_RE.split(text or "") if s.strip()]


def contains_term(haystack_norm: str, term: str) -> bool:
    """Whole-word containment of a (normalized) term in an already-normalized haystack."""
    t = normalize(term)
    if not t:
        return False
    return re.search(rf"(?<![\w]){re.escape(t)}(?![\w])", haystack_norm) is not None


_SUFFIXES = ("ization", "ations", "ation", "nesses", "ness", "ments", "ment", "ities", "ity", "ings", "ing",
             "ied", "ies", "ed", "es", "ly", "s", "y", "e")


def stem(token: str) -> str:
    """Very small suffix-stripping stemmer (enough to match 'fussy'~'fussiness', 'swelling'~'swollen' no)."""
    for suf in _SUFFIXES:
        if token.endswith(suf) and len(token) - len(suf) >= 4:
            return token[: -len(suf)]
    return token


def stem_tokens(text: str) -> list[str]:
    return [stem(t) for t in normalize(text).split()]


def shingles(text: str, n: int = 3) -> set[tuple[str, ...]]:
    toks = normalize(text).split()
    return {tuple(toks[i : i + n]) for i in range(max(0, len(toks) - n + 1))}


def jaccard(a: set, b: set) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)
