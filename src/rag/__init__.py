"""RAG 層：語料載入與檢索。"""

from __future__ import annotations

import logging
from functools import lru_cache

from ..config import SETTINGS
from .ingest import load_corpus
from .store import BM25Retriever, Chunk, Hit, Retriever, tokenize

log = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def get_retriever() -> Retriever:
    """建立預設檢索器。Chroma 不可用時自動退回 BM25。"""
    chunks = load_corpus()

    if SETTINGS.retriever == "chroma":
        try:
            from .store import ChromaRetriever

            return ChromaRetriever(chunks)
        except ImportError:
            log.warning("未安裝 chromadb，退回 BM25。")
        except Exception as exc:  # pragma: no cover - 環境相關
            log.warning("Chroma 初始化失敗（%s），退回 BM25。", exc)

    return BM25Retriever(chunks)


__all__ = [
    "Chunk",
    "Hit",
    "Retriever",
    "BM25Retriever",
    "get_retriever",
    "load_corpus",
    "tokenize",
]
