"""laserprep web — in-cluster HTTP front for the MCGT photo→laser pipeline.

Internal-only (ClusterIP). Two jobs:
  1. /prep       — run the deterministic tail (threshold → 1-bit / SVG /
                   contact sheet) on an uploaded image, entirely on-cluster.
  2. /stylize    — proxy a photo to the GPU stylization endpoint on the DGX
                   Spark (reached via the networking-egress ExternalName
                   service), then the result can be fed back through /prep.
                   Returns 503 until the Spark-side service exists (Phase 3).

Source of truth for the laserprep package is ~/workbench/manual_xtool; the
copy here is deployed via configMapGenerator (content-hash rolls the pod).
"""

import base64
import hashlib
import io
import json
import os
import urllib.request
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import cv2
import numpy as np
from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, Response
from PIL import Image

from laserprep import __version__
from laserprep.contact import build_contact_sheet
from laserprep.materials import load_materials
from laserprep.prep import (despeckle, feature_width_report, resize_physical,
                            threshold, to_1bit)
from laserprep.vectorize import trace_svg

STYLIZE_UPSTREAM = os.environ.get(
    "STYLIZE_UPSTREAM", "http://glm-spark1.networking:8001")

app = FastAPI(title="laserprep", version=__version__)
_cfg = os.environ.get("LASERPREP_CONFIG")
MATERIALS = load_materials(Path(_cfg) if _cfg else None)

_PAGE = """<!doctype html><meta charset="utf-8">
<title>laserprep</title>
<style>
 body {{ font: 15px/1.5 system-ui, sans-serif; max-width: 860px;
        margin: 2rem auto; padding: 0 1rem; }}
 label {{ display:block; margin-top:.7rem; }}
 img {{ max-width:100%; border:1px solid #ccc; margin:.5rem 0; }}
 .warn {{ background:#fff3cd; padding:.6rem; border-radius:6px; }}
 code {{ background:#eee; padding:0 .3em; }}
</style>
<h2>laserprep <small style="color:#888">{version}</small></h2>
{body}
"""


def _decode_upload(data: bytes) -> np.ndarray:
    arr = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_UNCHANGED)
    if arr is None:
        raise ValueError("could not decode image")
    if arr.ndim == 3 and arr.shape[2] == 4:  # alpha → composite on white
        alpha = arr[:, :, 3:4].astype(np.float32) / 255
        rgb = arr[:, :, :3].astype(np.float32)
        arr = (rgb * alpha + 255 * (1 - alpha)).astype(np.uint8)
    if arr.ndim == 3:
        arr = cv2.cvtColor(arr, cv2.COLOR_BGR2GRAY)
    return arr


@app.get("/healthz")
def healthz():
    return {"ok": True, "version": __version__}


def _upstream_ok() -> bool:
    try:
        with urllib.request.urlopen(STYLIZE_UPSTREAM + "/healthz", timeout=3) as r:
            return json.loads(r.read()).get("model_loaded", False)
    except Exception:
        return False


@app.get("/", response_class=HTMLResponse)
def index():
    opts = "".join(
        f'<option value="{m.name}">{m.name} — {m.description}</option>'
        for m in MATERIALS.values())
    up = _upstream_ok()
    up_note = ("🟢 stylize model loaded" if up else
               "🔴 stylize upstream not ready — raw photos can't be converted yet")
    body = f"""
<p>Pipeline: <b>photo → stylize (GPU line-art) → prep (threshold → burn files)</b>.<br>
Upload a photo with “stylize first” ticked, or upload existing line art directly.</p>
<form method="post" action="/prep" enctype="multipart/form-data">
 <label>Image <input type="file" name="file" required></label>
 <label><input type="checkbox" name="stylize_first" {"checked" if up else ""}>
   stylize photo first ({up_note})</label>
 <label><input type="checkbox" name="fast_stylize"> fast stylize (Lightning 4-step preview)</label>
 <label>Stylize seed <input type="number" name="seed" value="42" min="0" max="4294967295">
   <small>same photo + same seed = identical drawing (cached, instant on reorder);
   change it to get a different hatching interpretation</small></label>
 <label>Material <select name="material">{opts}</select></label>
 <label>Width (mm) <input type="number" name="width_mm" value="150" step="0.1" min="5" max="600"></label>
 <label>DPI <input type="number" name="dpi" value="318" step="1" min="72" max="1200"></label>
 <label>Strategy <select name="strategy">
   <option value="">material default</option>
   <option>otsu</option><option>adaptive</option><option>manual</option>
 </select></label>
 <label>Manual threshold (0–255, only for manual)
   <input type="number" name="manual_threshold" min="0" max="255" placeholder="material default"></label>
 <label><input type="checkbox" name="want_svg" checked> trace SVG (potrace)</label>
 <label><input type="checkbox" name="want_dither"> add dithering previews to
   contact sheet (Floyd/Jarvis/Stucki/Atkinson/Sierra/Bayer, 1:1 crops — slower)</label>
 <button style="margin-top:1rem">Process</button>
</form>
<p>Stylization upstream: <code>{STYLIZE_UPSTREAM}</code>
 (<a href="/stylize/status">status</a>)</p>"""
    return _PAGE.format(version=__version__, body=body)


@app.post("/prep", response_class=HTMLResponse)
async def prep(file: UploadFile = File(...),
               material: str = Form(...),
               width_mm: float = Form(150.0),
               dpi: float = Form(318.0),
               strategy: str = Form(""),
               manual_threshold: str = Form(""),
               want_svg: str = Form(""),
               want_dither: str = Form(""),
               stylize_first: str = Form(""),
               fast_stylize: str = Form(""),
               seed: int = Form(42)):
    data = await file.read()
    if len(data) > 30 * 2**20:
        return JSONResponse({"error": "upload > 30MB"}, status_code=413)
    mat = MATERIALS.get(material)
    if mat is None:
        return JSONResponse({"error": f"unknown material {material}"}, 422)
    cut = int(manual_threshold) if manual_threshold.strip() else None
    strat = strategy or ("manual" if cut is not None else mat.default_strategy)

    stylized_png = None
    stylize_cache = None
    if stylize_first:
        url = (f"{STYLIZE_UPSTREAM}/stylize?seed={seed}"
               + ("&fast=1" if fast_stylize else ""))
        req = urllib.request.Request(
            url, data=data, method="POST",
            headers={"Content-Type": "application/octet-stream"})
        try:
            with urllib.request.urlopen(req, timeout=900) as r:
                stylized_png = r.read()
                stylize_cache = r.headers.get("X-Stylize-Cache")
        except Exception as e:
            detail, hint = str(e), \
                "untick 'stylize first' to threshold the image as-is"
            body = getattr(e, "fp", None) and e.read()
            if body:
                try:
                    detail = json.loads(body)
                    if detail.get("error") == "model not loaded yet":
                        hint = ("the stylize model is (re)loading — takes "
                                "~4-5 min after a restart; retry shortly. "
                                "Previously cached photo+seed combos still "
                                "work during the reload.")
                except Exception:
                    detail = body.decode(errors="replace")[:300]
            return JSONResponse(
                {"error": "stylize upstream failed", "detail": detail,
                 "upstream": STYLIZE_UPSTREAM, "hint": hint},
                status_code=503)
        data = stylized_png  # prep the drawing, not the photo

    gray = resize_physical(_decode_upload(data), width_mm, dpi)
    binary, eff_cut = threshold(gray, strat, mat, dpi, cut)
    binary = despeckle(binary, mat, dpi)
    report = feature_width_report(binary, mat, dpi)
    height_mm = width_mm * binary.shape[0] / binary.shape[1]

    buf_1bit = io.BytesIO()
    to_1bit(binary, mat.invert).save(buf_1bit, format="PNG", dpi=(dpi, dpi))
    buf_sheet = io.BytesIO()
    build_contact_sheet(gray, mat, dpi, include_dither=bool(want_dither)
                        ).save(buf_sheet, format="PNG")
    svg = trace_svg(binary, width_mm, dpi, mat) if want_svg else None

    manifest = {
        "tool": "laserprep-web", "version": __version__,
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "input": {"filename": file.filename,
                  "sha256": hashlib.sha256(data).hexdigest()},
        "physical": {"width_mm": width_mm, "height_mm": round(height_mm, 3),
                     "dpi": dpi, "px": [binary.shape[1], binary.shape[0]]},
        "material": mat.__dict__, "strategy": strat, "manual_threshold": cut,
        "effective_threshold": eff_cut,
        "stylized_first": bool(stylized_png),
        "stylize_seed": seed if stylized_png else None,
        "stylize_cache": stylize_cache,
        "stylize_fast": bool(fast_stylize) if stylized_png else None,
        "feature_report": report,
    }

    stem = os.path.splitext(os.path.basename(file.filename or "image"))[0]
    zbuf = io.BytesIO()
    with zipfile.ZipFile(zbuf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(f"{stem}_1bit.png", buf_1bit.getvalue())
        z.writestr(f"{stem}_contact.png", buf_sheet.getvalue())
        if stylized_png:
            z.writestr(f"{stem}_stylized.png", stylized_png)
        if svg:
            z.writestr(f"{stem}.svg", svg)
        z.writestr(f"{stem}_run.json", json.dumps(manifest, indent=2))

    b64 = lambda b: base64.b64encode(b).decode()
    warn = ""
    if report["sub_min_feature_pct"] > 5:
        warn += (f'<p class="warn">{report["sub_min_feature_pct"]}% of ink is '
                 f'thinner than {mat.min_feature_mm}mm — may not survive the '
                 f'burn on {mat.name}.</p>')
    # Line art is bimodal (strokes + paper); photos live in the midtones.
    # Measured: synthetic line art 11% midtones, reference pet photo 81%.
    midtone_pct = 100 * ((gray > 64) & (gray < 192)).mean()
    if not stylized_png and midtone_pct > 40:
        warn += (f'<p class="warn">{midtone_pct:.0f}% of pixels are midtones — '
                 f'this looks like a raw photo, not line art. Thresholding a '
                 f'photo gives solid blobs; tick <b>stylize photo first</b> on '
                 f'the form to convert it to a pen-and-ink drawing first.</p>')
    stylized_html = ""
    if stylized_png:
        cache_note = " · served from cache" if stylize_cache == "hit" else ""
        stylized_html = (f'<h3>Stylized line art (input to prep) '
                         f'<small>seed {seed}{cache_note}</small></h3>'
                         f'<img src="data:image/png;base64,{b64(stylized_png)}">')
    body = f"""
<p><a href="/">&larr; another</a></p>
<p><b>{binary.shape[1]}×{binary.shape[0]}px = {width_mm:g}×{height_mm:.1f}mm
 @ {dpi:g}dpi</b> · {mat.name} · strategy {strat}{
    f" (cut {int(eff_cut)})" if eff_cut is not None else ""}</p>{warn}
<p><a download="{stem}_laserprep.zip"
      href="data:application/zip;base64,{b64(zbuf.getvalue())}">
   ⬇ download all ({len(zbuf.getvalue())//1024} KiB zip)</a></p>{stylized_html}
<h3>1-bit raster</h3><img src="data:image/png;base64,{b64(buf_1bit.getvalue())}">
<h3>Contact sheet — burn one tile, pick by eye</h3>
<img src="data:image/png;base64,{b64(buf_sheet.getvalue())}">"""
    return _PAGE.format(version=__version__, body=body)


@app.get("/stylize/status")
def stylize_status():
    try:
        with urllib.request.urlopen(STYLIZE_UPSTREAM + "/healthz", timeout=4) as r:
            return {"upstream": STYLIZE_UPSTREAM, "ok": True,
                    "detail": json.loads(r.read() or b"{}")}
    except Exception as e:
        return JSONResponse({"upstream": STYLIZE_UPSTREAM, "ok": False,
                             "detail": str(e)}, status_code=503)


@app.post("/stylize")
async def stylize(request: Request, file: UploadFile = File(...)):
    """Forward a photo to the Spark stylization service (Phase 3).
    Query params (seed, fast, steps, cfg, prompt) pass through unchanged."""
    data = await file.read()
    qs = str(request.query_params)
    req = urllib.request.Request(
        STYLIZE_UPSTREAM + "/stylize" + (f"?{qs}" if qs else ""),
        data=data, method="POST",
        headers={"Content-Type": file.content_type or "application/octet-stream"})
    try:
        with urllib.request.urlopen(req, timeout=600) as r:
            return Response(content=r.read(),
                            media_type=r.headers.get("Content-Type", "image/png"))
    except Exception as e:
        return JSONResponse({"error": f"stylize upstream unavailable: {e}",
                             "upstream": STYLIZE_UPSTREAM}, status_code=503)
