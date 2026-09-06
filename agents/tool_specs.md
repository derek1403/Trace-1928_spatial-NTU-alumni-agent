# Tool Specifications — 尋跡 1928 Agent

八項**模型可自主呼叫**的工具規格，實作對應 `src/agent/tools.py`、schema 定義於 `src/agent/schemas.py`。

第九項「工具呼叫軌跡記錄」由 agent loop **自動埋點**，刻意不開放模型呼叫 —— 見文末〈為什麼記錄工具不給模型呼叫〉。

---

## 工具總覽

| Tool | 分類 | 觸發時機 | MVP 必要 |
|---|---|---|---|
| `locate_landmark` | 感知 | 會話開始 | ✅ |
| `retrieve_archive` | 知識 | 需要史實佐證時 | ✅ |
| `verify_claim` | 知識 | 輸出前自檢 | ✅ |
| `animate_photo` | 生成 | 敘事需要視覺錨點時 | ✅ |
| `update_visitor_state` | 記憶 | 每輪對話分析後 | 10 月 |
| `calculate_distance` | 空間 | 準備推播前 | 10 月 |
| `generate_cliffhanger` | 空間 | 對話接近尾聲 | 10 月 |
| `verify_arrival` | 空間 | 抵達新地標時 | 11 月 |

> **MVP（9/10–9/12 送件前 demo）只實作前四項**，跑通傅鐘單點的端到端流程即可。空間類工具留到 10 月三節點擴充時實作。

---

## 感知類

### `locate_landmark`

由 QR code 或 GPS 判定使用者所在地標，載入對應人設與語料範圍。

```json
{
  "name": "locate_landmark",
  "description": "判定使用者當前所在的校園地標，載入該地標的歷史人設與語料範圍。會話開始時必須先呼叫。",
  "input_schema": {
    "type": "object",
    "properties": {
      "qr_token": { "type": "string", "description": "QR code 掃描取得的地標識別碼" },
      "gps": {
        "type": "object",
        "properties": { "lat": { "type": "number" }, "lon": { "type": "number" } },
        "description": "GPS 座標，作為 qr_token 缺失時的備援"
      }
    }
  }
}
```

**回傳**：`landmark_id`、`persona_file`、`corpus_filter`、`available_photos[]`、`prior_session_summary`（若有跨地標交接）

---

## 知識類

### `retrieve_archive`

向量檢索校史語料與照片 metadata。**任何 A 類史實斷言之前都必須先呼叫此工具。**

```json
{
  "name": "retrieve_archive",
  "description": "檢索校史語料庫與照片檔案。說出任何可查證的史實（年份、人名、事件、數字、建築規格、制度、物價）之前必須先呼叫本工具。檢索無果時不得憑推測補足。",
  "input_schema": {
    "type": "object",
    "properties": {
      "query": { "type": "string", "description": "檢索查詢，使用具體的史實關鍵詞" },
      "landmark_id": { "type": "string", "description": "限縮至特定地標，可留空表示全域檢索" },
      "era_range": { "type": "string", "description": "年代範圍，如 1945-1960" },
      "top_k": { "type": "integer", "default": 5 }
    },
    "required": ["query"]
  }
}
```

**回傳**：`chunks[]`（含 `text`、`source`、`source_date`、`verified_by`、`confidence`）

> `verified_by` 欄位記錄該筆語料是否已經圖書館／校史館館員校對，是內容效度的追蹤依據。

---

### `verify_claim`

對 Agent 自己產出的史實斷言執行二次檢索驗證。**這是幻覺控制的最後一道防線。**

```json
{
  "name": "verify_claim",
  "description": "對即將輸出的史實斷言執行獨立的二次檢索驗證。傳入你打算說出口的具體事實陳述，本工具會回報是否有語料支持。若回報 unsupported，你必須改口或誠實表示不知道。",
  "input_schema": {
    "type": "object",
    "properties": {
      "claim": { "type": "string", "description": "打算說出的單一史實陳述，一次一則" },
      "landmark_id": { "type": "string" }
    },
    "required": ["claim"]
  }
}
```

**回傳**：`status`（`supported` / `partially_supported` / `unsupported` / `contradicted`）、`evidence[]`、`note`

**設計要點**：`verify_claim` 使用與 `retrieve_archive` **不同的檢索查詢構造方式**（從斷言反推關鍵詞，而非從問題正推）。若兩者用同一套查詢，二次驗證就失去獨立性，形同橡皮圖章。

---

## 生成類

### `animate_photo`

呼叫可抽換的動態影像後端。

```json
{
  "name": "animate_photo",
  "description": "讓老照片產生動態效果，作為敘事的視覺錨點。在故事推進到需要具體畫面時自主呼叫，不需等使用者要求。",
  "input_schema": {
    "type": "object",
    "properties": {
      "photo_id": { "type": "string", "description": "來自 retrieve_archive 或 locate_landmark 的照片識別碼" },
      "backend": {
        "type": "string",
        "enum": ["kenburns_2_5d", "liveportrait", "commercial_api"],
        "default": "kenburns_2_5d",
        "description": "kenburns_2_5d 為零成本保底方案，必然可用；liveportrait 適用於有清晰人臉的照片；commercial_api 僅用於精選畫面"
      },
      "duration_sec": { "type": "number", "default": 4 },
      "narration_hint": { "type": "string", "description": "這段動畫要搭配的敘事重點，供運鏡參數調整" }
    },
    "required": ["photo_id"]
  }
}
```

**後端降級策略**：

    commercial_api  失敗 →  liveportrait  失敗 →  kenburns_2_5d（必成功）

`kenburns_2_5d` 為純 CPU 的單目深度估計 + 視差運鏡，無外部依賴、無 API 額度限制，作為**保證有輸出**的終點。

**授權檢查**：呼叫前必須確認 `photo_id` 在 `docs/LICENSING.md` 中有合法授權記錄。無記錄者拒絕生成。

---

## 記憶類

### `update_visitor_state`

記錄使用者興趣標籤。**Agent 自主判定並靜默呼叫，不得讓使用者察覺。**

```json
{
  "name": "update_visitor_state",
  "description": "依使用者的提問內容更新其興趣模型。每輪對話分析後自主呼叫。此為靜默操作，絕對不可在回應中提及你在做這件事。",
  "input_schema": {
    "type": "object",
    "properties": {
      "topic": {
        "type": "string",
        "enum": ["architecture", "daily_life", "academia", "politics", "people", "romance", "food"],
        "description": "興趣類別"
      },
      "weight": { "type": "number", "default": 1, "description": "本次觀察的強度，明確追問給較高權重" },
      "evidence": { "type": "string", "description": "判定依據的原句，供後續分析" }
    },
    "required": ["topic"]
  }
}
```

---

### `log_visitor_interest`

記錄興趣軌跡與工具呼叫鏈，供評鑑指標 6（Agentic 程度）之量化分析。全程自動記錄，去識別化後保存。

---

## 空間類

### `calculate_distance`

```json
{
  "name": "calculate_distance",
  "description": "計算地標間的步行距離與時間，用於篩選可推薦的下一地點。排除步行超過 10 分鐘者。",
  "input_schema": {
    "type": "object",
    "properties": {
      "from_landmark": { "type": "string" },
      "to_landmark": { "type": "string", "description": "留空則回傳所有可達地標" }
    },
    "required": ["from_landmark"]
  }
}
```

**MVP 實作**：黃金三角僅三節點，直接以硬編碼的鄰接矩陣實作即可，無須接地圖 API。

    傅鐘 ──2min── 舊總圖 ──3min── 文學院
      └──────────4min──────────────┘

---

### `generate_cliffhanger`

```json
{
  "name": "generate_cliffhanger",
  "description": "依下一地點與使用者的興趣模型，生成懸念式引導語。輸出必須是人設語氣的口語邀請，絕對不可產生導航指示或條列式建議。",
  "input_schema": {
    "type": "object",
    "properties": {
      "next_landmark": { "type": "string" },
      "top_interests": { "type": "array", "items": { "type": "string" }, "description": "訪客興趣模型的前幾名類別" },
      "conversation_summary": { "type": "string", "description": "本段對話摘要，供交接使用" }
    },
    "required": ["next_landmark", "top_interests"]
  }
}
```

**輸出風格約束**：

| | 範例 |
|---|---|
| ✅ 正確 | 「你要是走去文學院，那邊的拱門有個東西我一直覺得很妙，可惜三言兩語說不清楚。」 |
| ✗ 錯誤 | 「建議您接下來前往文學院，步行約 3 分鐘。」 |

**副作用**：本工具同時將 `conversation_summary` 寫入 session store，供下一地標的 Agent 做 State Handoff。

---

### `verify_arrival`

```json
{
  "name": "verify_arrival",
  "description": "驗證使用者是否真的抵達新地標。要求輸入該地標的隱藏密碼（需實地觀察才能取得，如石碑上的年份），驗證成功後解鎖新的動態老照片。",
  "input_schema": {
    "type": "object",
    "properties": {
      "landmark_id": { "type": "string" },
      "passcode": { "type": "string", "description": "使用者輸入的密碼" },
      "gps": { "type": "object", "description": "GPS 備援驗證" }
    },
    "required": ["landmark_id"]
  }
}
```

**這個工具身兼二職**——這是本專案評鑑設計的核心：

1. **遊戲機制**：ARG 式的解謎，提供抵達的成就感與探索動機
2. **評測工具**：密碼設計為**需綜合兩個地標的歷史知識方能推得**，因此解謎成功率直接反映 Agent 的知識傳遞效率（評鑑指標 3）

測驗被內建於體驗之中，受測者無感地被評估，避開「問卷本身干擾沉浸感」的經典難題。

**密碼設計原則**：
- 必須是實地可觀察、且需要前一地標的知識才能定位的資訊
- 不可只靠搜尋引擎取得
- 答錯時 Agent 以人設語氣給提示，不直接公布答案

---

## 工具呼叫循環

    ┌─────────────────────────────────────────────┐
    │                                             │
    │   Plan ──► Act ──► Observe ──► Replan ──────┤
    │             │                               │
    │             ├─ 感知  locate_landmark        │
    │             ├─ 知識  retrieve_archive       │
    │             │        verify_claim           │
    │             ├─ 生成  animate_photo          │
    │             ├─ 記憶  update_visitor_state   │
    │             └─ 空間  calculate_distance     │
    │                      generate_cliffhanger   │
    │                      verify_arrival         │
    └─────────────────────────────────────────────┘

此圖即計畫書之 **Fig.3**，繪製時將八個工具置於 Act 階段，並在迴圈外側標註自動埋點。

---

## 為什麼記錄工具不給模型呼叫

原始規格把 `log_visitor_interest` 列為第九個模型工具。實作時改為自動埋點，理由有兩個，第二個是決定性的：

1. **它與 `update_visitor_state` 職責重疊。** 兩個功能相近的工具會實際降低模型的工具選擇準確率 —— 模型得先花力氣決定該用哪一個。

2. **它會污染評鑑指標 6。** 該指標統計「每次會話的自主工具呼叫次數」，用來佐證 Agentic 程度。若讓模型自己呼叫一個純記錄用的工具，這個數字就變成可以灌水的假指標 —— 而我們正是要拿它當證據。

自動埋點反而記錄得更完整：每一次呼叫的名稱、參數、成功與否、耗時，全都由 loop 寫入 `VisitorState.trace`，模型漏呼叫也不會漏記。
