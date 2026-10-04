"""Agent 主迴圈：plan → act → observe → replan。

刻意手寫迴圈而非使用 SDK 的 tool runner，理由有二：
1. provider 抽象層要能換掉 Anthropic，tool runner 綁死在該 SDK 上。
2. 評鑑指標 6 需要完整的工具呼叫鏈與耗時紀錄，手寫迴圈才拿得到。
"""

from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from ..config import AGENTS_DIR, SETTINGS, Settings
from ..llm import build_client
from ..llm.base import LLMClient, ToolCall
from ..spatial.landmarks import load_graph
from . import tools as tool_impl
from .schemas import TOOLS
from .visitor_state import ToolInvocation, VisitorState

log = logging.getLogger(__name__)

_MAX_TOOL_OUTPUT = 4000


@dataclass
class Turn:
    """一次使用者輸入的完整處理結果。"""

    text: str
    trace: list[ToolInvocation] = field(default_factory=list)
    media: list[str] = field(default_factory=list)
    #: 本輪的史料卡（檢索到的語料原文與出處），由介面顯示在對話旁。
    citations: list[dict[str, Any]] = field(default_factory=list)
    rounds: int = 0
    usage: dict[str, int] = field(default_factory=dict)
    refused: bool = False


def _read_prompt(filename: str) -> str:
    path = AGENTS_DIR / filename
    if not path.exists():
        log.warning("找不到提示詞檔案：%s", path)
        return ""
    return path.read_text(encoding="utf-8")


class Agent:
    def __init__(
        self, client: LLMClient | None = None, settings: Settings | None = None
    ) -> None:
        self.settings = settings or SETTINGS
        self.client = client or build_client(self.settings)
        self._base_prompt = _read_prompt("system_prompt.md")
        self._persona_cache: dict[str, str] = {}

    # ------------------------------------------------------------------ #

    def new_session(self, landmark_hint: str | None = None) -> VisitorState:
        state = VisitorState(session_id=uuid.uuid4().hex[:12])
        graph = load_graph()
        landmark = graph.resolve(landmark_hint) if landmark_hint else graph.default_entry()
        if landmark:
            # 只記錄起點，實際人設載入仍由模型呼叫 locate_landmark 完成，
            # 讓工具鏈從第一步就是完整的。
            state.current_landmark = landmark.id
        return state

    def _persona(self, landmark_id: str | None) -> str:
        if not landmark_id:
            return ""
        if landmark_id not in self._persona_cache:
            landmark = load_graph().get(landmark_id)
            self._persona_cache[landmark_id] = (
                _read_prompt(landmark.persona_file) if landmark else ""
            )
        return self._persona_cache[landmark_id]

    def _system(self, state: VisitorState) -> str:
        """組出 system prompt。

        內容必須是前綴穩定的 —— 不可放入時間戳、session id、輪數等
        每輪都會變的東西，否則 prompt cache 每輪失效。
        """
        parts = [self._base_prompt]
        persona = self._persona(state.current_landmark)
        if persona:
            parts.append("# 你目前的人設\n\n" + persona)
        return "\n\n---\n\n".join(p for p in parts if p)

    # ------------------------------------------------------------------ #

    def chat(
        self,
        state: VisitorState,
        user_text: str,
        history: list[dict[str, Any]],
        landmark: str | None = None,
    ) -> Turn:
        """處理一次使用者輸入。`history` 會被就地更新。

        `landmark`：使用者剛掃描的地標。與目前地標不同時，**在送出請求之前**
        就換人設，讓這一輪從第一個請求起就用新地標的 system prompt。
        若等模型呼叫 locate_landmark 才換，這一輪會用舊人設回答新地標
        （實測：角色錯亂、跳出角色解釋），下一輪還會因 system prompt 改變而 400。
        """
        if landmark and landmark != state.current_landmark:
            state.arrived_from = state.current_landmark
            state.arrive(landmark)

        # 人設換了 → 舊 history 的 thinking 區塊綁定的是舊 system prompt，
        # 沿用會被 API 以 400 拒絕。開新 history；跨站記憶靠 handoff_notes 傳遞。
        if history and state.prompt_landmark not in (None, state.current_landmark):
            log.info("人設已從 %s 換成 %s，開新 history", state.prompt_landmark, state.current_landmark)
            history.clear()
        state.prompt_landmark = state.current_landmark

        history.append({"role": "user", "content": user_text})
        state.bump_turn()

        turn = Turn(text="")
        system = self._system(state)

        for round_no in range(self.settings.max_tool_rounds):
            response = self.client.complete(
                system=system, messages=history, tools=TOOLS
            )
            turn.rounds = round_no + 1
            for key, value in response.usage.items():
                turn.usage[key] = turn.usage.get(key, 0) + value

            if response.stop_reason == "refusal":
                turn.refused = True
                turn.text = (
                    "（這個話題我不太方便講。我們聊點別的吧 —— "
                    "你剛剛問到的那件事，我倒是記得比較清楚。）"
                )
                log.warning("模型拒答：%s", response.refusal)
                state.take_citations()  # 丟棄，免得漏到下一輪
                return turn

            history.append({"role": "assistant", "content": response.raw_content})

            if not response.wants_tools:
                turn.text = response.text
                if response.stop_reason == "max_tokens":
                    log.warning("回應被 max_tokens 截斷")
                break

            results, invocations, media = self._run_tools(state, response.tool_calls)
            turn.trace.extend(invocations)
            turn.media.extend(media)
            state.trace.extend(invocations)
            # 平行呼叫的所有結果必須包在同一則訊息裡回送。
            history.append(self.client.format_tool_results(results))
        else:
            log.warning("達到工具往返上限 %d，強制收尾", self.settings.max_tool_rounds)
            turn.text = turn.text or "（我想想……剛才講到哪了？）"

        # 若模型在最後一輪只呼叫工具沒說話，補一句避免空白泡泡。
        if not turn.text.strip():
            turn.text = "（沉默了一下）你剛才問的，讓我想起一些事。"
        turn.citations = state.take_citations()
        return turn

    # ------------------------------------------------------------------ #

    def _run_tools(
        self, state: VisitorState, calls: list[ToolCall]
    ) -> tuple[list[tuple[ToolCall, str, bool]], list[ToolInvocation], list[str]]:
        results: list[tuple[ToolCall, str, bool]] = []
        invocations: list[ToolInvocation] = []
        media: list[str] = []

        for call in calls:
            started = time.perf_counter()
            try:
                output = tool_impl.execute(state, call.name, call.arguments)
                is_error = False
            except tool_impl.ToolError as exc:
                output, is_error = str(exc), True
            except TypeError as exc:
                # schema 與 handler 簽章不符：這是程式錯誤，但不要讓對話中斷。
                output, is_error = f"參數錯誤：{exc}", True
                log.exception("工具 %s 參數不符", call.name)
            except Exception as exc:  # pragma: no cover
                output, is_error = f"工具執行失敗：{exc}", True
                log.exception("工具 %s 未預期例外", call.name)

            elapsed_ms = (time.perf_counter() - started) * 1000
            if len(output) > _MAX_TOOL_OUTPUT:
                output = output[:_MAX_TOOL_OUTPUT] + "\n…（結果過長已截斷）"

            results.append((call, output, is_error))
            # 自動埋點：不開放模型呼叫，避免污染「自主工具呼叫次數」這項指標。
            invocations.append(
                ToolInvocation(
                    name=call.name,
                    arguments=call.arguments,
                    ok=not is_error,
                    duration_ms=round(elapsed_ms, 1),
                    landmark=state.current_landmark or "",
                    turn=state.total_turns,
                    summary=tool_impl.describe_arguments(call.name, call.arguments),
                )
            )

        media.extend(state.pending_media)
        state.pending_media.clear()
        return results, invocations, media
