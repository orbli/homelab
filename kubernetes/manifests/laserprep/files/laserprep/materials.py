"""Material profile loading. All size parameters are physical (mm / mm^2)."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path

DEFAULT_CONFIG = Path(__file__).resolve().parent.parent / "configs" / "materials.toml"


@dataclass(frozen=True)
class Material:
    name: str
    description: str
    default_strategy: str
    manual_threshold: int
    adaptive_block_mm: float
    adaptive_c: int
    min_feature_mm: float
    speck_area_mm2: float
    invert: bool
    potrace_turdsize_mm2: float
    contact_thresholds: list[int]


def load_materials(path: Path | None = None) -> dict[str, Material]:
    cfg_path = path or DEFAULT_CONFIG
    with open(cfg_path, "rb") as f:
        raw = tomllib.load(f)
    return {name: Material(name=name, **fields) for name, fields in raw.items()}


def mm_to_px(mm: float, dpi: float) -> float:
    return mm / 25.4 * dpi


def mm2_to_px2(mm2: float, dpi: float) -> float:
    scale = dpi / 25.4
    return mm2 * scale * scale
