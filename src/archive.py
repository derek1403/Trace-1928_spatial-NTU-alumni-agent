"""照片檔案庫與授權閘門。

專案的授權策略是「登錄先於使用」：未登錄於 `data/archive/registry.json`
的照片，一律不得進入動畫生成或對話引用流程。這個閘門在程式層強制執行，
不依賴人的自律。
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from .config import ARCHIVE_DIR

log = logging.getLogger(__name__)

#: 可直接使用的授權狀態。其餘（授權中、不可用、未登錄）一律拒絕。
USABLE_LICENCES = {"PD", "CC-BY", "CC-BY-SA", "CC0", "UGC"}

#: 人臉動態化的兩種狀態。未填或填錯一律視為 forbidden。
FACE_FORBIDDEN = "forbidden"
FACE_ALLOWED_WITH_CONSENT = "allowed_with_consent"


def _as_bool(value: Any, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in ("true", "1", "yes", "是"):
            return True
        if lowered in ("false", "0", "no", "否"):
            return False
    return default


@dataclass(frozen=True)
class Photo:
    id: str
    filename: str
    landmark: str
    year: str
    source: str
    licence: str
    licence_basis: str
    acquired: str
    caption: str = ""
    #: 畫面中是否有可辨識的個人。未填時預設 True —— 寧可把一張建築照
    #: 誤判成只能運鏡（沒有損失），也不要誤把真人的臉做成動態。
    identifiable_person: bool = True
    face_animation: str = FACE_FORBIDDEN
    #: 允許臉部動態化時的依據（誰同意、何時、如何取得）。比照 licence_basis：
    #: 沒寫依據就不算數。
    face_animation_basis: str = ""
    meta: dict[str, Any] | None = None

    @property
    def path(self) -> Path:
        return ARCHIVE_DIR / self.filename

    @property
    def allows_face_animation(self) -> bool:
        """能否使用會改變臉部的後端（LivePortrait、商用影像 API）。

        回應審查意見二：可辨識的真人，除非有書面依據的同意，否則只做運鏡。
        """
        if not self.identifiable_person:
            return True
        return self.face_animation == FACE_ALLOWED_WITH_CONSENT and bool(
            self.face_animation_basis.strip()
        )

    @property
    def usable(self) -> bool:
        return self.licence in USABLE_LICENCES and bool(self.licence_basis)

    def reject_reason(self) -> str | None:
        if self.licence not in USABLE_LICENCES:
            return f"授權狀態為「{self.licence}」，不可使用。"
        if not self.licence_basis:
            return "缺少授權判定依據。依專案規定，不接受概括推定。"
        if not self.path.exists():
            return f"檔案不存在：{self.filename}"
        return None


class PhotoRegistry:
    def __init__(self, entries: list[dict[str, Any]]) -> None:
        self._photos: dict[str, Photo] = {}
        for raw in entries:
            photo = Photo(
                id=raw["id"],
                filename=raw.get("filename", ""),
                landmark=raw.get("landmark", ""),
                year=str(raw.get("year", "")),
                source=raw.get("source", ""),
                licence=raw.get("licence", ""),
                licence_basis=raw.get("licence_basis", ""),
                acquired=raw.get("acquired", ""),
                caption=raw.get("caption", ""),
                identifiable_person=_as_bool(raw.get("identifiable_person"), True),
                face_animation=raw.get("face_animation") or FACE_FORBIDDEN,
                face_animation_basis=raw.get("face_animation_basis", ""),
                meta=raw,
            )
            self._photos[photo.id] = photo

    def __len__(self) -> int:
        return len(self._photos)

    def get(self, photo_id: str) -> Photo | None:
        return self._photos.get(photo_id)

    def for_landmark(self, landmark_id: str, *, usable_only: bool = True) -> list[Photo]:
        out = [p for p in self._photos.values() if p.landmark == landmark_id]
        if usable_only:
            out = [p for p in out if p.usable and p.path.exists()]
        return out


@lru_cache(maxsize=1)
def load_registry() -> PhotoRegistry:
    path = ARCHIVE_DIR / "registry.json"
    if not path.exists():
        log.warning("照片登錄表不存在：%s（動畫功能將無可用素材）", path)
        return PhotoRegistry([])
    payload = json.loads(path.read_text(encoding="utf-8"))
    return PhotoRegistry(payload.get("photos", []))
