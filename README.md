# 尋跡 1928　Trace 1928

**讓台大老照片開口說話的 AI Agent**

115-1 NTU Challengers【個別型提案】

---

> **狀態：骨架完成，等史料。**
> 八個工具、agent loop、RAG、動畫降級鏈、Gradio 介面皆已可運行，煙霧測試 39/39 通過。
> **語料庫刻意留空** —— 史料查證未完成前不放任何內容，理由見下方〈為什麼語料庫是空的〉。
>
> 本 repo 於 **2026/09/07** 起正式執行，首次 commit 時間戳即為專案起始之客觀證明
> （計畫規定：專案限定為 2026/09/07 以後執行之課外活動）。

---

## 這是什麼

在台大校園的地標掃 QR Code，喚醒一個設定為當年學生的 AI Agent。它會讓該地點的老照片動起來，用第一人稱跟你聊那個年代的校園生活——然後**主動請你走去下一個地標**，而且在你抵達掃碼時**記得你剛剛問過什麼**。

最後這件事是本專案與一般 Chatbot 的根本差異：**它會改變你的實體行動路徑。**

---

## 空間範圍：黃金三角 × 三個年代

    傅鐘 ──2min── 舊總圖（校史館） ──3min── 舊文學院
    1960–70s        1950s                  帝大 1930s
      └──────────────4min────────────────────┘

**不做全校地圖。** 三個地標已足以驗證空間主動性的技術可行性，同時把單人四個月的開發負擔控制在可完成範圍。

**走空間就是走時間。** 三個地標分屬三個年代，所以順著這條路走，就是從 1970 年代一路回溯到台大創校的 1928 年——**動線的物理方向就是敘事的時間方向**，這正是「尋跡」的字面意思。

這讓 State Handoff 從「記得你問過什麼」升級為**跨世代的對話**：

> 「你剛剛在總圖跟人聊過？他大概沒告訴你，那棟樓在他來之前是什麼樣子。」

新地標的 Agent 不只繼承記憶，還**知道前一位不知道的事**。技術複雜度不變——就是三份人設檔案而非一份。

---

## 架構

    ┌─────────────────────────────────────────────┐
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

### 三個設計決策

**幻覺控制採架構性隔離。** 人設層（個人敘事，可自由發揮）與事實層（史實斷言，必須有檢索出處）在系統設計上分屬不同處理路徑。檢索不到時 Agent 用人設說「這我倒沒印象了」，而非編造。詳見 [`agents/system_prompt.md`](agents/system_prompt.md)。

**動態影像採可抽換後端，且保底方案零成本。** `commercial_api` → `liveportrait` → `kenburns_2_5d` 逐級降級，最後一級為純 CPU 的單目深度估計 + 視差運鏡，無外部依賴、必然可用。**保證專案在任何情況下都有動態成果。**

**素材授權不依賴外部核可。** 三層供給，MVP 僅依賴 Tier 0 公共領域與明確 CC 授權素材。館藏授權列為加分項。詳見 [`docs/LICENSING.md`](docs/LICENSING.md)。

---

## 目錄導覽

| 路徑 | 內容 |
|---|---|
| [`agents/`](agents/) | Agent 提示詞、人設定義、工具規格 |
| `src/agent/` | Agent 主迴圈、工具實作、訪客狀態 |
| `src/rag/` | 語料 ingest、ChromaDB 儲存、檢索 |
| `src/animate/` | 動態影像後端（三級降級） |
| `src/spatial/` | 地標圖、距離計算、跨地標交接 |
| `src/app/` | Gradio 介面 |
| `data/archive/` | 照片與 metadata（含授權欄位） |
| `data/corpus/` | 校史語料 |
| `data/landmarks/` | 地標定義、隱藏密碼、QR 對應 |
| `eval/` | 50 題校史 QA 評測集 |
| [`docs/`](docs/) | findings、report、授權盤點表 |

### 提示詞文件

- [`agents/system_prompt.md`](agents/system_prompt.md) — 系統提示詞主檔，含 A 類／B 類分野規則與跨世代交接
- [`agents/tool_specs.md`](agents/tool_specs.md) — 八項工具的 JSON schema、設計說明，以及為什麼第九項記錄工具改為自動埋點
- [`agents/persona_construction_method.md`](agents/persona_construction_method.md) — 人設建構方法論與倫理界線

**三份人設**（依地標載入）

| 地標 | 年代 | 檔案 | 設計上要注意的地方 |
|---|---|---|---|
| 傅鐘 | 1960–70s | [`persona_1970s_student.md`](agents/persona_1970s_student.md) | 多數使用者的入口，demo 品質最關鍵；**政治敏感度最高**，有專節約束 |
| 舊總圖 | 1950s | [`persona_1950s_literature_student.md`](agents/persona_1950s_literature_student.md) | 中間站，兩邊都要接得住 |
| 舊文學院 | 帝大 1930s | [`persona_1930s_taihoku_student.md`](agents/persona_1930s_taihoku_student.md) | 尋跡的終點＝時間的起點；**語言設定需公開揭露** |

---

## 快速開始

Windows / PowerShell：

```powershell
$env:PYTHONUTF8 = '1'
py -m venv .venv
.\.venv\Scripts\Activate.ps1
py -m pip install -r requirements.txt

# 煙霧測試：不需要 API 金鑰，驗證除了 LLM 呼叫以外的每一個環節
py eval\test_smoke.py

# 啟動介面（需要 ANTHROPIC_API_KEY，或先跑過 `ant auth login`）
py -m src.app
```

> 含中文的 `.ps1` 腳本請存為 **UTF-8 with BOM**，否則 PowerShell 5.1 會讀成亂碼。

### 評測

```powershell
py eval\run_eval.py --type trap     # 幻覺率：陷阱題
py eval\run_eval.py                 # 全部
```

### 設定

以 `TRACE1928_*` 環境變數覆寫，見 [`.env.example`](.env.example) 與 [`src/config.py`](src/config.py)。
常用的是 `TRACE1928_EFFORT`（對話用 `medium`，跑評測時調 `high`）
與 `TRACE1928_RETRIEVER`（`bm25` 預設 / `chroma`）。

---

## 為什麼語料庫是空的

因為史料還沒查證完成 —— 這是刻意的。

本專案的核心主張是「Agent 不編造史實」。如果連語料庫本身都塞入未經查證的內容，
整個主張就自我推翻了。所以 `data/corpus/` 目前只有格式規範與待蒐集清單。

**空語料庫下系統的行為是正確的**：Agent 會對所有具體問題回答「這我倒沒印象」，
而不是編一個答案。這正是 A 類規則在運作，不是壞掉。
同樣地，`data/landmarks/landmarks.json` 裡所有 `verified: false` 的欄位
（傅鐘設立年份、21 響典故、各建築落成年份）都留白待查。

要測檢索管線本身，用 `eval/fixtures/corpus/` 的虛構語料 —— 內容全為架空，
永遠不會被誤認為真實校史。

---

## 實作與原始規格的差異

| 項目 | 規格 | 實作 | 為什麼 |
|---|---|---|---|
| 工具數 | 9 個模型工具 | **8 個模型工具 + 1 個自動埋點** | `log_visitor_interest` 與 `update_visitor_state` 職責重疊；更關鍵的是它會污染評鑑指標 6（自主工具呼叫次數）—— 那正是我們要拿來當證據的數字 |
| 檢索 | ChromaDB | **BM25 預設**，Chroma 可選 | 零相依、離線可跑、啟動即用。語料量長大再切換 |
| `animate_photo` 的 backend | 模型參數 | 由系統的降級鏈決定 | 少一個模型可能填錯的參數；後端選擇是實作細節，不是敘事決策 |

---

## 執行期程

| 期程 | 里程碑 |
|---|---|
| 9/10–9/12 | 傅鐘單點端到端打通（RAG → agent loop → Ken Burns 動畫 → Gradio） |
| 10 月 | 擴充黃金三角三節點、State Handoff、LivePortrait、50 題評測集 |
| 11 月 | `verify_claim` 上線、幻覺率優化、ARG 解謎機制、公開部署 |
| 12 月上旬 | 校園實測 N=15（SUS + 前後測 + 半結構式訪談） |
| 12 月中旬 | 成果繳交 |

---

## 評鑑指標

| # | 指標 | 目標 |
|---|---|---|
| 1 | 幻覺率 | < 5% |
| 2 | SUS 可用性量表 | > 68 |
| 3 | 解謎成功率（情境式行為反饋） | > 70% |
| 4 | 平均對話輪數 | > 6 |
| 5 | 下一地標推薦採納率 | > 50% |
| 6 | 每會話自主工具呼叫次數 | > 3 |
| 7 | 半結構式訪談 | 15 份完成編碼 |

指標 3 值得一提：`verify_arrival` 的隱藏密碼**需綜合兩個地標的歷史知識才能推得**，因此它同時是遊戲機制與評測工具——**測驗內建於體驗，受測者無感地被評估**，避開了「問卷干擾沉浸感」的經典難題。

---

## 授權

程式碼採 **MIT License**。

`data/archive/` 中的歷史照片**不適用** MIT，各依其原始授權條款，逐張登錄於 [`docs/LICENSING.md`](docs/LICENSING.md)。
