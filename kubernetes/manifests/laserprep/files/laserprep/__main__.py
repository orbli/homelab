"""laserprep — deterministic tail of the photo→laser pipeline.

    python -m laserprep input.png --material birch-3mm --width-mm 150 --dpi 318

Outputs (next to input unless --out-dir):
    <stem>_1bit.png     laser-ready 1-bit raster, DPI embedded
    <stem>.svg          potrace vector, physical mm dimensions
    <stem>_contact.png  labelled grid of threshold variants
    <stem>_run.json     reproducibility manifest (input hash + all params)
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from . import __version__
from .contact import build_contact_sheet
from .materials import DEFAULT_CONFIG, load_materials
from .prep import (despeckle, feature_width_report, load_grayscale,
                   resize_physical, threshold, to_1bit)
from .vectorize import trace_svg


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="laserprep", description=__doc__.split("\n")[0])
    ap.add_argument("input", help="input image (the stylized line-art PNG)")
    ap.add_argument("--material", required=True, help="profile from materials.toml")
    ap.add_argument("--width-mm", type=float, required=True,
                    help="physical output width in mm")
    ap.add_argument("--dpi", type=float, default=318.0,
                    help="raster resolution (default 318 = 0.08mm laser spot)")
    ap.add_argument("--strategy", choices=["otsu", "adaptive", "manual"],
                    help="override the material's default threshold strategy")
    ap.add_argument("--threshold", type=int, dest="manual_threshold",
                    help="manual cut point 0-255 (implies --strategy manual)")
    ap.add_argument("--out-dir", type=Path, help="output directory")
    ap.add_argument("--config", type=Path, default=DEFAULT_CONFIG,
                    help="materials.toml path")
    ap.add_argument("--no-svg", action="store_true", help="skip potrace vector")
    ap.add_argument("--dither-preview", action="store_true",
                    help="add dithering tiles (1:1 crops) to the contact sheet")
    args = ap.parse_args(argv)

    materials = load_materials(args.config)
    if args.material not in materials:
        ap.error(f"unknown material {args.material!r}; "
                 f"available: {', '.join(sorted(materials))}")
    mat = materials[args.material]
    strategy = args.strategy or ("manual" if args.manual_threshold is not None
                                 else mat.default_strategy)

    in_path = Path(args.input)
    out_dir = args.out_dir or in_path.parent
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = in_path.stem

    gray = load_grayscale(str(in_path))
    gray = resize_physical(gray, args.width_mm, args.dpi)
    binary, cut = threshold(gray, strategy, mat, args.dpi, args.manual_threshold)
    binary = despeckle(binary, mat, args.dpi)
    report = feature_width_report(binary, mat, args.dpi)

    height_mm = args.width_mm * binary.shape[0] / binary.shape[1]

    p_1bit = out_dir / f"{stem}_1bit.png"
    to_1bit(binary, mat.invert).save(p_1bit, dpi=(args.dpi, args.dpi))

    p_svg = None
    if not args.no_svg:
        p_svg = out_dir / f"{stem}.svg"
        p_svg.write_text(trace_svg(binary, args.width_mm, args.dpi, mat))

    p_contact = out_dir / f"{stem}_contact.png"
    build_contact_sheet(gray, mat, args.dpi,
                        include_dither=args.dither_preview).save(p_contact)

    manifest = {
        "tool": "laserprep",
        "version": __version__,
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "input": {"path": str(in_path),
                  "sha256": hashlib.sha256(in_path.read_bytes()).hexdigest()},
        "physical": {"width_mm": args.width_mm, "height_mm": round(height_mm, 3),
                     "dpi": args.dpi,
                     "px": [binary.shape[1], binary.shape[0]]},
        "material": mat.__dict__,
        "strategy": strategy,
        "manual_threshold": args.manual_threshold,
        "effective_threshold": cut,
        "feature_report": report,
        "outputs": {"raster_1bit": str(p_1bit),
                    "svg": str(p_svg) if p_svg else None,
                    "contact_sheet": str(p_contact)},
    }
    p_manifest = out_dir / f"{stem}_run.json"
    p_manifest.write_text(json.dumps(manifest, indent=2))

    print(f"1-bit raster : {p_1bit}  ({binary.shape[1]}x{binary.shape[0]}px "
          f"= {args.width_mm}x{height_mm:.1f}mm @ {args.dpi:g}dpi)")
    if p_svg:
        print(f"vector       : {p_svg}")
    print(f"contact sheet: {p_contact}")
    print(f"manifest     : {p_manifest}")
    if report["sub_min_feature_pct"] > 5:
        print(f"WARNING: {report['sub_min_feature_pct']}% of ink is thinner than "
              f"{mat.min_feature_mm}mm and may not survive the burn on "
              f"{mat.name}. Consider lower DPI art or a bolder style.",
              file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
