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
    meta: dict[str, Any] | None = None

    @property
    def path(self) -> Path:
        return ARCHIVE_DIR / self.filename

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
