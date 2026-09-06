"""Anthropic Claude provider。

實作重點：
- 使用 Claude Opus 5 與 adaptive thinking（Opus 5 預設即為 adaptive）。
- 開啟伺服器端 refusal fallback：安全分類器拒答時自動轉給備援模型，
  避免導覽對話在敏感年代話題上整段中斷。
- 平行工具呼叫的結果一律包在同一則 user 訊息裡回送。
"""

from __future__ import annotations

import logging
from typing import Any

import anthropic

from ..config import SETTINGS, Settings
from .base import LLMResponse, ToolCall

log = logging.getLogger(__name__)

_FALLBACK_BETA = "server-side-fallback-2026-07-01"


class AnthropicClient:
    """LLMClient 的 Anthropic 實作。"""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or SETTINGS
        # 零參數建構：SDK 會依序解析 ANTHROPIC_API_KEY / ANTHROPIC_AUTH_TOKEN /
        # `ant auth login` 建立的 profile，不要在程式碼裡寫死金鑰。
        self.client = anthropic.Anthropic()

    # ------------------------------------------------------------------ #

    def complete(
        self,
        *,
        system: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> LLMResponse:
        s = self.settings
        kwargs: dict[str, Any] = {
            "model": s.model,
            "max_tokens": s.max_tokens,
            "system": [
                {
                    "type": "text",
                    "text": system,
                    # system prompt 內容固定，快取後每輪省下重複計費。
                    # 快取是前綴比對，所以 system 不可含時間戳等變動內容。
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            "messages": messages,
            "tools": tools,
            "output_config": {"effort": s.effort},
        }

        try:
            if s.enable_refusal_fallback:
                resp = self.client.beta.messages.create(
                    betas=[_FALLBACK_BETA], fallbacks="default", **kwargs
                )
            else:
                resp = self.client.messages.create(**kwargs)
        except anthropic.BadRequestError as exc:
            # fallback beta 未開通時退回一般呼叫，不要讓 demo 整個掛掉。
            if s.enable_refusal_fallback:
                log.warning("refusal fallback 不可用，退回一般呼叫：%s", exc.message)
                s.enable_refusal_fallback = False
                resp = self.client.messages.create(**kwargs)
            else:
                raise

        return self._parse(resp)

    # ------------------------------------------------------------------ #

    @staticmethod
    def _parse(resp: Any) -> LLMResponse:
        stop_reason = getattr(resp, "stop_reason", "end_turn") or "end_turn"

        # 先看 stop_reason 再讀 content：被拒答時 content 不保證有東西。
        refusal = None
        if stop_reason == "refusal":
            details = getattr(resp, "stop_details", None)
            category = getattr(details, "category", None) if details else None
            refusal = f"模型拒絕回應（類別：{category or '未指定'}）"

        text_parts: list[str] = []
        tool_calls: list[ToolCall] = []
        for block in getattr(resp, "content", []) or []:
            btype = getattr(block, "type", None)
            if btype == "text":
                text_parts.append(block.text)
            elif btype == "tool_use":
                # tool_use.input 已是 dict，但絕不要對序列化後的字串做字串比對。
                tool_calls.append(
                    ToolCall(id=block.id, name=block.name, arguments=dict(block.input))
                )

        usage_obj = getattr(resp, "usage", None)
        usage = {}
        if usage_obj is not None:
            for key in (
                "input_tokens",
                "output_tokens",
                "cache_read_input_tokens",
                "cache_creation_input_tokens",
            ):
                value = getattr(usage_obj, key, None)
                if value is not None:
                    usage[key] = value

        return LLMResponse(
            text="".join(text_parts).strip(),
            tool_calls=tool_calls,
            stop_reason=stop_reason,
            raw_content=getattr(resp, "content", None),
            usage=usage,
            refusal=refusal,
        )

    # ------------------------------------------------------------------ #

    @staticmethod
    def format_tool_results(
        results: list[tuple[ToolCall, str, bool]]
    ) -> dict[str, Any]:
        blocks: list[dict[str, Any]] = []
        for call, output, is_error in results:
            block: dict[str, Any] = {
                "type": "tool_result",
                "tool_use_id": call.id,
                "content": output,
            }
            if is_error:
                # 失敗的工具也要回，不能整個略過，否則 tool_use 會沒有對應結果。
                block["is_error"] = True
            blocks.append(block)
        return {"role": "user", "content": blocks}
