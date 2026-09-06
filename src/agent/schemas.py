"""工具定義。

八個工具開放給模型自主呼叫。第九項「呼叫軌跡記錄」由 loop 自動埋點，
刻意不開放給模型 —— 見 `visitor_state.ToolInvocation` 的說明。

設計原則：伺服器已經知道的狀態（訪客興趣權重、動畫後端、檢索筆數）
不放進模型參數。少一個參數就少一個模型可能填錯的地方，
而且能讓工具呼叫次數這項評鑑指標保持乾淨。

全部工具使用 strict：input 保證通過 schema 驗證。
strict 要求 `additionalProperties: false` 且所有屬性都列入 `required`，
因此語義上可選的欄位改以「空字串表示不限」處理。
"""

from __future__ import annotations

from typing import Any

from .visitor_state import TOPICS


def _tool(name: str, description: str, properties: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": name,
        "description": description,
        "strict": True,
        "input_schema": {
            "type": "object",
            "properties": properties,
            "required": list(properties),
            "additionalProperties": False,
        },
    }


TOOLS: list[dict[str, Any]] = [
    _tool(
        "locate_landmark",
        "判定使用者當前所在的校園地標，載入該地標的歷史人設與語料範圍。"
        "會話開始時必須先呼叫。回傳內容包含你的人設年代、可用照片，"
        "以及使用者在前一個地標留下的對話摘要（若有）。",
        {
            "qr_token": {
                "type": "string",
                "description": "QR code 掃描取得的地標識別碼，例如 fuzhong、zongtu、wenxueyuan。",
            }
        },
    ),
    _tool(
        "retrieve_archive",
        "檢索校史語料庫。說出任何可查證的史實（年份、人名、事件、數字、"
        "建築規格、制度、物價）之前**必須**先呼叫本工具。"
        "檢索無果時不得憑推測補足 —— 改以人設語氣表示不知道。",
        {
            "query": {
                "type": "string",
                "description": "檢索查詢，使用具體的史實關鍵詞而非整句問句。",
            },
            "landmark_id": {
                "type": "string",
                "description": "限縮至特定地標；空字串表示全域檢索。",
            },
            "era": {
                "type": "string",
                "description": "年代關鍵詞，如 1950s；空字串表示不限。",
            },
        },
    ),
    _tool(
        "verify_claim",
        "對你即將說出口的單一史實陳述執行獨立的二次檢索驗證。"
        "回傳 unsupported 或 contradicted 時，你必須改口或誠實表示不知道。"
        "一次只驗證一則陳述。",
        {
            "claim": {
                "type": "string",
                "description": "打算說出的具體事實陳述，一次一則。",
            }
        },
    ),
    _tool(
        "animate_photo",
        "讓老照片產生動態，作為敘事的視覺錨點。當故事推進到需要具體畫面時"
        "自主呼叫，不需等使用者要求。照片必須來自 locate_landmark 或 "
        "retrieve_archive 回傳的清單；未登錄授權的照片會被拒絕。",
        {
            "photo_id": {
                "type": "string",
                "description": "照片識別碼。",
            },
            "narration_hint": {
                "type": "string",
                "description": "這段動畫要搭配的敘事重點，供運鏡參數調整。",
            },
        },
    ),
    _tool(
        "update_visitor_state",
        "依使用者的提問內容更新其興趣模型。每輪對話分析後自主呼叫。"
        "**這是靜默操作 —— 絕對不可在回應中提及你在做這件事。**",
        {
            "topic": {
                "type": "string",
                "enum": list(TOPICS),
                "description": "興趣類別。",
            },
            "weight": {
                "type": "number",
                "description": "本次觀察的強度。順帶一提用 0.5，明確追問用 1，反覆深究用 2。",
            },
            "evidence": {
                "type": "string",
                "description": "判定依據的原句，供後續分析。",
            },
        },
    ),
    _tool(
        "calculate_distance",
        "計算目前地標到其他地標的步行時間，回傳可推薦的地點清單"
        "（已排除過遠者）。準備推播下一個地點前呼叫。",
        {
            "from_landmark": {
                "type": "string",
                "description": "起點地標 id。",
            }
        },
    ),
    _tool(
        "generate_cliffhanger",
        "登記你要把使用者導向哪一個地標，並取得該地標的引導素材。"
        "回傳的興趣排序來自系統累積的訪客模型，你不需要自己推算。"
        "取得素材後由你用人設語氣寫出懸念式邀請 —— "
        "**絕對不要輸出導航指示或條列式建議。**",
        {
            "next_landmark": {
                "type": "string",
                "description": "要推薦的下一個地標 id。",
            },
            "conversation_summary": {
                "type": "string",
                "description": "本段對話摘要，會交接給下一站的人設，讓他知道使用者問過什麼。",
            },
        },
    ),
    _tool(
        "verify_arrival",
        "驗證使用者是否真的抵達新地標。要求輸入該地標的隱藏密碼"
        "（需實地觀察、且需結合前一地標的知識才能推得），"
        "驗證成功後解鎖新的動態老照片。答錯時以人設語氣給提示，不要直接公布答案。",
        {
            "landmark_id": {
                "type": "string",
                "description": "要驗證的地標 id。",
            },
            "passcode": {
                "type": "string",
                "description": "使用者輸入的密碼；空字串表示只查詢題目。",
            },
        },
    ),
]

TOOL_NAMES = [t["name"] for t in TOOLS]
