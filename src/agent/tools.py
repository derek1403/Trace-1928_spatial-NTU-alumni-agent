"""八個工具的實作。

回傳一律為給模型看的字串。措辭刻意寫得像「給人的指示」而非資料庫傾印，
因為工具回傳值會直接影響模型下一步的行為。
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from ..animate import animate
from ..archive import load_registry
from ..config import SETTINGS
from ..rag import get_retriever
from ..spatial.landmarks import load_graph
from .visitor_state import TOPIC_LABELS, VisitorState

log = logging.getLogger(__name__)


class ToolError(Exception):
    """工具執行失敗，會以 is_error=True 回送給模型。"""


# --------------------------------------------------------------------------- #
# 感知
# --------------------------------------------------------------------------- #


def locate_landmark(state: VisitorState, *, qr_token: str) -> str:
    graph = load_graph()
    landmark = graph.resolve(qr_token)
    if landmark is None:
        known = "、".join(lm.id for lm in graph.all())
        raise ToolError(f"無法辨識的地標代碼「{qr_token}」。目前支援：{known}")

    previous = state.current_landmark
    state.arrive(landmark.id)

    lines = [
        f"地標：{landmark.name}（id: {landmark.id}）",
        f"你的人設年代：{landmark.era_label}",
        f"人設檔案：{landmark.persona_file}",
    ]
    if landmark.entry_note:
        lines.append(f"敘事定位：{landmark.entry_note}")
    if landmark.language_disclosure:
        lines.append(f"語言設定揭露（介面已顯示給使用者）：{landmark.language_disclosure}")

    if not landmark.facts_verified:
        todo = "、".join(landmark.facts_status.get("todo", []))
        lines.append(
            "⚠️ 本地標的史實錨點尚未查證完成"
            + (f"（待查：{todo}）" if todo else "")
            + "。語料庫中沒有的內容一律不得陳述，改以人設語氣表示不知道。"
        )

    photos = load_registry().for_landmark(landmark.id)
    if photos:
        listing = "、".join(f"{p.id}（{p.year or '年份待考'}）" for p in photos)
        lines.append(f"可用照片（授權已登錄）：{listing}")
    else:
        lines.append("可用照片：無。本地標目前沒有通過授權登錄的素材，不要嘗試呼叫 animate_photo。")

    # 跨世代交接 —— 這是整個系統最有感的地方，務必用上。
    handoff = state.handoff_notes.get(previous) if previous else None
    if handoff and previous != landmark.id:
        prev_lm = graph.get(previous) if previous else None
        prev_desc = f"{prev_lm.name}（{prev_lm.era_label}）" if prev_lm else previous
        lines.append(
            f"【交接】使用者剛從 {prev_desc} 過來，那一段聊的是：{handoff}\n"
            "開場就要用上這件事，並點出你們是不同年代的人 —— "
            "你知道他不知道的事，或你們的說法不一樣。"
        )
    else:
        lines.append("【交接】無前段記憶，這是使用者的第一站。")

    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# 知識
# --------------------------------------------------------------------------- #


def _format_hits(hits: list[Any]) -> str:
    if not hits:
        return ""
    blocks = []
    for i, hit in enumerate(hits, 1):
        chunk = hit.chunk
        flag = "" if chunk.verified else "（未經館員校對，引用時語氣要保留）"
        blocks.append(f"[{i}] {chunk.text}\n    —— 出處：{chunk.citation()}{flag}")
    return "\n".join(blocks)


def retrieve_archive(
    state: VisitorState, *, query: str, landmark_id: str = "", era: str = ""
) -> str:
    retriever = get_retriever()
    if len(retriever) == 0:
        raise ToolError(
            "語料庫是空的（data/corpus/ 尚未放入史料）。"
            "你現在無法查證任何史實，所有具體事實一律以人設語氣表示不知道。"
        )

    search_query = f"{query} {era}".strip()
    hits = retriever.search(
        search_query,
        top_k=SETTINGS.top_k,
        landmark=landmark_id or None,
    )
    if not hits and landmark_id:
        hits = retriever.search(search_query, top_k=SETTINGS.top_k)

    if not hits:
        return (
            f"查無「{query}」的相關語料。\n"
            "→ 不要推測、不要用常識填補。以人設語氣表示這件事你沒印象，"
            "然後主動把話題帶回你確實知道的事情上。"
        )
    return f"檢索到 {len(hits)} 筆：\n{_format_hits(hits)}"


# --- verify_claim ---------------------------------------------------------- #

_YEAR = re.compile(r"(?:19|20)\d{2}|\d{1,4}\s*(?:年|響|號|樓|元|角|分)")
_CJK_RUN = re.compile(r"[一-鿿]{2,}")


def _claim_atoms(claim: str) -> list[str]:
    """從斷言反推可查證的原子。

    這是 verify_claim 與 retrieve_archive 的關鍵差異：檢索是從**問題**正推
    關鍵詞，驗證是從**斷言本身**反推。若兩者共用同一套查詢構造，
    二次驗證就只是把第一次的結果再拿一次，形同橡皮圖章。
    """
    atoms = _YEAR.findall(claim)
    atoms.extend(run for run in _CJK_RUN.findall(claim) if len(run) >= 2)
    # 長字串優先：專有名詞比通用詞更有鑑別力
    return sorted(set(atoms), key=len, reverse=True)[:8]


def verify_claim(state: VisitorState, *, claim: str) -> str:
    atoms = _claim_atoms(claim)
    if not atoms:
        return "status: unsupported\n這句話沒有可查證的具體內容。若它其實是個人敘事（B 類），就不需要驗證。"

    retriever = get_retriever()
    if len(retriever) == 0:
        return "status: unsupported\n語料庫是空的，無法驗證任何斷言。不要說出這句話。"

    # 逐個原子獨立檢索，看斷言的每個組成部分是否都有語料支撐。
    supported_atoms: list[str] = []
    evidence: list[Any] = []
    for atom in atoms:
        hits = retriever.search(atom, top_k=2, landmark=state.current_landmark)
        for hit in hits:
            if atom in hit.chunk.text:
                supported_atoms.append(atom)
                evidence.append(hit)
                break

    coverage = len(supported_atoms) / len(atoms)
    numeric = [a for a in atoms if any(ch.isdigit() for ch in a)]
    numeric_ok = all(a in supported_atoms for a in numeric)

    if coverage >= 0.6 and numeric_ok:
        status = "supported"
        note = "可以說。引用時仍要標明是聽說或記得，不要用教科書口吻。"
    elif coverage > 0 and not numeric_ok:
        status = "contradicted"
        missing = "、".join(a for a in numeric if a not in supported_atoms)
        note = (
            f"**數字沒有語料支撐：{missing}。**\n"
            "→ 不要說出這個數字。可以講這件事的大概，但具體數目一律略過或明說記不清。"
        )
    elif coverage > 0:
        status = "partially_supported"
        note = "只有部分內容查得到。把沒查到的部分拿掉，或改用不確定的語氣。"
    else:
        status = "unsupported"
        note = "→ 不要說這句話。以人設語氣表示沒印象，然後轉回你知道的事。"

    lines = [f"status: {status}", f"可查證原子：{'、'.join(atoms)}", f"有語料支撐：{coverage:.0%}"]
    if evidence:
        lines.append("佐證：\n" + _format_hits(evidence[:3]))
    lines.append(note)
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# 生成
# --------------------------------------------------------------------------- #


def animate_photo(state: VisitorState, *, photo_id: str, narration_hint: str = "") -> str:
    photo = load_registry().get(photo_id)
    if photo is None:
        raise ToolError(
            f"照片 {photo_id} 未登錄於授權表。依專案規定，未登錄素材一律不得使用。"
        )
    reason = photo.reject_reason()
    if reason:
        raise ToolError(f"照片 {photo_id} 不可使用：{reason}")

    try:
        result = animate(photo, backend=SETTINGS.animate_backend)
    except Exception as exc:
        raise ToolError(f"動畫生成失敗：{exc}") from exc

    state.pending_media.append(str(result.path))
    lines = [
        f"已生成動態影像：{result.path.name}（後端：{result.backend}）",
        f"畫面內容：{photo.caption or '（無說明）'}",
        f"素材出處：{photo.source}｜授權：{photo.licence}",
    ]
    if result.attempts:
        chain = "、".join(f"{name}（{why}）" for name, why in result.attempts)
        lines.append(f"降級紀錄：{chain}")
    lines.append("→ 畫面已顯示給使用者。接著用你的語氣講這張照片，不要複述上面的技術資訊。")
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# 記憶
# --------------------------------------------------------------------------- #


def update_visitor_state(
    state: VisitorState, *, topic: str, weight: float = 1.0, evidence: str = ""
) -> str:
    if topic not in TOPIC_LABELS:
        raise ToolError(f"未知的興趣類別「{topic}」。")
    state.add_interest(topic, float(weight))
    # 回傳刻意簡短且不帶指示，避免模型把這件事講出來。
    return "ok"


# --------------------------------------------------------------------------- #
# 空間
# --------------------------------------------------------------------------- #


def calculate_distance(state: VisitorState, *, from_landmark: str) -> str:
    graph = load_graph()
    if from_landmark not in graph:
        raise ToolError(f"未知的地標 id「{from_landmark}」。")

    neighbours = graph.neighbours(from_landmark, max_minutes=SETTINGS.max_walk_minutes)
    if not neighbours:
        return "附近沒有可推薦的地標。"

    turns = state.turns_here()
    lines = []
    for lm, minutes in neighbours:
        visited = "（已去過）" if lm.id in state.visited else ""
        lines.append(f"- {lm.name}（id: {lm.id}）步行約 {minutes:.0f} 分鐘｜{lm.era_label}{visited}")

    ready = turns >= SETTINGS.cliffhanger_after_turns
    lines.append(
        f"目前在本地標已對話 {turns} 輪。"
        + (
            "已達推播門檻，若話題告一段落就可以呼叫 generate_cliffhanger。"
            if ready
            else f"尚未達到 {SETTINGS.cliffhanger_after_turns} 輪的門檻，除非使用者主動表示要走，否則先把這裡聊透。"
        )
    )
    return "\n".join(lines)


def generate_cliffhanger(
    state: VisitorState, *, next_landmark: str, conversation_summary: str
) -> str:
    graph = load_graph()
    target = graph.get(next_landmark)
    if target is None:
        raise ToolError(f"未知的地標 id「{next_landmark}」。")

    current = state.current_landmark
    if current:
        state.handoff_notes[current] = conversation_summary
    state.record_suggestion(next_landmark, reason=",".join(state.top_interests()))

    interests = state.top_interests()
    interest_text = (
        "、".join(TOPIC_LABELS.get(t, t) for t in interests) if interests else "尚未看出明顯偏好"
    )
    minutes = graph.walk_minutes(current, next_landmark) if current else None

    lines = [
        f"下一站：{target.name}（{target.era_label}）"
        + (f"，步行約 {minutes:.0f} 分鐘" if minutes else ""),
        f"使用者的興趣排序：{interest_text}",
        f"已登記交接摘要：{conversation_summary}",
    ]
    if target.arrival_puzzle.get("enabled"):
        lines.append("該地標設有抵達驗證，可以先預告那裡有個「要自己去看才知道」的東西。")

    lines.append(
        "→ 現在用你的人設語氣寫出邀請：拋一個跟上述興趣有關的懸念，"
        "說一件你講不清楚、非得他自己去看的事。"
        "**不要給步行時間、不要給方位、不要列點。**"
    )
    return "\n".join(lines)


def verify_arrival(state: VisitorState, *, landmark_id: str, passcode: str = "") -> str:
    graph = load_graph()
    landmark = graph.get(landmark_id)
    if landmark is None:
        raise ToolError(f"未知的地標 id「{landmark_id}」。")

    puzzle = landmark.arrival_puzzle
    if not puzzle.get("enabled"):
        state.unlocked.add(landmark_id)
        return (
            f"{landmark.name} 的抵達解謎尚未啟用（密碼待實地勘查後設計），已直接解鎖。\n"
            "→ 不要向使用者提及密碼或解謎機制。"
        )

    if not passcode:
        return f"題目：{puzzle.get('hint', '（尚未設定）')}\n→ 用你的語氣把這個問題拋給使用者。"

    expected = str(puzzle.get("passcode") or "").strip().lower()
    if passcode.strip().lower() == expected:
        state.unlocked.add(landmark_id)
        return "驗證成功。→ 表現出「你真的來了」的驚喜，然後解鎖新的照片與故事。"
    return (
        f"密碼不正確。提示：{puzzle.get('hint', '')}\n"
        "→ 以人設語氣給一點方向，但**不要直接公布答案**。"
    )


# --------------------------------------------------------------------------- #

HANDLERS = {
    "locate_landmark": locate_landmark,
    "retrieve_archive": retrieve_archive,
    "verify_claim": verify_claim,
    "animate_photo": animate_photo,
    "update_visitor_state": update_visitor_state,
    "calculate_distance": calculate_distance,
    "generate_cliffhanger": generate_cliffhanger,
    "verify_arrival": verify_arrival,
}


def execute(state: VisitorState, name: str, arguments: dict[str, Any]) -> str:
    handler = HANDLERS.get(name)
    if handler is None:
        raise ToolError(f"未知的工具「{name}」。")
    # strict schema 已保證型別，但空字串代表「不限」，在各 handler 內處理。
    return handler(state, **arguments)


def describe_arguments(name: str, arguments: dict[str, Any]) -> str:
    """給 trace log 用的簡短摘要。"""
    if name == "retrieve_archive":
        return str(arguments.get("query", ""))[:60]
    if name == "verify_claim":
        return str(arguments.get("claim", ""))[:60]
    if name == "update_visitor_state":
        return f"{arguments.get('topic')} +{arguments.get('weight', 1)}"
    if name == "generate_cliffhanger":
        return f"-> {arguments.get('next_landmark')}"
    return json.dumps(arguments, ensure_ascii=False)[:60]
