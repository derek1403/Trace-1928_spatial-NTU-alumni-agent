"""真實 API 對話測試：跑一段腳本化的對話，存成逐字稿，並估算花費。需要 API 金鑰。

    PYTHONUTF8=1 py eval/live_session.py                     # 預設腳本（傅鐘 → 舊總圖）
    PYTHONUTF8=1 py eval/live_session.py --max-usd 0.8       # 估計花費超過就停

與 run_eval.py 的差別：這裡測的是**接口與多輪行為**（工具迴圈、人設切換、
史料卡、記憶交接），而不是逐題評分。逐字稿存在 eval/live/，供除錯與心得引用。

切換地標時比照介面：把 landmark 交給 Agent.chat，由它在送出請求前換人設、
開新 history（system prompt 隨人設改變，沿用舊 history 會被 API 以 400 拒絕）。

花費是用下列單價**估算**的；實際扣款以 Anthropic Console 的 Usage 頁為準。
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.agent import Agent  # noqa: E402
from src.config import SETTINGS  # noqa: E402

#: USD / 百萬 token（Opus 5.5 定價；快取寫入 1.25 倍、讀取 0.1 倍）。
PRICE_IN = 4.0
PRICE_OUT = 20.0
PRICE_CACHE_WRITE = PRICE_IN * 1.25
PRICE_CACHE_READ = PRICE_IN * 0.1

#: (地標, 使用者輸入)。地標與上一句不同時視為「走到新地點掃碼」。
DEFAULT_SCRIPT: list[tuple[str, str]] = [
    ("fuzhong", "（打個招呼）"),
    ("fuzhong", "鐘旁邊那塊碑寫「一天只有 21 小時，剩下 3 小時是用來思考的」，這是傅校長說的嗎？"),
    ("fuzhong", "你剛剛有在記錄我什麼嗎？"),
    ("zongtu", "我走到舊總圖了。這棟樓是哪一年蓋好的？"),
    ("zongtu", "你常在校史館讀書嗎？"),
]


def cost_usd(usage: dict[str, int]) -> float:
    return (
        usage.get("input_tokens", 0) * PRICE_IN
        + usage.get("output_tokens", 0) * PRICE_OUT
        + usage.get("cache_creation_input_tokens", 0) * PRICE_CACHE_WRITE
        + usage.get("cache_read_input_tokens", 0) * PRICE_CACHE_READ
    ) / 1_000_000


def run(script: list[tuple[str, str]], max_usd: float) -> dict:
    started_at = datetime.now().isoformat(timespec="seconds")
    agent = Agent()
    state = agent.new_session(script[0][0])
    history: list = []
    current = None
    turns = []
    total: dict[str, int] = {}
    spent = 0.0

    for landmark, text in script:
        prompt = text
        if landmark != current:
            prompt = f"[系統：使用者剛掃描了 {landmark} 的 QR Code]\n{text}"
            current = landmark

        started = time.perf_counter()
        error = None
        try:
            turn = agent.chat(state, prompt, history, landmark=landmark)
        except Exception as exc:  # 接口測試要的就是把錯誤原樣記下來
            error = f"{type(exc).__name__}: {exc}"
            turn = None
        elapsed = time.perf_counter() - started

        record = {"landmark": landmark, "user": prompt, "seconds": round(elapsed, 1)}
        if turn is None:
            record["error"] = error
            turns.append(record)
            print(f"\n❌ [{landmark}] {text}\n   {error}")
            break

        usage = dict(turn.usage)
        for k, v in usage.items():
            total[k] = total.get(k, 0) + v
        spent += cost_usd(usage)
        record.update(
            {
                "reply": turn.text,
                "tools": [
                    {"name": t.name, "summary": t.summary, "ok": t.ok, "ms": t.duration_ms}
                    for t in turn.trace
                ],
                "citations": [
                    {"id": c["id"], "source": c.get("source", ""), "verified_by": c.get("verified_by", "")}
                    for c in turn.citations
                ],
                "rounds": turn.rounds,
                "refused": turn.refused,
                "usage": usage,
                "usd": round(cost_usd(usage), 4),
            }
        )
        turns.append(record)

        print(f"\n▶ [{landmark}] {text}")
        print(f"  {turn.text}")
        print(f"  工具：{' → '.join(t.name for t in turn.trace) or '（無）'}")
        print(f"  史料卡：{', '.join(c['id'] for c in turn.citations) or '（無）'}")
        print(f"  {turn.rounds} 輪、{elapsed:.1f}s、約 USD {cost_usd(usage):.4f}（累計 {spent:.4f}）")

        if spent > max_usd:
            print(f"\n⚠️ 估計花費 {spent:.4f} 已超過上限 {max_usd}，提前停止。")
            break

    return {
        "model": SETTINGS.model,
        "effort": SETTINGS.effort,
        "started": started_at,
        "turns": turns,
        "usage_total": total,
        "usd_estimate": round(spent, 4),
        "handoff_notes": dict(state.handoff_notes),
        "interests": dict(state.interests),
    }


def to_markdown(result: dict, title: str) -> str:
    lines = [
        f"# {title}",
        "",
        f"- 模型：`{result['model']}`，effort `{result['effort']}`",
        f"- 時間：{result['started']}",
        f"- 估計花費：**USD {result['usd_estimate']:.4f}**（實際以 Console 為準）",
        f"- token 合計：`{json.dumps(result['usage_total'])}`",
        "",
        "---",
    ]
    for i, t in enumerate(result["turns"], 1):
        lines += ["", f"## {i}.【{t['landmark']}】", "", "**使用者**", ""]
        lines += [f"> {line}" for line in t["user"].splitlines()]
        if "error" in t:
            lines += ["", f"**錯誤**：`{t['error']}`"]
            continue
        lines += ["", "**Agent**", ""]
        lines += [f"> {line}" if line else ">" for line in t["reply"].splitlines()]
        tools = "、".join(f"`{x['name']}`" + ("" if x["ok"] else "（失敗）") for x in t["tools"]) or "（無）"
        cards = "、".join(f"`{c['id']}`" for c in t["citations"]) or "（無）"
        lines += [
            "",
            f"- 工具：{tools}",
            f"- 史料卡：{cards}",
            f"- {t['rounds']} 輪、{t['seconds']}s、約 USD {t['usd']:.4f}"
            + ("、**被拒答**" if t["refused"] else ""),
        ]
    lines += [
        "",
        "---",
        "",
        f"- 交接筆記：{result['handoff_notes'] or '（無）'}",
        f"- 興趣紀錄：{result['interests'] or '（無）'}",
        "",
    ]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--max-usd", type=float, default=1.5)
    parser.add_argument("--name", default="fuzhong_zongtu")
    args = parser.parse_args()

    result = run(DEFAULT_SCRIPT, args.max_usd)

    out_dir = ROOT / "eval" / "live"
    out_dir.mkdir(exist_ok=True)
    stem = f"{datetime.now():%Y-%m-%d_%H%M}_{args.name}"
    (out_dir / f"{stem}.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (out_dir / f"{stem}.md").write_text(
        to_markdown(result, f"真實 API 對話 {stem}"), encoding="utf-8"
    )
    print(f"\n估計總花費：USD {result['usd_estimate']:.4f}")
    print(f"逐字稿：eval/live/{stem}.md")
    return 0 if all("error" not in t for t in result["turns"]) else 1


if __name__ == "__main__":
    sys.exit(main())
