"""Ken Burns 保底方案。

純 CPU、無外部服務、無 API 額度限制。這是整條動態影像降級鏈的終點，
存在的唯一目的是**保證專案在任何情況下都有動態成果**。

兩種模式：
- `kenburns`      平面平移縮放。只需 Pillow + numpy，必然可用。
- `kenburns_2_5d` 加上單目深度估計做視差位移，前景與背景以不同速度移動，
                  產生立體感。需要 torch；不可用時自動退回平面模式。

兩種模式都**只移動鏡頭，不改變畫面中人物的表情或五官**，所以可以用在
有可辨識人物的照片上（人臉閘門見 `animate/__init__.py`）。
所有輸出影格一律燒入「AI 生成動態影像」標示（回應審查意見二）。
"""

from __future__ import annotations

import logging
from functools import lru_cache
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

log = logging.getLogger(__name__)

_FPS = 24

AI_LABEL = "AI 生成動態影像"
#: 找不到中文字型時的退路。寧可標示是英文，也不能沒有標示。
AI_LABEL_ASCII = "AI-generated motion"

_CJK_FONT_CANDIDATES = (
    "C:/Windows/Fonts/msjh.ttc",  # 微軟正黑體
    "C:/Windows/Fonts/msjhbd.ttc",
    "C:/Windows/Fonts/mingliu.ttc",
    "/System/Library/Fonts/PingFang.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/noto-cjk/NotoSansCJK-Regular.ttc",
)


@lru_cache(maxsize=8)
def _label_font(size: int) -> tuple[ImageFont.ImageFont, bool]:
    """回傳 (字型, 是否支援中文)。"""
    for path in _CJK_FONT_CANDIDATES:
        try:
            return ImageFont.truetype(path, size), True
        except OSError:
            continue
    log.warning("找不到中文字型，AI 標示改用英文")
    return ImageFont.load_default(), False


def add_ai_label(frame: Image.Image) -> Image.Image:
    """在右下角燒入半透明底的 AI 生成標示。所有動態影像後端都應呼叫這個函式。"""
    img = frame.convert("RGB").copy()
    draw = ImageDraw.Draw(img, "RGBA")
    size = max(14, img.height // 24)
    font, cjk = _label_font(size)
    text = AI_LABEL if cjk else AI_LABEL_ASCII
    left, top, right, bottom = draw.textbbox((0, 0), text, font=font)
    tw, th = right - left, bottom - top
    pad = max(4, size // 3)
    x = img.width - tw - pad * 3
    y = img.height - th - pad * 3
    draw.rectangle((x - pad, y - pad, x + tw + pad, y + th + pad), fill=(0, 0, 0, 150))
    draw.text((x - left, y - top), text, font=font, fill=(255, 255, 255, 235))
    return img


def _ease(t: float) -> float:
    """平滑進出，避免運鏡在頭尾出現生硬的速度跳變。"""
    return t * t * (3.0 - 2.0 * t)


def _estimate_depth(image: Image.Image) -> np.ndarray | None:
    """單目深度估計。torch/timm 不可用時回傳 None。"""
    try:
        import torch
    except ImportError:
        return None

    try:
        model = torch.hub.load("intel-isl/MiDaS", "MiDaS_small", trust_repo=True)
        transforms = torch.hub.load("intel-isl/MiDaS", "transforms", trust_repo=True)
        model.eval()
        sample = transforms.small_transform(np.array(image.convert("RGB")))
        with torch.no_grad():
            prediction = model(sample)
            prediction = torch.nn.functional.interpolate(
                prediction.unsqueeze(1),
                size=image.size[::-1],
                mode="bicubic",
                align_corners=False,
            ).squeeze()
        depth = prediction.cpu().numpy()
    except Exception as exc:  # pragma: no cover - 需下載模型
        log.warning("深度估計失敗，退回平面 Ken Burns：%s", exc)
        return None

    span = float(depth.max() - depth.min())
    if span <= 0:
        return None
    return (depth - depth.min()) / span


def _parallax_frame(
    rgb: np.ndarray, depth: np.ndarray, shift_px: float
) -> Image.Image:
    """依深度做水平視差位移：近景位移大、遠景位移小。"""
    h, w = depth.shape
    xs = np.arange(w)[None, :].repeat(h, axis=0)
    # depth 已正規化到 0..1，1 代表最近。
    src_x = np.clip(np.rint(xs - shift_px * depth).astype(np.int64), 0, w - 1)
    rows = np.arange(h)[:, None].repeat(w, axis=1)
    return Image.fromarray(rgb[rows, src_x])


def render(
    image_path: Path,
    output_path: Path,
    *,
    duration_sec: float = 4.0,
    use_depth: bool = False,
    size: tuple[int, int] = (960, 540),
) -> Path:
    """輸出運鏡影片。優先寫 mp4，無 ffmpeg 時退回動畫 GIF。"""
    image = Image.open(image_path).convert("RGB")

    # 先等比裁切到目標長寬比，避免變形。
    target_ratio = size[0] / size[1]
    w, h = image.size
    if w / h > target_ratio:
        new_w = int(h * target_ratio)
        image = image.crop(((w - new_w) // 2, 0, (w + new_w) // 2, h))
    else:
        new_h = int(w / target_ratio)
        image = image.crop((0, (h - new_h) // 2, w, (h + new_h) // 2))

    # 放大留出運鏡餘裕：這 15% 就是鏡頭可以推移的空間。
    base = image.resize((int(size[0] * 1.15), int(size[1] * 1.15)), Image.LANCZOS)
    depth = _estimate_depth(base) if use_depth else None
    rgb = np.array(base)

    frame_count = max(int(duration_sec * _FPS), 2)
    max_shift_x = base.size[0] - size[0]
    max_shift_y = base.size[1] - size[1]

    frames: list[Image.Image] = []
    for i in range(frame_count):
        t = _ease(i / (frame_count - 1))
        canvas = (
            _parallax_frame(rgb, depth, shift_px=t * 18.0)
            if depth is not None
            else base
        )
        left = int(t * max_shift_x)
        top = int(t * max_shift_y * 0.5)
        frames.append(add_ai_label(canvas.crop((left, top, left + size[0], top + size[1]))))

    output_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        import imageio.v2 as imageio

        with imageio.get_writer(output_path, fps=_FPS, macro_block_size=1) as writer:
            for frame in frames:
                writer.append_data(np.array(frame))
        return output_path
    except Exception as exc:
        # 沒有 ffmpeg 也要有東西可看 —— 這就是「保底」的意思。
        log.warning("mp4 輸出失敗（%s），改寫 GIF。", exc)
        gif_path = output_path.with_suffix(".gif")
        frames[0].save(
            gif_path,
            save_all=True,
            append_images=frames[1:],
            duration=int(1000 / _FPS),
            loop=0,
        )
        return gif_path
