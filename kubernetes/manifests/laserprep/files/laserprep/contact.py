"""Contact sheet: a labelled grid of threshold variants.

Burn ONE test tile of this sheet per material and pick the cut point by eye
on the actual substrate — the screen lies about what the material will do.
"""

from __future__ import annotations

import cv2
import numpy as np
from PIL import Image, ImageDraw

from .dither import DITHER_MODES, dither
from .materials import Material, mm_to_px
from .prep import despeckle, threshold

_LABEL_H = 28
_PAD = 6
_THUMB_W = 480


def _center_crop(gray: np.ndarray, w: int, h: int) -> np.ndarray:
    y0 = max(0, (gray.shape[0] - h) // 2)
    x0 = max(0, (gray.shape[1] - w) // 2)
    return gray[y0:y0 + h, x0:x0 + w]


def build_contact_sheet(gray: np.ndarray, material: Material, dpi: float,
                        include_dither: bool = False) -> Image.Image:
    otsu_bin, otsu_cut = threshold(gray, "otsu", material, dpi)
    variants: list[tuple[str, np.ndarray]] = [
        (f"otsu ({int(otsu_cut)})", otsu_bin),
        ("adaptive", threshold(gray, "adaptive", material, dpi)[0]),
    ]
    for cut in material.contact_thresholds:
        variants.append(
            (f"manual {cut}", threshold(gray, "manual", material, dpi, cut)[0]))

    variants = [(name, despeckle(v, material, dpi), False)
                for name, v in variants]

    if include_dither:
        # Dither tiles are 1:1 center crops: downscaling a dither pattern
        # re-averages the dots back into gray and misrepresents the burn.
        # No despeckle here — isolated dots ARE the tonal signal.
        thumb_h = round(gray.shape[0] * _THUMB_W / gray.shape[1])
        crop = _center_crop(gray, _THUMB_W, thumb_h)
        dot_mm = 25.4 / dpi
        for mode in DITHER_MODES:
            note = " DOT<MIN!" if dot_mm < material.min_feature_mm else ""
            variants.append((f"{mode} 1:1 crop{note}", dither(crop, mode), True))

    scale = _THUMB_W / gray.shape[1]
    thumb_h = round(gray.shape[0] * scale)
    cols = 4
    rows = (len(variants) + cols - 1) // cols
    cell_w, cell_h = _THUMB_W + _PAD * 2, thumb_h + _LABEL_H + _PAD * 2
    sheet = Image.new("L", (cols * cell_w, rows * cell_h), 210)
    draw = ImageDraw.Draw(sheet)

    for i, (name, v, is_crop) in enumerate(variants):
        r, c = divmod(i, cols)
        x, y = c * cell_w + _PAD, r * cell_h + _PAD
        if is_crop:
            tile = np.full((thumb_h, _THUMB_W), 255, np.uint8)
            tile[:v.shape[0], :v.shape[1]] = v
        else:
            tile = cv2.resize(v, (_THUMB_W, thumb_h),
                              interpolation=cv2.INTER_AREA)
        ink_pct = 100 * (v == 0).sum() / v.size
        sheet.paste(Image.fromarray(tile), (x, y + _LABEL_H))
        draw.text((x + 2, y + 6), f"{name}  |  ink {ink_pct:.1f}%", fill=0)

    return sheet
