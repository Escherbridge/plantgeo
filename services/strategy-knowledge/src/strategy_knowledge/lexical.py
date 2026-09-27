"""Okapi BM25 lexical retrieval (Robertson & Zaragoza 2009) over one collection, rebuilt in-process on load.

Applies the same `FilterExpression` as the dense search, evaluated in Python; see AGENTS.md section "Lexical".
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


def tokenize(text: str) -> list[str]:
    """Lower-cased alphanumeric tokens without stopwords; units like 'tons/acre' become 'tons', 'acre'."""
    return [token for token in TOKEN_PATTERN.findall(text.lower()) if token not in STOPWORDS]


class LexicalIndex:
    """BM25 over a fixed list of documents with their metadata."""

    def __init__(self, ids: Sequence[str], texts: Sequence[str], metadatas: Sequence[Mapping[str, Any]]) -> None:
        self.ids = list(ids)
        self.metadatas = list(metadatas)
        tokenized = [tokenize(text) for text in texts]
        # rank_bm25 divides by the vocabulary size, so an empty or token-free corpus gets no model at all.
        self.model = BM25Okapi(tokenized) if any(tokenized) else None

    def search(self, query: str, expression: FilterExpression | None, limit: int) -> list[str]:
        """Ids of the best `limit` matching documents with a positive score, best first."""
        query_tokens = tokenize(query)
        if self.model is None or not query_tokens or limit <= 0:
            return []
        scores = self.model.get_scores(query_tokens)
        candidates = [
            (float(score), identifier)
            for identifier, score, metadata in zip(self.ids, scores, self.metadatas, strict=True)
            if score > 0 and matches(expression, metadata)
        ]
        candidates.sort(key=lambda pair: (-pair[0], pair[1]))
        return [identifier for _, identifier in candidates[:limit]]
