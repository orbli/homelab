"""Raster preparation: physical sizing, thresholding, cleanup.

Everything here is deterministic. The run manifest (input hash + all
parameters) is enough to reproduce any output bit-for-bit.
"""

from __future__ import annotations

import cv2
import numpy as np
from PIL import Image, ImageOps

from .materials import Material, mm2_to_px2, mm_to_px


def load_grayscale(path: str) -> np.ndarray:
    """Load an image as 8-bit grayscale, honouring EXIF orientation."""
    img = Image.open(path)
    img = ImageOps.exif_transpose(img)
    if img.mode in ("RGBA", "LA", "P"):
        # Composite transparency onto white — matted subjects arrive with
        # alpha backgrounds and must become white paper, not black.
        img = img.convert("RGBA")
        bg = Image.new("RGBA", img.size, (255, 255, 255, 255))
        img = Image.alpha_composite(bg, img)
    return np.asarray(img.convert("L"))


def resize_physical(gray: np.ndarray, width_mm: float, dpi: float) -> np.ndarray:
    """Resize so the output is exactly width_mm wide at the given DPI."""
    target_w = max(1, round(mm_to_px(width_mm, dpi)))
    target_h = max(1, round(gray.shape[0] * target_w / gray.shape[1]))
    interp = cv2.INTER_AREA if target_w < gray.shape[1] else cv2.INTER_LANCZOS4
    return cv2.resize(gray, (target_w, target_h), interpolation=interp)


def threshold(gray: np.ndarray, strategy: str, material: Material, dpi: float,
              manual_value: int | None = None) -> np.ndarray:
    """Binarise. Returns uint8 array of {0, 255}; 0 = ink (burn), 255 = paper."""
    if strategy == "otsu":
        _, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    elif strategy == "adaptive":
        block = round(mm_to_px(material.adaptive_block_mm, dpi))
        block = max(3, block | 1)  # adaptiveThreshold needs an odd size >= 3
        binary = cv2.adaptiveThreshold(
            gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY,
            block, material.adaptive_c)
    elif strategy == "manual":
        cut = material.manual_threshold if manual_value is None else manual_value
        _, binary = cv2.threshold(gray, cut, 255, cv2.THRESH_BINARY)
    else:
        raise ValueError(f"unknown threshold strategy: {strategy}")
    return binary


def despeckle(binary: np.ndarray, material: Material, dpi: float) -> np.ndarray:
    """Remove islands below the material's engravable area, both polarities."""
    min_area = mm2_to_px2(material.speck_area_mm2, dpi)
    out = binary.copy()
    for invert in (False, True):
        work = cv2.bitwise_not(out) if not invert else out
        # connectedComponents labels WHITE regions, so flip to label ink first,
        # then flip again to label pinholes.
        n, labels, stats, _ = cv2.connectedComponentsWithStats(work, connectivity=8)
        kill = np.isin(labels, [i for i in range(1, n)
                                if stats[i, cv2.CC_STAT_AREA] < min_area])
        out[kill] = 255 if not invert else 0
    return out


def feature_width_report(binary: np.ndarray, material: Material, dpi: float) -> dict:
    """Estimate how much ink is thinner than the material can hold.

    Erodes the ink by the minimum feature radius; ink that disappears entirely
    under erosion is thinner than min_feature_mm and will likely burn out.
    """
    radius_px = mm_to_px(material.min_feature_mm, dpi) / 2
    k = max(1, round(radius_px))
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * k + 1, 2 * k + 1))
    ink = binary == 0
    survived = cv2.erode(ink.astype(np.uint8), kernel).astype(bool)
    # Dilate survivors back: ink pixels with no surviving core are sub-minimum.
    core_coverage = cv2.dilate(survived.astype(np.uint8), kernel,
                               iterations=2).astype(bool)
    thin = ink & ~core_coverage
    total = int(ink.sum())
    return {
        "ink_px": total,
        "sub_min_feature_px": int(thin.sum()),
        "sub_min_feature_pct": round(100 * thin.sum() / total, 2) if total else 0.0,
        "min_feature_mm": material.min_feature_mm,
    }


def to_1bit(binary: np.ndarray, invert: bool) -> Image.Image:
    """Convert {0,255} array to a 1-bit PIL image, applying material polarity."""
    arr = cv2.bitwise_not(binary) if invert else binary
    return Image.fromarray(arr).convert("1")
