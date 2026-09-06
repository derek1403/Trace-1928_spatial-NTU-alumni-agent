"""地標圖：黃金三角的 3 節點微型 Graph。

三個節點就足以驗證空間主動性，且鄰接矩陣小到可以直接硬編在資料檔裡，
不需要接任何地圖 API。這是刻意的範圍控制。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

from ..config import LANDMARK_DIR


@dataclass(frozen=True)
class Landmark:
    id: str
    name: str
    era_label: str
    persona_file: str
    corpus_tags: tuple[str, ...]
    qr_tokens: tuple[str, ...]
    is_default_entry: bool
    entry_note: str
    language_disclosure: str | None
    arrival_puzzle: dict[str, Any]
    facts_status: dict[str, Any]

    @property
    def facts_verified(self) -> bool:
        return bool(self.facts_status.get("verified"))


class LandmarkGraph:
    """地標與其步行鄰接關係。"""

    def __init__(self, payload: dict[str, Any]) -> None:
        self._landmarks: dict[str, Landmark] = {}
        self._qr_index: dict[str, str] = {}

        for raw in payload["landmarks"]:
            lm = Landmark(
                id=raw["id"],
                name=raw["name"],
                era_label=raw["era_label"],
                persona_file=raw["persona_file"],
                corpus_tags=tuple(raw.get("corpus_tags", [])),
                qr_tokens=tuple(raw.get("qr_tokens", [])),
                is_default_entry=bool(raw.get("is_default_entry")),
                entry_note=raw.get("entry_note", ""),
                language_disclosure=raw.get("language_disclosure"),
                arrival_puzzle=raw.get("arrival_puzzle", {}),
                facts_status=raw.get("facts_status", {}),
            )
            self._landmarks[lm.id] = lm
            for token in (*lm.qr_tokens, lm.id):
                self._qr_index[token.lower()] = lm.id

        # 無向圖，兩個方向都存一份，查詢時不必判斷順序。
        self._edges: dict[str, dict[str, float]] = {k: {} for k in self._landmarks}
        for edge in payload.get("edges", []):
            a, b, minutes = edge["a"], edge["b"], float(edge["walk_minutes"])
            if a in self._edges and b in self._edges:
                self._edges[a][b] = minutes
                self._edges[b][a] = minutes

    # ------------------------------------------------------------------ #

    def __contains__(self, landmark_id: object) -> bool:
        return landmark_id in self._landmarks

    def all(self) -> list[Landmark]:
        return list(self._landmarks.values())

    def get(self, landmark_id: str) -> Landmark | None:
        return self._landmarks.get(landmark_id)

    def default_entry(self) -> Landmark:
        for lm in self._landmarks.values():
            if lm.is_default_entry:
                return lm
        return next(iter(self._landmarks.values()))

    def resolve(self, token: str) -> Landmark | None:
        """由 QR token 或 landmark id 解析地標。"""
        if not token:
            return None
        landmark_id = self._qr_index.get(token.strip().lower())
        return self._landmarks.get(landmark_id) if landmark_id else None

    def neighbours(
        self, landmark_id: str, max_minutes: float | None = None
    ) -> list[tuple[Landmark, float]]:
        """回傳可達鄰居，依步行時間由近到遠排序。"""
        out: list[tuple[Landmark, float]] = []
        for other_id, minutes in self._edges.get(landmark_id, {}).items():
            if max_minutes is not None and minutes > max_minutes:
                continue
            other = self._landmarks.get(other_id)
            if other is not None:
                out.append((other, minutes))
        out.sort(key=lambda pair: pair[1])
        return out

    def walk_minutes(self, a: str, b: str) -> float | None:
        return self._edges.get(a, {}).get(b)


@lru_cache(maxsize=1)
def load_graph() -> LandmarkGraph:
    path = LANDMARK_DIR / "landmarks.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    return LandmarkGraph(payload)
