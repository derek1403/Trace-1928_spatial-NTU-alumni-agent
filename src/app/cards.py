"""史料卡：把 Agent 本輪檢索到的語料原文與出處顯示在對話旁。

設計理由（2026-10-03，使用者決定）：人設只講他那個年代的事。
年代之後的事實 —— 例如 1970 年代的學生不可能知道 2000 年改電子鐘、
也不可能知道臺大今天的官方說法 —— 不從人設嘴裡說出，而是由史料卡
呈現原文與出處。這樣既守住年代（跨世代落差才成立），計畫書承諾的
「引用官方原文、如實說明出處待查」也由系統兌現。

不依賴 gradio，測試可直接呼叫。
"""

from __future__ import annotations

from typing import Any

EMPTY = "_這一輪沒有查詢史料。_"


def format_citations(citations: list[dict[str, Any]]) -> str:
    if not citations:
        return EMPTY
    cards: list[str] = []
    for c in citations:
        text = " ".join(str(c.get("text", "")).split())
        head = f"**{c.get('source') or '未標註來源'}**"
        if c.get("source_date"):
            head += f"｜擷取 {c['source_date']}"
        # 原樣顯示核對者。「已對照官方網頁」不等於「館員校對」，不能混為一談。
        if c.get("verified") and c.get("verified_by"):
            check = f"核對：{c['verified_by']}"
        elif c.get("verified"):
            check = "已核對（未註明核對者）"
        else:
            check = "⚠️ 尚未核對"
        cards.append(f"{head}\n\n> {text}\n\n<sub>{check}</sub>")
    return "\n\n---\n\n".join(cards)
