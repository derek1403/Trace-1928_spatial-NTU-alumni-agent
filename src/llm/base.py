"""LLM provider 抽象層。

專案主力使用 Anthropic Claude；本層存在的目的是讓之後要換 provider
（例如取得 Gemini 額度時）只需新增一個 adapter，不必動 agent loop。

刻意保持極薄：只抽象「送出一輪帶工具的請求，拿回文字與工具呼叫」。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass
class ToolCall:
    """模型要求呼叫某個工具。"""

    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class LLMResponse:
    """一輪模型回應。"""

    text: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    #: end_turn | tool_use | max_tokens | refusal | pause_turn
    stop_reason: str = "end_turn"
    #: provider 原生的 assistant content，回填對話歷史時原封不動送回。
    raw_content: Any = None
    usage: dict[str, int] = field(default_factory=dict)
    #: 被安全分類器拒答時的說明，其餘情況為 None。
    refusal: str | None = None

    @property
    def wants_tools(self) -> bool:
        return bool(self.tool_calls)


class LLMClient(Protocol):
    """provider 需實作的介面。"""

    def complete(
        self,
        *,
        system: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> LLMResponse:
        """送出一輪請求。`messages` 為 provider 原生格式的對話歷史。"""
        ...

    def format_tool_results(
        self, results: list[tuple[ToolCall, str, bool]]
    ) -> dict[str, Any]:
        """把工具執行結果打包成一則可加入 `messages` 的訊息。

        `results` 的每一項為 (呼叫, 結果字串, 是否為錯誤)。

        注意：平行工具呼叫的所有結果必須包在**同一則**訊息裡送回，
        分成多則會讓模型逐漸停止平行呼叫。
        """
        ...
