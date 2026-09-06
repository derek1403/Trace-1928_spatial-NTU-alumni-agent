"""Gradio 介面。

除了對話本身，介面刻意把**工具呼叫軌跡**攤開顯示。
這不只是除錯面板 —— 它是「這不是 Chatbot」最直接的證據：
使用者能親眼看到 Agent 自己決定去檢索、去驗證、去規劃路線。
計畫書的 demo 截圖就是這一塊。
"""

from __future__ import annotations

import logging

import gradio as gr

from ..agent import Agent, VisitorState
from ..agent.visitor_state import TOPIC_LABELS
from ..config import SETTINGS
from ..rag import get_retriever
from ..spatial.landmarks import load_graph

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
)
log = logging.getLogger(__name__)

CSS = """
.trace-panel { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 12px; }
footer { display: none !important; }
"""


def _landmark_choices() -> list[tuple[str, str]]:
    return [(f"{lm.name}｜{lm.era_label}", lm.id) for lm in load_graph().all()]


def _format_trace(state: VisitorState, limit: int = 12) -> str:
    if not state.trace:
        return "_尚未有工具呼叫。_"
    rows = ["| # | 工具 | 參數 | 耗時 | |", "|---|---|---|---|---|"]
    for i, inv in enumerate(state.trace[-limit:], 1):
        mark = "✅" if inv.ok else "⚠️"
        summary = inv.summary.replace("|", "\\|")[:40]
        rows.append(f"| {i} | `{inv.name}` | {summary} | {inv.duration_ms:.0f}ms | {mark} |")
    return "\n".join(rows)


def _format_metrics(state: VisitorState) -> str:
    m = state.metrics()
    interests = (
        "、".join(TOPIC_LABELS.get(t, t) for t in m["top_interests"]) or "尚未成形"
    )
    rate = m["suggestion_follow_rate"]
    rate_text = f"{rate:.0%}" if rate is not None else "尚未推薦"
    return (
        f"**對話輪數** {m['total_turns']}　**造訪地標** {m['landmarks_visited']}\n\n"
        f"**工具呼叫** {m['tool_calls']} 次（每輪 {m['tool_calls_per_turn']}）"
        f"　**用到幾種工具** {m['distinct_tools']}/8\n\n"
        f"**訪客興趣模型** {interests}\n\n"
        f"**下一站推薦採納率** {rate_text}"
        f"（推薦 {m['suggestions_made']}、走到 {m['suggestions_followed']}）"
    )


def _startup_warnings() -> str:
    notes: list[str] = []
    if len(get_retriever()) == 0:
        notes.append(
            "⚠️ **語料庫是空的**（`data/corpus/` 尚無史料）。"
            "Agent 目前無法查證任何史實，會對所有具體問題回答「沒印象」—— "
            "這是預期行為，不是壞掉。"
        )
    unverified = [lm.name for lm in load_graph().all() if not lm.facts_verified]
    if unverified:
        notes.append(f"⚠️ 年代錨點尚未查證的地標：{'、'.join(unverified)}")
    return "\n\n".join(notes)


def build_ui() -> gr.Blocks:
    agent = Agent()

    with gr.Blocks(title="尋跡 1928", css=CSS, theme=gr.themes.Soft()) as demo:
        gr.Markdown(
            "# 尋跡 1928\n"
            "### 老校友帶你走讀台大歷史的空間感知 Agent\n"
            "選一個地標（模擬掃描該地的 QR Code），"
            "然後跟那個年代的學生說話。他會記得你問過什麼，"
            "並且在適當的時候請你走去下一個地方。"
        )

        warnings = _startup_warnings()
        if warnings:
            gr.Markdown(warnings)

        session = gr.State()
        history = gr.State([])

        with gr.Row():
            with gr.Column(scale=3):
                landmark = gr.Radio(
                    choices=_landmark_choices(),
                    value=load_graph().default_entry().id,
                    label="你現在站在哪裡（模擬 QR Code 掃描）",
                )
                chat = gr.Chatbot(height=440, type="messages", label="對話")
                with gr.Row():
                    box = gr.Textbox(
                        placeholder="跟他說點什麼……例如：這個鐘為什麼要敲那麼多下？",
                        show_label=False,
                        scale=5,
                        autofocus=True,
                    )
                    send = gr.Button("送出", variant="primary", scale=1)
                media = gr.Video(label="動起來的老照片", visible=False, autoplay=True, loop=True)

            with gr.Column(scale=2):
                gr.Markdown("### Agent 做了什麼")
                gr.Markdown(
                    "_下面每一列都是 Agent **自己決定**要呼叫的工具，"
                    "沒有任何按鈕觸發。_"
                )
                trace = gr.Markdown("_尚未有工具呼叫。_", elem_classes=["trace-panel"])
                gr.Markdown("### 即時指標")
                metrics = gr.Markdown("_尚未開始。_")
                reset = gr.Button("重新開始一段導覽")

        # ---------------- 事件 ---------------- #

        def on_landmark_change(landmark_id, state):
            """切換地標＝走到新地點掃碼。刻意保留 state，交接記憶才會生效。"""
            if state is None:
                state = agent.new_session(landmark_id)
            return state, [], []

        def respond(user_text, state, hist, landmark_id):
            if not (user_text or "").strip():
                return state, hist, gr.update(), gr.update(), gr.update(), ""

            if state is None:
                state = agent.new_session(landmark_id)

            # 使用者切到別的地標時，把地標代碼一併給模型，
            # 讓它自己呼叫 locate_landmark 完成人設切換與記憶交接。
            prompt = user_text
            if landmark_id and landmark_id != state.current_landmark:
                prompt = f"[系統：使用者剛掃描了 {landmark_id} 的 QR Code]\n{user_text}"

            try:
                turn = agent.chat(state, prompt, hist)
                reply = turn.text
            except Exception as exc:  # pragma: no cover - 前端不該因後端例外而白畫面
                log.exception("對話失敗")
                reply = f"（連線好像斷了……）\n\n`{type(exc).__name__}: {exc}`"
                turn = None

            display = _rebuild_display(hist)

            video_update = gr.update(visible=False)
            if turn and turn.media:
                video_update = gr.update(value=turn.media[-1], visible=True)

            return (
                state,
                hist,
                display,
                _format_trace(state),
                _format_metrics(state),
                video_update,
            )

        def do_reset():
            return None, [], [], "_尚未有工具呼叫。_", "_尚未開始。_", gr.update(visible=False)

        landmark.change(
            on_landmark_change, [landmark, session], [session, history, chat]
        )

        outputs = [session, history, chat, trace, metrics, media]
        send.click(respond, [box, session, history, landmark], outputs).then(
            lambda: "", None, box
        )
        box.submit(respond, [box, session, history, landmark], outputs).then(
            lambda: "", None, box
        )
        reset.click(do_reset, None, outputs)

    return demo


def _rebuild_display(history: list[dict]) -> list[dict]:
    """把內部對話歷史轉成 Chatbot 能顯示的格式。

    內部歷史含 tool_use / tool_result 區塊，那些不該顯示給使用者 ——
    它們在右側的軌跡面板另外呈現。
    """
    out: list[dict] = []
    for msg in history:
        content = msg.get("content")
        if isinstance(content, str):
            text = content
            # 系統注入的地標切換提示不顯示給使用者
            if text.startswith("[系統："):
                text = text.split("\n", 1)[-1]
            if text.strip():
                out.append({"role": msg["role"], "content": text})
            continue
        if msg["role"] != "assistant" or not content:
            continue
        parts = [
            getattr(block, "text", "")
            for block in content
            if getattr(block, "type", None) == "text"
        ]
        joined = "".join(parts).strip()
        if joined:
            out.append({"role": "assistant", "content": joined})
    return out


def main() -> None:
    log.info(
        "模型 %s｜effort %s｜檢索器 %s｜動畫後端 %s",
        SETTINGS.model,
        SETTINGS.effort,
        SETTINGS.retriever,
        SETTINGS.animate_backend,
    )
    build_ui().launch(inbrowser=True)


if __name__ == "__main__":
    main()
