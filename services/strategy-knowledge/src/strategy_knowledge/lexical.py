"""Okapi BM25 lexical retrieval (Robertson & Zaragoza 2009) over one collection, rebuilt in-process on load.

Applies the same `FilterExpression` as the dense search, evaluated in Python; terms are lightly stemmed and a
query may carry weighted expansion tokens. See AGENTS.md section "Lexical".
"""

import re
from collections.abc import Mapping, Sequence
from typing import Any, Final

from rank_bm25 import BM25Okapi

from strategy_knowledge.filters import FilterExpression, matches

TOKEN_PATTERN: Final = re.compile(r"[a-z0-9]+")
STOPWORDS: Final = frozenset(
    {
        "a", "an", "and", "are", "as", "at", "be", "by", "for", "from", "has", "have", "in", "into", "is", "it",
        "its", "of", "on", "or", "that", "the", "their", "this", "to", "was", "were", "which", "with",
    },
)  # fmt: skip
#: Words the stemmer leaves alone: "burned" names a post-fire site state, "burn" a practice (AGENTS.md "Lexical").
UNSTEMMED_WORDS: Final = frozenset({"burned", "burnt"})
#: The shortest stem a suffix may leave behind, so "bed", "seed" and "used" are never cut to nothing.
MINIMUM_STEM_LENGTH: Final = 3
SINGULAR_EXEMPT_ENDINGS: Final = ("ss", "us", "is")
SIBILANT_PLURAL_ENDINGS: Final = ("ches", "shes", "sses", "xes", "zes")
DOUBLED_CONSONANTS_KEPT: Final = frozenset("lsz")
VOWELS: Final = frozenset("aeiouy")
#: Weight of an expansion token's BM25 contribution relative to a query token's.
DEFAULT_EXPANSION_WEIGHT: Final = 0.3


def tokenize(text: str) -> list[str]:
    """Lower-cased alphanumeric tokens without stopwords; units like 'tons/acre' become 'tons', 'acre'."""
    return [token for token in TOKEN_PATTERN.findall(text.lower()) if token not in STOPWORDS]


def _undouble(stem: str) -> str:
    """'cropp' -> 'crop' after a suffix is cut; 'till', 'grass' and 'buzz' keep their double letter."""
    if len(stem) > MINIMUM_STEM_LENGTH and stem[-1] == stem[-2] and stem[-1] not in VOWELS | DOUBLED_CONSONANTS_KEPT:
        return stem[:-1]
    return stem


def _has_vowel(text: str) -> bool:
    return any(character in VOWELS for character in text)


def stem(token: str) -> str:
    """Light suffix stripping for BM25 only: plurals, -ing, -ed and a final -e; not Porter, never a stem under 3."""
    if token in UNSTEMMED_WORDS or len(token) <= MINIMUM_STEM_LENGTH or token.isdigit():
        return token
    word = token
    if word.endswith("ies") and len(word) - 3 >= MINIMUM_STEM_LENGTH - 1:
        word = word[:-3] + "y"
    elif word.endswith(SIBILANT_PLURAL_ENDINGS):
        word = word[:-2]
    elif word.endswith("s") and not word.endswith(SINGULAR_EXEMPT_ENDINGS):
        word = word[:-1]
    if word.endswith("ied") and len(word) - 3 >= MINIMUM_STEM_LENGTH - 1:
        word = word[:-3] + "y"
    elif word.endswith("ing") and len(word) - 3 >= MINIMUM_STEM_LENGTH and _has_vowel(word[:-3]):
        word = _undouble(word[:-3])
    elif (
        word.endswith("ed")
        and not word.endswith("eed")
        and len(word) - 2 >= MINIMUM_STEM_LENGTH
        and _has_vowel(word[:-2])
    ):
        word = _undouble(word[:-2])
    if word.endswith("e") and len(word) > MINIMUM_STEM_LENGTH:
        word = word[:-1]
    return word


def lexical_terms(text: str) -> list[str]:
    """What BM25 indexes and queries: `tokenize` then `stem`."""
    return [stem(token) for token in tokenize(text)]


class LexicalIndex:
    """BM25 over a fixed list of documents with their metadata."""

    def __init__(self, ids: Sequence[str], texts: Sequence[str], metadatas: Sequence[Mapping[str, Any]]) -> None:
        self.ids = list(ids)
        self.metadatas = list(metadatas)
        tokenized = [lexical_terms(text) for text in texts]
        # rank_bm25 divides by the vocabulary size, so an empty or token-free corpus gets no model at all.
        self.model = BM25Okapi(tokenized) if any(tokenized) else None

    def search(
        self,
        query: str,
        expression: FilterExpression | None,
        limit: int,
        expansion_tokens: Sequence[str] = (),
        expansion_weight: float = DEFAULT_EXPANSION_WEIGHT,
    ) -> list[str]:
        """Ids of the best `limit` matching documents with a positive score, best first.

        BM25 is additive over query terms, so expansion tokens are scored as a second query and added at
        `expansion_weight`; a token already in the query is not counted twice.
        """
        query_terms = lexical_terms(query)
        expansion_terms = [term for term in lexical_terms(" ".join(expansion_tokens)) if term not in query_terms]
        if self.model is None or not (query_terms or expansion_terms) or limit <= 0:
            return []
        scores = self.model.get_scores(query_terms) if query_terms else [0.0] * len(self.ids)
        if expansion_terms and expansion_weight > 0:
            extra = self.model.get_scores(list(dict.fromkeys(expansion_terms)))
            scores = [float(base) + expansion_weight * float(added) for base, added in zip(scores, extra, strict=True)]
        candidates = [
            (float(score), identifier)
            for identifier, score, metadata in zip(self.ids, scores, self.metadatas, strict=True)
            if score > 0 and matches(expression, metadata)
        ]
        candidates.sort(key=lambda pair: (-pair[0], pair[1]))
        return [identifier for _, identifier in candidates[:limit]]
