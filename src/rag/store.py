"""檢索後端。

預設使用內建 BM25：零相依、離線可跑、啟動即用，對本專案的小型語料
（單一地標數十個 chunk）已經足夠。Chroma + 向量檢索列為可選升級路徑，
語料量長大或需要語意近似時再開啟。
"""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass
class Chunk:
    """語料的最小檢索單位。"""

    id: str
    text: str
    source: str = "未標註來源"
    source_date: str = ""
    landmark: str = ""
    era: str = ""
    #: 是否經圖書館／校史館館員校對。未校對者不得被當作已確認史實。
    verified: bool = False
    verified_by: str = ""
    meta: dict[str, Any] = field(default_factory=dict)

    def citation(self) -> str:
        bits = [self.source]
        if self.source_date:
            bits.append(self.source_date)
        bits.append("已校對" if self.verified else "未校對")
        return "｜".join(bits)


@dataclass
class Hit:
    chunk: Chunk
    score: float


# --------------------------------------------------------------------------- #
# 斷詞
# --------------------------------------------------------------------------- #

_CJK = r"一-鿿㐀-䶿"
_ASCII_WORD = re.compile(r"[a-zA-Z0-9]+")
_CJK_RUN = re.compile(f"[{_CJK}]+")


def tokenize(text: str) -> list[str]:
    """中英混合斷詞。

    中文不做詞典斷詞，改用**連續中文串內部**的二元組。避免引入 jieba
    這類額外相依，對專有名詞密集的校史語料召回也比詞典斷詞穩定。

    兩個刻意的細節：

    1. **bigram 只在連續中文串內組合**，不跨越數字或標點。否則
       「傅鐘敲 21 響」會生出「敲響」這種橫跨數字的假詞。
    2. **不索引單一中文字**（除非整串就只有一個字）。單字的鑑別力太低 ——
       「量子色動力學」會因為共用「子」「動」「力」「學」而命中校史語料。
    """
    tokens: list[str] = [m.group().lower() for m in _ASCII_WORD.finditer(text)]
    for run in _CJK_RUN.findall(text):
        if len(run) == 1:
            tokens.append(run)
            continue
        tokens.extend(run[i : i + 2] for i in range(len(run) - 1))
    return tokens


# --------------------------------------------------------------------------- #
# 檢索介面
# --------------------------------------------------------------------------- #


class Retriever(Protocol):
    def search(
        self, query: str, *, top_k: int = 5, landmark: str | None = None
    ) -> list[Hit]: ...

    def __len__(self) -> int: ...


class BM25Retriever:
    """Okapi BM25。純 Python，無外部相依。"""

    def __init__(self, chunks: list[Chunk], *, k1: float = 1.5, b: float = 0.75) -> None:
        self.chunks = chunks
        self.k1 = k1
        self.b = b

        self._tokens: list[Counter[str]] = []
        self._lengths: list[int] = []
        doc_freq: Counter[str] = Counter()

        for chunk in chunks:
            counts = Counter(tokenize(chunk.text))
            self._tokens.append(counts)
            self._lengths.append(sum(counts.values()))
            doc_freq.update(counts.keys())

        n = max(len(chunks), 1)
        self._avg_len = (sum(self._lengths) / n) if self._lengths else 1.0
        # BM25 的 IDF，加 0.5 平滑避免高頻詞得到負分。
        self._idf = {
            term: math.log(1 + (n - df + 0.5) / (df + 0.5)) for term, df in doc_freq.items()
        }

    def __len__(self) -> int:
        return len(self.chunks)

    def search(
        self, query: str, *, top_k: int = 5, landmark: str | None = None
    ) -> list[Hit]:
        q_terms = tokenize(query)
        if not q_terms or not self.chunks:
            return []

        # 精準度閘門：BM25 只看詞頻，一段話可能靠零星共用的高權重詞拿到分數，
        # 實際上跟查詢無關。要求查詢詞至少有一個字面出現在原文中，
        # 才算真的命中。寧可少給，不要給錯 —— 這是校史 Agent，
        # 檢索到不相關的東西比檢索不到更危險。
        meaningful = {t for t in q_terms if len(t) >= 2}

        scored: list[Hit] = []
        for idx, chunk in enumerate(self.chunks):
            if landmark and chunk.landmark and chunk.landmark != landmark:
                continue
            if meaningful and not any(term in chunk.text for term in meaningful):
                continue

            counts = self._tokens[idx]
            length = self._lengths[idx] or 1
            score = 0.0
            for term in q_terms:
                tf = counts.get(term)
                if not tf:
                    continue
                idf = self._idf.get(term, 0.0)
                denom = tf + self.k1 * (1 - self.b + self.b * length / self._avg_len)
                score += idf * (tf * (self.k1 + 1)) / denom
            if score > 0:
                scored.append(Hit(chunk=chunk, score=score))

        scored.sort(key=lambda h: h.score, reverse=True)
        return scored[:top_k]


class ChromaRetriever:
    """ChromaDB 向量檢索（可選）。

    語料量變大、或需要語意近似而非字面比對時使用。
    需 `pip install chromadb`；未安裝時 `build_retriever` 會自動退回 BM25。
    """

    def __init__(self, chunks: list[Chunk], *, persist_dir: str | None = None) -> None:
        import chromadb  # 延遲匯入：未安裝時不影響預設路徑

        self.chunks = {c.id: c for c in chunks}
        client = (
            chromadb.PersistentClient(path=persist_dir)
            if persist_dir
            else chromadb.EphemeralClient()
        )
        self.collection = client.get_or_create_collection("trace1928")

        if chunks:
            self.collection.upsert(
                ids=[c.id for c in chunks],
                documents=[c.text for c in chunks],
                metadatas=[
                    {"landmark": c.landmark, "source": c.source, "verified": c.verified}
                    for c in chunks
                ],
            )

    def __len__(self) -> int:
        return len(self.chunks)

    def search(
        self, query: str, *, top_k: int = 5, landmark: str | None = None
    ) -> list[Hit]:
        if not self.chunks:
            return []
        result = self.collection.query(
            query_texts=[query],
            n_results=min(top_k, len(self.chunks)),
            where={"landmark": landmark} if landmark else None,
        )
        ids = (result.get("ids") or [[]])[0]
        distances = (result.get("distances") or [[]])[0]
        hits: list[Hit] = []
        for chunk_id, dist in zip(ids, distances):
            chunk = self.chunks.get(chunk_id)
            if chunk is not None:
                # 距離轉相似度，讓分數方向與 BM25 一致（愈大愈相關）。
                hits.append(Hit(chunk=chunk, score=1.0 / (1.0 + float(dist))))
        return hits
