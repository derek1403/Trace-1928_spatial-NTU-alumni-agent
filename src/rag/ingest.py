"""語料載入。

`data/corpus/*.md` 每個檔案帶一段 frontmatter 標明出處與校對狀態，
正文以空行分段後切成 chunk。

**出處與校對狀態是強制欄位**：Agent 只能引用有來源的內容，
而 `verified` 決定該筆語料能否被當作已確認史實陳述。
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

from ..config import CORPUS_DIR
from .store import Chunk

log = logging.getLogger(__name__)

_FRONTMATTER = re.compile(r"\A---\s*\n(.*?)\n---\s*\n", re.DOTALL)
_TRUE = {"true", "yes", "1", "是"}


def _parse_frontmatter(raw: str) -> tuple[dict[str, str], str]:
    """極簡 `key: value` frontmatter 解析，不引入 YAML 相依。"""
    match = _FRONTMATTER.match(raw)
    if not match:
        return {}, raw

    meta: dict[str, str] = {}
    for line in match.group(1).splitlines():
        line = line.strip()
        if not line or line.startswith("#") or ":" not in line:
            continue
        key, _, value = line.partition(":")
        meta[key.strip()] = value.strip().strip("\"'")
    return meta, raw[match.end() :]


def _split_chunks(body: str, min_chars: int = 40) -> list[str]:
    """以空行分段。過短的段落併入前一段，避免產生無意義的碎片。"""
    parts = [p.strip() for p in re.split(r"\n\s*\n", body) if p.strip()]
    merged: list[str] = []
    for part in parts:
        if merged and len(part) < min_chars:
            merged[-1] = f"{merged[-1]}\n{part}"
        else:
            merged.append(part)
    return merged


def load_corpus(corpus_dir: Path | None = None) -> list[Chunk]:
    """讀入語料目錄下所有 markdown 檔。"""
    directory = corpus_dir or CORPUS_DIR
    if not directory.exists():
        log.warning("語料目錄不存在：%s", directory)
        return []

    chunks: list[Chunk] = []
    for path in sorted(directory.glob("*.md")):
        if path.name.startswith("_"):
            continue  # _ 開頭視為說明文件，不進語料
        meta, body = _parse_frontmatter(path.read_text(encoding="utf-8"))
        if not meta.get("source"):
            log.warning("%s 缺少 source，仍會載入但引用會標為未標註來源", path.name)

        for i, text in enumerate(_split_chunks(body)):
            chunks.append(
                Chunk(
                    id=f"{path.stem}#{i}",
                    text=text,
                    source=meta.get("source", "未標註來源"),
                    source_date=meta.get("source_date", ""),
                    landmark=meta.get("landmark", ""),
                    era=meta.get("era", ""),
                    verified=meta.get("verified", "false").lower() in _TRUE,
                    verified_by=meta.get("verified_by", ""),
                    meta=dict(meta),
                )
            )

    log.info("載入 %d 個 chunk（來自 %d 個檔案）", len(chunks), len(list(directory.glob("*.md"))))
    return chunks
