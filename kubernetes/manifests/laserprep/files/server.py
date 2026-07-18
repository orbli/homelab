"""laserprep web — thin portal for the MCGT photo→laser pipeline.

Flow: upload photo → (optional) proxy to the in-cluster stylize service
(which drives spark1's headless ComfyUI and owns the render cache) →
result page where THE BROWSER does all post-processing live (threshold /
dither / invert, via prep.js + canvas) and saves a laser-ready PNG with
pHYs DPI + tEXt params spliced in client-side.

The reproducible artifact is the stylized art in the stylize service's
cache PVC (keyed input-hash + seed + steps + recipe-version + cfg); the
model knobs on the form pass straight through, and threshold/dither are
cheap deterministic knobs recorded in the downloaded file's metadata.

Server responsibilities: serve the page/JS, proxy stylize, nothing else —
no numpy/opencv needed. Full server-side pipeline (despeckle, SVG, contact
sheet) lives in the manual_xtool CLI.
"""

import base64
import hashlib
import html
import json
import os
import tomllib
import urllib.parse
import urllib.request
from pathlib import Path

from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response

STYLIZE_UPSTREAM = os.environ.get(
    "STYLIZE_UPSTREAM", "http://stylize:8001")
CONFIG = Path(os.environ.get("LASERPREP_CONFIG", "/app/configs/materials.toml"))
APP_DIR = Path(__file__).resolve().parent

app = FastAPI(title="laserprep", version="0.2.0")
MATERIALS: dict = tomllib.loads(CONFIG.read_text())

_PAGE = """<!doctype html><meta charset="utf-8">
<title>laserprep</title>
<style>
 body {{ font: 15px/1.5 system-ui, sans-serif; max-width: 980px;
        margin: 2rem auto; padding: 0 1rem; }}
 label {{ display:block; margin-top:.7rem; }}
 img, canvas {{ max-width:100%; border:1px solid #ccc; margin:.5rem 0;
               image-rendering: pixelated; }}
 .warn {{ background:#fff3cd; padding:.6rem; border-radius:6px; }}
 .row {{ display:flex; gap:1rem; align-items:center; flex-wrap:wrap;
        margin:.6rem 0; }}
 .pane {{ margin-bottom:1rem; }}
 .cap {{ font-size:.85em; color:#555; margin-bottom:.2rem; }}
 hr {{ border:none; border-top:2px solid #ddd; margin:1.5rem 0; }}
 code {{ background:#eee; padding:0 .3em; }}
 button {{ padding:.4rem 1rem; }}
 textarea {{ width:100%; font:13px/1.4 ui-monospace, monospace; }}
 details {{ margin-top:.8rem; }}
 summary {{ cursor:pointer; color:#555; }}
</style>
<h2>laserprep <small style="color:#888">{version}</small></h2>
{body}
"""


def _upstream_health() -> dict:
    try:
        with urllib.request.urlopen(STYLIZE_UPSTREAM + "/healthz", timeout=3) as r:
            return json.loads(r.read())
    except Exception as e:
        return {"model_loaded": False, "error": str(e)}


def _stylize_info() -> dict:
    """Recipe metadata (prompts, defaults) from the stylize service — the
    portal displays it but never owns it; the recipe lives with the cache."""
    try:
        with urllib.request.urlopen(STYLIZE_UPSTREAM + "/info", timeout=3) as r:
            return json.loads(r.read())
    except Exception:
        return {"house_prompt": "", "negative_prompt": "",
                "defaults": {"steps": 40, "cfg": 4.0,
                             "fast_steps": 4, "fast_cfg": 1.0}}


@app.get("/healthz")
def healthz():
    return {"ok": True, "version": app.version}


@app.get("/prep.js")
def prep_js():
    return FileResponse(APP_DIR / "prep.js", media_type="text/javascript")


@app.get("/", response_class=HTMLResponse)
def index():
    opts = "".join(
        f'<option value="{name}">{name} — {m.get("description", "")}</option>'
        for name, m in MATERIALS.items())
    up = _upstream_health().get("model_loaded", False)
    up_note = ("🟢 model backend (ComfyUI) reachable" if up else
               "🔴 stylize backend not ready — raw photos can't be converted yet")
    info = _stylize_info()
    d = info["defaults"]
    body = f"""
<p>Pipeline: <b>photo → stylize (GPU line art) → tune &amp; save in the browser</b>.<br>
Threshold/dither happen live on the result page — nothing to choose here.</p>
<form method="post" action="/prep" enctype="multipart/form-data">
 <label>Image <input type="file" name="file" required></label>
 <label><input type="checkbox" name="stylize_first" {"checked" if up else ""}>
   stylize photo first ({up_note})</label>
 <label><input type="checkbox" name="fast_stylize"> fast stylize (Lightning 4-step preview)</label>
 <label>Stylize seed <input type="number" name="seed" value="42" min="0" max="4294967295">
   <small>same photo + same seed = identical drawing (cached, instant on reorder)</small></label>
 <label>Material <select name="material">{opts}</select></label>
 <label>Width (mm) <input type="number" name="width_mm" value="150" step="0.1" min="5" max="600"></label>
 <label>DPI <input type="number" name="dpi" value="318" step="1" min="72" max="1200">
   <small>cork wants ~305 (120 lines/cm)</small></label>
 <details>
  <summary>Model knobs — house recipe shown; edit only to experiment</summary>
  <label>Prompt
   <textarea name="prompt" rows="5">{html.escape(info["house_prompt"])}</textarea></label>
  <label>Negative prompt <small>(active only with CFG &gt; 1; fast mode ignores it)</small>
   <textarea name="neg" rows="3">{html.escape(info["negative_prompt"])}</textarea></label>
  <div class="row">
   <label>Steps <input type="number" name="steps" min="1" max="100"
     placeholder="{d['steps']} ({d['fast_steps']} fast)"></label>
   <label>CFG <input type="number" name="cfg" min="1" max="10" step="0.5"
     placeholder="{d['cfg']:g} ({d['fast_cfg']:g} fast)"></label>
  </div>
  <p><small>CFG &gt; 1 ≈ doubles render time (two model passes per step).
  Blank fields use the defaults. Every distinct recipe is cached under its
  own version hash — experiments never overwrite house renders.</small></p>
 </details>
 <button style="margin-top:1rem">Process</button>
</form>
<p>Stylization upstream: <code>{STYLIZE_UPSTREAM}</code>
 (<a href="/stylize/status">status</a>)</p>"""
    return _PAGE.format(version=app.version, body=body)


@app.post("/prep", response_class=HTMLResponse)
async def prep(file: UploadFile = File(...),
               material: str = Form(...),
               width_mm: float = Form(150.0),
               dpi: float = Form(318.0),
               stylize_first: str = Form(""),
               fast_stylize: str = Form(""),
               seed: int = Form(42),
               prompt: str = Form(""),
               neg: str = Form(""),
               steps: str = Form(""),
               cfg: str = Form("")):
    data = await file.read()
    if len(data) > 30 * 2**20:
        return JSONResponse({"error": "upload > 30MB"}, status_code=413)
    mat = MATERIALS.get(material)
    if mat is None:
        return JSONResponse({"error": f"unknown material {material}"}, 422)
    input_sha = hashlib.sha256(data).hexdigest()

    art, render_info, cache_state = data, {}, None
    if stylize_first:
        q = {"seed": seed}
        if fast_stylize:
            q["fast"] = 1
        try:
            if steps.strip():
                q["steps"] = int(steps)
            if cfg.strip():
                q["cfg"] = float(cfg)
        except ValueError:
            return JSONResponse({"error": "steps/cfg must be numeric"}, 422)
        if prompt.strip():
            q["prompt"] = prompt.strip()
        if neg.strip():
            q["neg"] = neg.strip()
        url = f"{STYLIZE_UPSTREAM}/stylize?" + urllib.parse.urlencode(q)
        req = urllib.request.Request(
            url, data=data, method="POST",
            headers={"Content-Type": "application/octet-stream"})
        try:
            with urllib.request.urlopen(req, timeout=1200) as r:
                art = r.read()
                cache_state = r.headers.get("X-Stylize-Cache")
                render_info = json.loads(r.headers.get("X-Render-Manifest", "{}"))
        except Exception as e:
            detail, hint = str(e), \
                "untick 'stylize first' to process the image as-is"
            body = getattr(e, "fp", None) and e.read()
            if body:
                try:
                    detail = json.loads(body)
                    if detail.get("error") == "model backend unreachable":
                        hint = ("spark1's ComfyUI is down or restarting — "
                                "check the comfyui container. Cached "
                                "photo+seed combos still work meanwhile.")
                except Exception:
                    detail = body.decode(errors="replace")[:300]
            return JSONResponse(
                {"error": "stylize upstream failed", "detail": detail,
                 "upstream": STYLIZE_UPSTREAM, "hint": hint}, status_code=503)

    stem = os.path.splitext(os.path.basename(file.filename or "image"))[0]
    mime = "image/png" if art[:8] == b"\x89PNG\r\n\x1a\n" else "image/jpeg"
    lp = {
        "artSrc": f"data:{mime};base64,{base64.b64encode(art).decode()}",
        "width_mm": width_mm, "dpi": dpi,
        "material": {"name": material, **mat},
        "seed": seed, "steps": render_info.get("steps"),
        "true_cfg": render_info.get("cfg"),
        "prompt": prompt.strip() or None, "neg": neg.strip() or None,
        "input_sha256": input_sha, "stem": stem,
        "stylized": bool(stylize_first),
    }
    cache_note = " · served from cache" if cache_state == "hit" else ""
    recipe = (f'seed {seed} · steps {render_info.get("steps", "?")} · '
              f'cfg {render_info.get("cfg", "?")}'
              + (" · custom prompt" if prompt.strip() or neg.strip() else ""))
    sty_head = (f'<h3>Stylized line art <small>{recipe}{cache_note} · '
                f'<a id="openart" href="#" target="_blank">open full</a></small></h3>'
                if stylize_first else
                '<h3>Input art <small><a id="openart" href="#" target="_blank">'
                'open full</a></small></h3>')
    seed_note = f" · seed <b>{seed}</b>" if stylize_first else ""
    body = f"""
<p><a href="/">&larr; another</a></p>
<p><b id="dims">computing…</b> · {material}{seed_note}</p>
<div id="warn"></div>
<div class="row">
 <label>Mode <select id="mode">
  <option value="otsu">otsu</option>
  <option value="manual">manual</option>
  <option value="adaptive">adaptive</option>
  <option value="floyd">floyd dither</option>
  <option value="jarvis">jarvis dither</option>
  <option value="stucki">stucki dither</option>
  <option value="atkinson">atkinson dither</option>
  <option value="sierra">sierra dither</option>
  <option value="bayer">bayer dither</option>
 </select></label>
 <label>Cut <input type="range" id="cut" min="0" max="255" value="128"
   style="width:180px"> <span id="cutv"></span></label>
 <span id="ink"></span>
 <button id="save">💾 Save for laser (PNG + DPI)</button>
 <a id="openfull" href="#" target="_blank">open full</a>
 <button id="snapbtn" title="pin the current config/result for side-by-side comparison">📌 Snapshot</button>
</div>
<div class="pane">
 <div class="cap" id="livecap">live</div>
 <canvas id="preview"></canvas>
</div>
<div class="pane" id="snappane" style="display:none">
 <div class="cap"><span id="snapcap"></span> ·
  <a id="opensnap" href="#" target="_blank">open full</a> ·
  <a id="snapclear" href="#">clear</a></div>
 <canvas id="snap"></canvas>
</div>
<hr>
{sty_head}
<img id="art">
<p><small>Dither dots are {25.4 / dpi:.3f}mm at {dpi:g}dpi; this material holds
features ≥ {mat.get("min_feature_mm", "?")}mm — burn a test tile before
trusting dithered tone. Saved PNG embeds DPI + all parameters.</small></p>
<script>window.LP = {json.dumps(lp)};</script>
<script src="/prep.js"></script>"""
    return _PAGE.format(version=app.version, body=body)


@app.get("/stylize/status")
def stylize_status():
    h = _upstream_health()
    code = 200 if h.get("model_loaded") else 503
    return JSONResponse({"upstream": STYLIZE_UPSTREAM,
                         "ok": h.get("model_loaded", False), "detail": h}, code)


@app.post("/stylize")
async def stylize(request: Request, file: UploadFile = File(...)):
    """Raw proxy to the Spark stylize service; query params pass through."""
    data = await file.read()
    qs = str(request.query_params)
    req = urllib.request.Request(
        STYLIZE_UPSTREAM + "/stylize" + (f"?{qs}" if qs else ""),
        data=data, method="POST",
        headers={"Content-Type": file.content_type or "application/octet-stream"})
    try:
        with urllib.request.urlopen(req, timeout=1200) as r:
            return Response(content=r.read(),
                            media_type=r.headers.get("Content-Type", "image/png"),
                            headers={k: v for k, v in r.headers.items()
                                     if k.lower().startswith("x-")})
    except Exception as e:
        return JSONResponse({"error": f"stylize upstream unavailable: {e}",
                             "upstream": STYLIZE_UPSTREAM}, status_code=503)
