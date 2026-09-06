"""煙霧測試：不需要 API 金鑰，驗證除了 LLM 呼叫以外的每一個環節。

    PYTHONUTF8=1 py eval/test_smoke.py

測的是那些「壞了也不會報錯、只會安靜地給錯結果」的地方：
檢索排序、verify_claim 的數字把關、授權閘門、動畫降級鏈、指標計算。
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.agent import tools as tool_impl  # noqa: E402
from src.agent.visitor_state import VisitorState  # noqa: E402
from src.animate.kenburns import render  # noqa: E402
from src.archive import Photo, PhotoRegistry  # noqa: E402
from src.rag.ingest import load_corpus  # noqa: E402
from src.rag.store import BM25Retriever, tokenize  # noqa: E402
from src.spatial.landmarks import load_graph  # noqa: E402

FIXTURE_CORPUS = ROOT / "eval" / "fixtures" / "corpus"

_passed = 0
_failed: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    global _passed
    if condition:
        _passed += 1
        print(f"  ✅ {name}")
    else:
        _failed.append(name)
        print(f"  ❌ {name}" + (f" —— {detail}" if detail else ""))


# --------------------------------------------------------------------------- #

print("\n[1] 斷詞")
tokens = tokenize("傅鐘敲 21 響")
check("中文產生 bigram", "傅鐘" in tokens, f"got {tokens[:8]}")
check("數字有被保留", "21" in tokens, f"got {tokens}")

print("\n[2] 語料載入")
chunks = load_corpus(FIXTURE_CORPUS)
check("測試語料載入成功", len(chunks) >= 4, f"只載到 {len(chunks)} 個 chunk")
check("frontmatter 有解析", all(c.source for c in chunks))
check("verified 旗標有讀到", all(c.verified for c in chunks))

print("\n[3] BM25 檢索")
retriever = BM25Retriever(chunks)
hits = retriever.search("測試鐘 鳴響 幾次", top_k=3)
check("查得到結果", bool(hits))
check("最相關的是鳴響那段", hits and "十七" in hits[0].chunk.text, hits[0].chunk.text[:40] if hits else "")
check("不相關查詢不亂給", not retriever.search("量子色動力學膠子", top_k=3))

print("\n[4] verify_claim 的數字把關")
state = VisitorState(session_id="test")
state.current_landmark = ""

import src.rag as rag_module  # noqa: E402

rag_module.get_retriever.cache_clear()
rag_module.get_retriever = lambda: retriever  # type: ignore[assignment]
tool_impl.get_retriever = lambda: retriever  # type: ignore[assignment]

supported = tool_impl.verify_claim(state, claim="測試鐘每日鳴響十七次")
check("有語料支撐的斷言 → supported", "status: supported" in supported, supported[:80])

wrong = tool_impl.verify_claim(state, claim="測試鐘每日鳴響九十九次")
check(
    "數字錯誤 → 不得為 supported",
    "status: supported" not in wrong,
    wrong.split("\n")[0],
)

nothing = tool_impl.verify_claim(state, claim="校長昨天騎獨角獸經過")
check("無語料 → unsupported", "unsupported" in nothing, nothing.split("\n")[0])

print("\n[5] 授權閘門")
registry = PhotoRegistry(
    [
        {"id": "ok", "filename": "x.jpg", "licence": "PD", "licence_basis": "法人著作，1935 年發表"},
        {"id": "no_basis", "filename": "y.jpg", "licence": "PD", "licence_basis": ""},
        {"id": "pending", "filename": "z.jpg", "licence": "授權中", "licence_basis": "已行文申請"},
    ]
)
check("有依據的公版 → 可用", registry.get("ok").usable)
check("缺判定依據 → 不可用（不接受概括推定）", not registry.get("no_basis").usable)
check("授權中 → 不可用", not registry.get("pending").usable)
check("未登錄的照片查不到", registry.get("never_registered") is None)

print("\n[6] 地標圖")
graph = load_graph()
check("三個節點", len(graph.all()) == 3, f"got {len(graph.all())}")
check("QR token 可解析", graph.resolve("fuzhong") is not None)
check("預設入口是傅鐘", graph.default_entry().id == "fuzhong")
check("三個地標三個年代", len({lm.era_label for lm in graph.all()}) == 3)
neighbours = graph.neighbours("zongtu", max_minutes=10)
check("舊總圖有兩個鄰居", len(neighbours) == 2, f"got {len(neighbours)}")
check("鄰居依步行時間排序", neighbours[0][1] <= neighbours[1][1])
check("超過門檻會被濾掉", len(graph.neighbours("zongtu", max_minutes=2.5)) == 1)

print("\n[7] 動畫保底方案")
with tempfile.TemporaryDirectory() as tmp:
    from PIL import Image

    src_img = Path(tmp) / "in.jpg"
    Image.new("RGB", (1200, 800), (120, 100, 90)).save(src_img)
    out = render(src_img, Path(tmp) / "out.mp4", duration_sec=0.5)
    check("保底後端有產出檔案", out.exists(), str(out))
    check("檔案不是空的", out.stat().st_size > 0)
    print(f"     （輸出格式：{out.suffix}）")

print("\n[8] 訪客模型與指標")
s = VisitorState(session_id="m")
s.arrive("fuzhong")
s.bump_turn()
s.add_interest("architecture", 2)
s.add_interest("food", 1)
check("興趣排序正確", s.top_interests()[0] == "architecture")
s.record_suggestion("zongtu", reason="architecture")
s.arrive("zongtu")
check("走到推薦地標會被記為採納", s.suggestions[0]["followed"])
check("採納率算得出來", s.metrics()["suggestion_follow_rate"] == 1.0)
check("造訪地標數正確", s.metrics()["landmarks_visited"] == 2)

print("\n[9] 工具介面完整性")
from src.agent.schemas import TOOLS  # noqa: E402

check("八個工具", len(TOOLS) == 8, f"got {len(TOOLS)}")
check("每個工具都有 handler", all(t["name"] in tool_impl.HANDLERS for t in TOOLS))
check("沒有多餘的 handler", len(tool_impl.HANDLERS) == len(TOOLS))
for t in TOOLS:
    schema = t["input_schema"]
    ok = (
        t.get("strict") is True
        and schema.get("additionalProperties") is False
        and set(schema["required"]) == set(schema["properties"])
    )
    check(f"{t['name']} 的 strict schema 合法", ok)

# --------------------------------------------------------------------------- #

print("\n" + "=" * 56)
if _failed:
    print(f"❌ {len(_failed)} 項失敗，{_passed} 項通過")
    for name in _failed:
        print(f"   - {name}")
    sys.exit(1)
print(f"✅ 全部 {_passed} 項通過")
