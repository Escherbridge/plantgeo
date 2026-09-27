"""The injectable text-embedding seam: production MiniLM, or any `TextEmbedder` a test supplies.

The service embeds text itself and hands Chroma vectors, so no embedding function is persisted in a
collection's configuration; see AGENTS.md section "Embedding".
"""

from collections.abc import Sequence
from typing import Final, Protocol

from chromadb.utils.embedding_functions.onnx_mini_lm_l6_v2 import ONNXMiniLM_L6_V2

from strategy_knowledge.config import PRODUCTION_EMBEDDING_MODEL

EMBEDDING_BATCH_SIZE: Final = 64


class TextEmbedder(Protocol):
    """Anything that turns texts into fixed-length vectors and names its model."""

    @property
    def model_name(self) -> str:
        """The name recorded in collection metadata and compared on open."""
        ...

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        """One vector per text, in order."""
        ...


class MiniLmEmbedder:
    """Chroma's bundled all-MiniLM-L6-v2 ONNX model (384-d); the model downloads on first use, not on import."""

    def __init__(self) -> None:
        self._function: ONNXMiniLM_L6_V2 | None = None

    @property
    def model_name(self) -> str:
        """The production model name."""
        return PRODUCTION_EMBEDDING_MODEL

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        """Embed in batches of 64; the function returns L2-normalised numpy rows."""
        if self._function is None:
            self._function = ONNXMiniLM_L6_V2()
        vectors: list[list[float]] = []
        for first in range(0, len(texts), EMBEDDING_BATCH_SIZE):
            batch = list(texts[first : first + EMBEDDING_BATCH_SIZE])
            vectors.extend([float(value) for value in row] for row in self._function(batch))
        return vectors


class UnknownEmbeddingModelError(ValueError):
    """Raised when settings name an embedding model this service cannot construct."""


def embedder_for(model_name: str) -> TextEmbedder:
    """The embedder a configured model name selects."""
    if model_name == PRODUCTION_EMBEDDING_MODEL:
        return MiniLmEmbedder()
    raise UnknownEmbeddingModelError(f"no embedder for model {model_name!r}; only {PRODUCTION_EMBEDDING_MODEL!r}")
