"""動態影像後端與降級鏈。

    commercial_api  失敗 → liveportrait  失敗 → kenburns_2_5d  失敗 → kenburns
                                                                       ↑ 必然成功

最後一級是純 CPU 的平面 Ken Burns，只需 Pillow + numpy。有它在，
「老照片動起來」這項成果就不依賴任何外部服務或 GPU。

兩道閘門（依序）：
1. 授權閘門 —— 未登錄或缺授權依據的照片一律拒絕（`Photo.reject_reason`）。
2. 人臉閘門 —— 有可辨識個人且未取得有依據的同意時，跳過會改變臉部的後端
   （商用 API、LivePortrait），只做運鏡。回應審查意見二。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from ..archive import Photo
from ..config import OUTPUT_DIR, SETTINGS

log = logging.getLogger(__name__)

#: 由高品質到保底的嘗試順序。
CHAIN = ["commercial_api", "liveportrait", "kenburns_2_5d", "kenburns"]

#: 會改變畫面中人物表情或五官的後端。Ken Burns（含 2.5D 視差）只動鏡頭，不在此列。
FACE_DEFORMING = {"commercial_api", "liveportrait"}

FACE_GATE_REASON = "人臉閘門：畫面有可辨識的人物且未取得有依據的同意，只允許運鏡"


@dataclass
class AnimationResult:
    path: Path
    backend: str
    #: 實際嘗試過的後端與失敗原因，供成果報告佐證降級鏈確實運作。
    attempts: list[tuple[str, str]]


class BackendUnavailable(RuntimeError):
    """該後端在目前環境不可用，應降級到下一級。"""


def _commercial_api(photo: Photo, out: Path, duration: float) -> Path:
    raise BackendUnavailable("商用 API 未設定（僅用於精選畫面，非 MVP 必需）")


def _liveportrait(photo: Photo, out: Path, duration: float) -> Path:
    try:
        import liveportrait  # type: ignore # noqa: F401
    except ImportError as exc:
        raise BackendUnavailable("未安裝 LivePortrait") from exc
    raise BackendUnavailable("LivePortrait 整合排程於 10 月")


def _kenburns_25d(photo: Photo, out: Path, duration: float) -> Path:
    from .kenburns import render

    return render(photo.path, out, duration_sec=duration, use_depth=True)


def _kenburns(photo: Photo, out: Path, duration: float) -> Path:
    from .kenburns import render

    return render(photo.path, out, duration_sec=duration, use_depth=False)


_BACKENDS = {
    "commercial_api": _commercial_api,
    "liveportrait": _liveportrait,
    "kenburns_2_5d": _kenburns_25d,
    "kenburns": _kenburns,
}


def animate(
    photo: Photo,
    *,
    backend: str = "auto",
    duration_sec: float | None = None,
) -> AnimationResult:
    """讓照片動起來。

    呼叫端必須先確認 `photo.usable`；本函式不重複做授權判斷，
    但會拒絕檔案不存在的情況。
    """
    reason = photo.reject_reason()
    if reason:
        raise ValueError(f"照片 {photo.id} 不可使用：{reason}")

    duration = duration_sec or SETTINGS.animate_duration_sec
    order = CHAIN if backend in ("auto", "", None) else [backend]
    # 指定單一後端時仍保留保底，寧可畫質降級也不要沒有成果。
    if order != CHAIN and "kenburns" not in order:
        order = [*order, "kenburns"]

    out = OUTPUT_DIR / f"{photo.id}.mp4"
    attempts: list[tuple[str, str]] = []

    for name in order:
        fn = _BACKENDS.get(name)
        if fn is None:
            attempts.append((name, "未知的後端名稱"))
            continue
        if name in FACE_DEFORMING and not photo.allows_face_animation:
            attempts.append((name, FACE_GATE_REASON))
            continue
        try:
            path = fn(photo, out, duration)
            return AnimationResult(path=path, backend=name, attempts=attempts)
        except BackendUnavailable as exc:
            attempts.append((name, str(exc)))
            log.info("後端 %s 不可用，降級：%s", name, exc)
        except Exception as exc:  # pragma: no cover - 環境相關
            attempts.append((name, f"執行失敗：{exc}"))
            log.warning("後端 %s 執行失敗，降級：%s", name, exc)

    raise RuntimeError(f"所有後端皆失敗：{attempts}")


__all__ = [
    "animate",
    "AnimationResult",
    "BackendUnavailable",
    "CHAIN",
    "FACE_DEFORMING",
    "FACE_GATE_REASON",
]
