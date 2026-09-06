"""LLM provider 層。"""

from __future__ import annotations

from ..config import SETTINGS, Settings
from .base import LLMClient, LLMResponse, ToolCall


def build_client(settings: Settings | None = None) -> LLMClient:
    """建立預設 provider。

    目前只有 Anthropic 一種實作。要新增 provider（例如 Gemini），
    實作 `base.LLMClient` 的兩個方法後在這裡分派即可，
    agent loop 完全不需要改動。
    """
    from .anthropic_client import AnthropicClient

    return AnthropicClient(settings)


__all__ = ["LLMClient", "LLMResponse", "ToolCall", "build_client", "SETTINGS"]
