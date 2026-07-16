"""Dithering: grayscale -> 1-bit preserving tone as dot density.

The error-diffusion family (Floyd-Steinberg, Jarvis, Stucki, Atkinson,
Sierra) is one algorithm with different diffusion kernels: walk the pixels,
snap each to black/white, push the rounding error onto unvisited neighbours.
Bayer is ordered dithering: threshold against a tiled fixed matrix.

Used for contact-sheet previews (1:1 crops — dither patterns cannot be
shown downscaled, shrinking re-averages the dots back into gray) and for
full dithered output. Despeckle must NOT run on dithered results: isolated
sub-minimum dots ARE the signal.

Note the physics: one dot = 25.4/dpi mm (0.08 mm @ 318 dpi). Materials with
min_feature_mm above that cannot hold isolated dots — expect dot loss or
tonal blur; burn a test tile before trusting any of these on real stock.
"""

from __future__ import annotations

import numpy as np

# kernel: list of (dy, dx, weight); divisor normalises.
_DIFFUSION_KERNELS: dict[str, tuple[list[tuple[int, int, int]], int]] = {
    "floyd": ([(0, 1, 7), (1, -1, 3), (1, 0, 5), (1, 1, 1)], 16),
    "jarvis": ([(0, 1, 7), (0, 2, 5),
                (1, -2, 3), (1, -1, 5), (1, 0, 7), (1, 1, 5), (1, 2, 3),
                (2, -2, 1), (2, -1, 3), (2, 0, 5), (2, 1, 3), (2, 2, 1)], 48),
    "stucki": ([(0, 1, 8), (0, 2, 4),
                (1, -2, 2), (1, -1, 4), (1, 0, 8), (1, 1, 4), (1, 2, 2),
                (2, -2, 1), (2, -1, 2), (2, 0, 4), (2, 1, 2), (2, 2, 1)], 42),
    "atkinson": ([(0, 1, 1), (0, 2, 1),
                  (1, -1, 1), (1, 0, 1), (1, 1, 1),
                  (2, 0, 1)], 8),
    "sierra": ([(0, 1, 5), (0, 2, 3),
                (1, -2, 2), (1, -1, 4), (1, 0, 5), (1, 1, 4), (1, 2, 2),
                (2, -1, 2), (2, 0, 3), (2, 1, 2)], 32),
}

_BAYER_8 = (1 + np.array([
    [0, 32,  8, 40,  2, 34, 10, 42],
    [48, 16, 56, 24, 50, 18, 58, 26],
    [12, 44,  4, 36, 14, 46,  6, 38],
    [60, 28, 52, 20, 62, 30, 54, 22],
    [3, 35, 11, 43,  1, 33,  9, 41],
    [51, 19, 59, 27, 49, 17, 57, 25],
    [15, 47,  7, 39, 13, 45,  5, 37],
    [63, 31, 55, 23, 61, 29, 53, 21]])) / 65.0

DITHER_MODES = ("floyd", "jarvis", "stucki", "atkinson", "sierra", "bayer")


def dither(gray: np.ndarray, mode: str) -> np.ndarray:
    """Dither 8-bit grayscale to {0, 255} uint8 (0 = ink)."""
    if mode == "bayer":
        h, w = gray.shape
        tiled = np.tile(_BAYER_8, (h // 8 + 1, w // 8 + 1))[:h, :w]
        return np.where(gray / 255.0 >= tiled, 255, 0).astype(np.uint8)

    if mode not in _DIFFUSION_KERNELS:
        raise ValueError(f"unknown dither mode: {mode}")
    kernel, divisor = _DIFFUSION_KERNELS[mode]
    h, w = gray.shape
    # Plain Python floats beat numpy scalar ops ~10x for this serial loop.
    rows = gray.astype(np.float32).tolist()
    out = np.empty((h, w), dtype=np.uint8)
    for y in range(h):
        row = rows[y]
        for x in range(w):
            old = row[x]
            new = 255.0 if old >= 128.0 else 0.0
            out[y, x] = int(new)
            err = (old - new) / divisor
            if err:
                for dy, dx, wt in kernel:
                    ny, nx = y + dy, x + dx
                    if 0 <= nx < w and ny < h:
                        rows[ny][nx] += err * wt
    return out
