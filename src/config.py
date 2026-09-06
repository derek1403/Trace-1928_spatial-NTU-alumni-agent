"""全域設定。所有可調參數集中在這裡，避免散落在各模組。"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

DATA_DIR = ROOT / "data"
CORPUS_DIR = DATA_DIR / "corpus"
ARCHIVE_DIR = DATA_DIR / "archive"
LANDMARK_DIR = DATA_DIR / "landmarks"
AGENTS_DIR = ROOT / "agents"
OUTPUT_DIR = ROOT / "outputs"
SESSION_DIR = ROOT / "sessions"


def _env(name: str, default: str) -> str:
    return os.environ.get(f"TRACE1928_{name}", default)


@dataclass
class Settings:
    """執行期設定。以環境變數 TRACE1928_* 覆寫。"""

    # --- LLM ---
    model: str = field(default_factory=lambda: _env("MODEL", "claude-opus-5"))
    max_tokens: int = field(default_factory=lambda: int(_env("MAX_TOKENS", "8000")))
    # low | medium | high | xhigh | max
    # 對話式導覽以延遲為重，預設 medium；做 golden QA 評測時可調高。
    effort: str = field(default_factory=lambda: _env("EFFORT", "medium"))
    # Claude Opus 5 的伺服器端 refusal fallback，避免安全分類器拒答時整段對話中斷。
    enable_refusal_fallback: bool = field(
        default_factory=lambda: _env("REFUSAL_FALLBACK", "1") == "1"
    )

    # --- Agent loop ---
    # 單一使用者輸入最多允許幾輪 tool 往返，防止無限迴圈。
    max_tool_rounds: int = field(default_factory=lambda: int(_env("MAX_TOOL_ROUNDS", "8")))

    # --- RAG ---
    # bm25（零相依，預設）| chroma（需安裝 chromadb）
    retriever: str = field(default_factory=lambda: _env("RETRIEVER", "bm25"))
    top_k: int = field(default_factory=lambda: int(_env("TOP_K", "5")))

    # --- 動態影像 ---
    # auto = 依序嘗試 commercial_api -> liveportrait -> kenburns_2_5d -> kenburns
    animate_backend: str = field(default_factory=lambda: _env("ANIMATE_BACKEND", "auto"))
    animate_duration_sec: float = field(
        default_factory=lambda: float(_env("ANIMATE_DURATION", "4.0"))
    )

    # --- 空間 ---
    # 超過這個步行分鐘數就不推薦，對應 calculate_distance 的篩選條件。
    max_walk_minutes: float = field(default_factory=lambda: float(_env("MAX_WALK_MIN", "10")))
    # 對話輪數達到這個值之後，Agent 才會考慮推播下一個地標。
    cliffhanger_after_turns: int = field(
        default_factory=lambda: int(_env("CLIFFHANGER_AFTER", "6"))
    )


SETTINGS = Settings()
