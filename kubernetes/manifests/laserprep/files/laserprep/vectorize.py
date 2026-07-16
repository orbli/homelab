"""Trace the 1-bit raster to SVG via potrace (pure-Python `potracer` backend).

The SVG carries explicit physical dimensions (mm) so xTool Studio imports it
at true size instead of guessing from pixels.
"""

from __future__ import annotations

import numpy as np
import potrace

from .materials import Material, mm2_to_px2


def _pt(p) -> tuple[float, float]:
    if hasattr(p, "x"):
        return (p.x, p.y)
    return (p[0], p[1])


def trace_svg(binary: np.ndarray, width_mm: float, dpi: float,
              material: Material) -> str:
    """binary: uint8 {0,255}, 0 = ink. Returns an SVG document string."""
    h, w = binary.shape
    height_mm = width_mm * h / w
    turdsize = max(1, round(mm2_to_px2(material.potrace_turdsize_mm2, dpi)))

    # potracer quirk (verified empirically): it traces FALSE/zero pixels as
    # foreground, the inverse of C potrace's convention. Ink is 0 in `binary`,
    # so pass `binary != 0` to trace the ink.
    bmp = potrace.Bitmap(binary != 0)
    path = bmp.trace(turdsize=turdsize, alphamax=1.0,
                     opticurve=True, opttolerance=0.2)

    d_parts: list[str] = []
    for curve in path:
        sx, sy = _pt(curve.start_point)
        d_parts.append(f"M{sx:.2f},{sy:.2f}")
        for seg in curve:
            ex, ey = _pt(seg.end_point)
            if seg.is_corner:
                cx, cy = _pt(seg.c)
                d_parts.append(f"L{cx:.2f},{cy:.2f}L{ex:.2f},{ey:.2f}")
            else:
                c1x, c1y = _pt(seg.c1)
                c2x, c2y = _pt(seg.c2)
                d_parts.append(
                    f"C{c1x:.2f},{c1y:.2f} {c2x:.2f},{c2y:.2f} {ex:.2f},{ey:.2f}")
        d_parts.append("Z")

    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" '
        f'width="{width_mm:.3f}mm" height="{height_mm:.3f}mm" '
        f'viewBox="0 0 {w} {h}">\n'
        f'<path d="{"".join(d_parts)}" fill="#000" fill-rule="evenodd" '
        f'stroke="none"/>\n</svg>\n'
    )
