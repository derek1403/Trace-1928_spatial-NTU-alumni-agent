"""訪客模型與跨地標交接狀態。

這是空間主動性的資料基礎：Agent 靜默累積興趣權重，據此決定要把
使用者導向哪一個地標，並在對方抵達時交接前一段的敘事脈絡。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

#: 興趣分類。刻意保持粗粒度 —— 分太細會讓權重稀釋，反而選不出主要興趣。
TOPICS = (
    "architecture",  # 建築、空間、materials
    "daily_life",  # 生活、宿舍、飲食、消費
    "academia",  # 課業、制度、學問
    "politics",  # 時代氛圍、社會事件（1970s 人設有額外約束）
    "people",  # 人物、師長、同儕
    "romance",  # 情感、交遊
    "food",  # 吃食（daily_life 的細分，出現頻率高故獨立）
)

TOPIC_LABELS = {
    "architecture": "建築與空間",
    "daily_life": "日常生活",
    "academia": "課業與制度",
    "politics": "時代氛圍",
    "people": "人物",
    "romance": "情感與交遊",
    "food": "飲食",
}


@dataclass
class ToolInvocation:
    """一次工具呼叫的紀錄。

    這就是自動埋點（原規格中的 `log_visitor_interest`）：由 loop 自動寫入，
    不開放模型呼叫。理由是評鑑指標 6 要統計「自主工具呼叫次數」，
    若讓模型自己呼叫一個記錄用工具，會把該指標灌水成無意義的數字。
    """

    name: str
    arguments: dict[str, Any]
    ok: bool
    duration_ms: float
    landmark: str
    turn: int
    summary: str = ""


@dataclass
class VisitorState:
    """單一 session 的訪客狀態。"""

    session_id: str
    current_landmark: str | None = None
    interests: dict[str, float] = field(default_factory=lambda: {t: 0.0 for t in TOPICS})
    #: 每個地標各自的對話輪數，決定何時該推播下一站。
    turns_at_landmark: dict[str, int] = field(default_factory=dict)
    visited: list[str] = field(default_factory=list)
    #: landmark_id -> 該段對話摘要，供下一站的 Agent 承接。
    handoff_notes: dict[str, str] = field(default_factory=dict)
    #: 已通過抵達驗證的地標。
    unlocked: set[str] = field(default_factory=set)
    trace: list[ToolInvocation] = field(default_factory=list)
    started_at: float = field(default_factory=time.time)
    #: 推薦過的下一站與是否真的走到，用於評鑑指標 5（推薦採納率）。
    suggestions: list[dict[str, Any]] = field(default_factory=list)
    #: 本輪產生、待前端顯示的媒體檔路徑；由 loop 取走後清空。
    pending_media: list[str] = field(default_factory=list)

    # ------------------------------------------------------------------ #

    @property
    def total_turns(self) -> int:
        return sum(self.turns_at_landmark.values())

    def arrive(self, landmark_id: str) -> None:
        """進入地標，並結算前一次推薦是否被採納。"""
        for suggestion in self.suggestions:
            if not suggestion["followed"] and suggestion["to"] == landmark_id:
                suggestion["followed"] = True
                break
        if self.current_landmark != landmark_id:
            self.current_landmark = landmark_id
            self.turns_at_landmark.setdefault(landmark_id, 0)
            if landmark_id not in self.visited:
                self.visited.append(landmark_id)

    def bump_turn(self) -> None:
        if self.current_landmark:
            self.turns_at_landmark[self.current_landmark] = (
                self.turns_at_landmark.get(self.current_landmark, 0) + 1
            )

    def add_interest(self, topic: str, weight: float = 1.0) -> None:
        if topic in self.interests:
            self.interests[topic] += weight

    def top_interests(self, n: int = 3) -> list[str]:
        ranked = sorted(self.interests.items(), key=lambda kv: kv[1], reverse=True)
        return [topic for topic, score in ranked[:n] if score > 0]

    def turns_here(self) -> int:
        if not self.current_landmark:
            return 0
        return self.turns_at_landmark.get(self.current_landmark, 0)

    def record_suggestion(self, to_landmark: str, reason: str) -> None:
        self.suggestions.append(
            {"from": self.current_landmark, "to": to_landmark, "reason": reason, "followed": False}
        )

    # ------------------------------------------------------------------ #

    def metrics(self) -> dict[str, Any]:
        """評鑑指標的原始數據。"""
        tool_calls = len(self.trace)
        turns = self.total_turns or 1
        followed = sum(1 for s in self.suggestions if s["followed"])
        return {
            "session_id": self.session_id,
            "total_turns": self.total_turns,
            "landmarks_visited": len(self.visited),
            "tool_calls": tool_calls,
            # 指標 6：每次會話的自主工具呼叫次數
            "tool_calls_per_turn": round(tool_calls / turns, 2),
            "distinct_tools": len({t.name for t in self.trace}),
            # 指標 5：下一地標推薦採納率
            "suggestions_made": len(self.suggestions),
            "suggestions_followed": followed,
            "suggestion_follow_rate": (
                round(followed / len(self.suggestions), 3) if self.suggestions else None
            ),
            "puzzles_solved": len(self.unlocked),
            "top_interests": self.top_interests(),
            "duration_sec": round(time.time() - self.started_at, 1),
        }
