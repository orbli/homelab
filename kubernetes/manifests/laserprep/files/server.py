"""laserprep web — thin portal for the MCGT photo→laser pipeline.

Flow: upload photo → BiRefNet mask pre-fills a draggable crop box
(index.js) → submit → the stylize service renders asynchronously on
spark1's ComfyUI (subject-cropped, shadow-killed, RGBA whose alpha is the
subject mask) → result page polls progress, then THE BROWSER does all
post-processing live (invert → levels/gamma → unsharp → binarize →
morphology → despeckle, via prep.js + canvas) and saves a laser-ready PNG
with pHYs DPI + tEXt params spliced in client-side.

The reproducible artifact is the stylized art in the stylize service's
cache PVC (keyed input-hash + seed + steps + recipe-version + cfg); the
model knobs on the form pass straight through, and every raster knob is
recorded in the downloaded file's metadata (and restorable from it).

Server responsibilities: serve the pages/JS, proxy stylize, nothing else —
no numpy/opencv needed (image work lives on the GPU box or in the
browser). Knob tiers per docs/REDESIGN.md: tier 1 always visible, tier 2
"Model", tier 3 "Advanced raster".
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

app = FastAPI(title="laserprep", version="0.3.1")
MATERIALS: dict = tomllib.loads(CONFIG.read_text())

# Cache-buster: browsers heuristically cache JS for ~10% of its mtime age
# (no Cache-Control header), so a redeploy can leave clients running old
# prep.js against new HTML — the result page then never polls its job.
_JS_VER = hashlib.sha256(
    (APP_DIR / "prep.js").read_bytes() +
    (APP_DIR / "index.js").read_bytes()).hexdigest()[:8]
_NO_CACHE = {"Cache-Control": "no-cache"}

_PAGE = """<!doctype html><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>laserprep</title>
<style>
 body {{ font: 15px/1.5 system-ui, sans-serif; max-width: 980px;
        margin: 2rem auto; padding: 0 1rem; }}
 label {{ display:block; margin-top:.7rem; }}
 img, canvas {{ max-width:100%; border:1px solid #ccc; margin:.5rem 0;
               image-rendering: pixelated; background:#fff; }}
 .warn {{ background:#fff3cd; padding:.6rem; border-radius:6px; }}
 .warn2 {{ color:#b50; font-weight:600; }}
 .row {{ display:flex; gap:1rem; align-items:center; flex-wrap:wrap;
        margin:.6rem 0; }}
 .row label {{ margin-top:0; }}
 .pane {{ margin-bottom:1rem; }}
 .cap {{ font-size:.85em; color:#555; margin-bottom:.2rem; }}
 hr {{ border:none; border-top:2px solid #ddd; margin:1.5rem 0; }}
 code {{ background:#eee; padding:0 .3em; }}
 button {{ padding:.4rem 1rem; }}
 textarea {{ width:100%; font:13px/1.4 ui-monospace, monospace; }}
 details {{ margin-top:.8rem; }}
 summary {{ cursor:pointer; color:#555; font-weight:600; }}
 #cropwrap {{ position:relative; }}
 #cropcv {{ cursor:move; touch-action:none; }}
 #seedgrid {{ display:grid; grid-template-columns:1fr 1fr; gap:.6rem;
             margin:.6rem 0; }}
 .seedcell {{ border:2px solid #ccc; border-radius:6px; padding:.3rem;
             cursor:pointer; }}
 .seedcell.picked {{ border-color:#28a; box-shadow:0 0 0 2px #28a4; }}
 .seedcell img {{ border:none; margin:0; }}
 #progress {{ background:#eee; border-radius:6px; margin:.8rem 0;
             overflow:hidden; }}
 #progbar {{ background:#28a; height:8px; width:2%; transition:width .5s; }}
 #progtext {{ padding:.4rem .6rem; font-size:.9em; color:#333; }}
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
    """Recipe metadata (prompts, presets, defaults) from the stylize
    service — the portal displays it but never owns it."""
    try:
        with urllib.request.urlopen(STYLIZE_UPSTREAM + "/info", timeout=3) as r:
            return json.loads(r.read())
    except Exception:
        return {"house_prompt": "", "negative_prompt": "", "presets": {},
                "defaults": {"steps": 40, "cfg": 4.0,
                             "fast_steps": 4, "fast_cfg": 1.0, "mp": 1.0}}


@app.get("/healthz")
def healthz():
    return {"ok": True, "version": app.version}


@app.get("/prep.js")
def prep_js():
    return FileResponse(APP_DIR / "prep.js", media_type="text/javascript",
                        headers=_NO_CACHE)


@app.get("/index.js")
def index_js():
    return FileResponse(APP_DIR / "index.js", media_type="text/javascript",
                        headers=_NO_CACHE)


@app.get("/", response_class=HTMLResponse)
def index():
    opts = "".join(
        f'<option value="{name}" data-dpi="{m.get("default_dpi", 318)}">'
        f'{name} — {m.get("description", "")}</option>'
        for name, m in MATERIALS.items())
    up = _upstream_health().get("model_loaded", False)
    up_note = ("🟢 model backend (ComfyUI) reachable" if up else
               "🔴 stylize backend not ready — raw photos can't be converted yet")
    info = _stylize_info()
    d = info["defaults"]
    preset_opts = "".join(
        f'<option value="{k}">{html.escape(v["label"])}</option>'
        for k, v in info.get("presets", {}).items())
    body = f"""
<p>Pipeline: <b>photo → subject crop → stylize (GPU line art) → tune &amp;
save in the browser</b>. Threshold/dither happen live on the result page.</p>
<form method="post" action="/prep" enctype="multipart/form-data">
 <label>Image <input type="file" id="file" name="file" required>
   <small>(JPEG/PNG/WebP — iPhone HEIC must be exported as JPEG first; drop a
   previously saved laserprep PNG to restore its settings)</small></label>
 <div id="cropwrap" style="display:none">
  <img id="photo" style="display:none" alt="">
  <canvas id="cropcv"></canvas>
  <div class="row">
   <span class="cap" id="cropstatus"></span>
   <label>Aspect <select id="aspect" name="aspect_sel">
     <option value="free">free</option><option value="1:1">1:1 (coaster)</option>
     <option value="4:5">4:5</option><option value="5:4">5:4</option>
     <option value="3:2">3:2</option><option value="2:3">2:3</option>
   </select></label>
   <a href="#" id="cropreset">reset crop</a>
  </div>
 </div>
 <input type="hidden" id="crop" name="crop" value="">
 <input type="hidden" id="shadow_lift" name="shadow_lift" value="1.0">
 <label><input type="checkbox" name="stylize_first" {"checked" if up else ""}>
   stylize photo first ({up_note})</label>
 <label>Material <select id="material" name="material">{opts}</select></label>
 <label>Width (mm) <input type="number" id="width_mm" name="width_mm"
   value="150" step="0.1" min="5" max="600"></label>
 <details open>
  <summary>Model</summary>
  <div class="row">
   <label>Preset <select id="preset" name="preset">{preset_opts}</select></label>
   <label>Seed <input type="number" id="seed" name="seed" value="42" min="0"
     max="4294967295"></label>
   <button id="seedprev" title="four Lightning previews (~30s each), click one to pick its seed">🎲 Preview 4 seeds</button>
  </div>
  <div id="seedgrid" style="display:none"></div>
  <label><input type="checkbox" name="fast_stylize"> fast stylize (Lightning
    4-step — final render, not just preview)</label>
  <label>Prompt
   <textarea id="prompt" name="prompt" rows="5">{html.escape(info["house_prompt"])}</textarea></label>
  <label>Negative prompt <small>(active only with CFG &gt; 1; fast mode ignores it)</small>
   <textarea id="neg" name="neg" rows="3">{html.escape(info["negative_prompt"])}</textarea></label>
  <div class="row">
   <label>Steps <input type="number" id="steps" name="steps" min="1" max="100"
     placeholder="{d['steps']} ({d['fast_steps']} fast)"></label>
   <label>CFG <input type="number" id="cfg" name="cfg" min="1" max="10" step="0.5"
     placeholder="{d['cfg']:g} ({d['fast_cfg']:g} fast)"></label>
  </div>
 </details>
 <details>
  <summary>Advanced</summary>
  <div class="row">
   <label>DPI <input type="number" id="dpi" name="dpi" value="318" step="1"
     min="72" max="1200"> <small>cork wants 305</small></label>
   <label>Working MP <input type="number" name="mp" min="0.5" max="3.5"
     step="0.1" placeholder="{d.get('mp', 1.0):g}"
     title="model working resolution in megapixels; higher = finer strokes but slower"></label>
  </div>
  <p><small>Every distinct recipe (crop box included) is cached under its own
  version hash — experiments never overwrite house renders; same photo + same
  settings is instant.</small></p>
 </details>
 <button style="margin-top:1rem">Process</button>
</form>
<script>window.LP_PRESETS = {json.dumps(
    {k: {"prompt": v["prompt"], "neg": v["neg"],
         "shadow_lift": v["shadow_lift"]}
     for k, v in info.get("presets", {}).items()})};</script>
<script src="/prep.js?v={_JS_VER}"></script>
<script src="/index.js?v={_JS_VER}"></script>
<p>Stylization upstream: <code>{STYLIZE_UPSTREAM}</code>
 (<a href="/stylize/status">status</a>)</p>"""
    return _PAGE.format(version=app.version, body=body)


def _mode_options(stylized: bool) -> str:
    """Context-aware ordering (REDESIGN P1-3): line art wants thresholds,
    raw photos want error diffusion. Everything stays available."""
    thresh = [("otsu", "otsu"), ("manual", "manual"), ("adaptive", "adaptive")]
    dither = [("floyd", "floyd dither"), ("jarvis", "jarvis dither"),
              ("stucki", "stucki dither"), ("atkinson", "atkinson dither"),
              ("sierra", "sierra dither"), ("bayer", "bayer dither")]
    first, second = (thresh, dither) if stylized else (dither, thresh)
    g1 = "".join(f'<option value="{v}">{t}</option>' for v, t in first)
    g2 = "".join(f'<option value="{v}">{t}</option>' for v, t in second)
    lbl = "more modes"
    return f'{g1}<optgroup label="{lbl}">{g2}</optgroup>'


@app.post("/prep", response_class=HTMLResponse)
async def prep(file: UploadFile = File(...),
               material: str = Form(...),
               width_mm: float = Form(150.0),
               dpi: float = Form(318.0),
               stylize_first: str = Form(""),
               fast_stylize: str = Form(""),
               seed: int = Form(42),
               preset: str = Form(""),
               prompt: str = Form(""),
               neg: str = Form(""),
               steps: str = Form(""),
               cfg: str = Form(""),
               crop: str = Form(""),
               aspect_sel: str = Form("free"),
               shadow_lift: str = Form("1.0"),
               mp: str = Form("")):
    data = await file.read()
    if len(data) > 30 * 2**20:
        return JSONResponse({"error": "upload > 30MB"}, status_code=413)
    mat = MATERIALS.get(material)
    if mat is None:
        return JSONResponse({"error": f"unknown material {material}"}, 422)
    input_sha = hashlib.sha256(data).hexdigest()
    info = _stylize_info()
    d = info["defaults"]

    job, art = None, data
    n_steps, n_cfg, n_mp = None, None, None
    if stylize_first:
        q = {"seed": seed}
        if fast_stylize:
            q["fast"] = 1
        try:
            if steps.strip():
                q["steps"] = n_steps = int(steps)
            if cfg.strip():
                q["cfg"] = n_cfg = float(cfg)
            if mp.strip():
                q["mp"] = n_mp = float(mp)
            sl = float(shadow_lift or 1.0)
        except ValueError:
            return JSONResponse({"error": "steps/cfg/mp must be numeric"}, 422)
        if prompt.strip():
            q["prompt"] = prompt.strip()
        if neg.strip():
            q["neg"] = neg.strip()
        if crop.strip():
            q["crop"] = crop.strip()
        elif aspect_sel != "free" and ":" in aspect_sel:
            a, b = aspect_sel.split(":")
            q["aspect"] = round(float(a) / float(b), 4)
        if sl < 1.0:
            q["shadow_lift"] = sl
        url = f"{STYLIZE_UPSTREAM}/jobs?" + urllib.parse.urlencode(q)
        req = urllib.request.Request(
            url, data=data, method="POST",
            headers={"Content-Type": "application/octet-stream"})
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                job = json.loads(r.read())
        except Exception as e:
            detail, hint = str(e), \
                "untick 'stylize first' to process the image as-is"
            body_b = getattr(e, "fp", None) and e.read()
            if body_b:
                try:
                    detail = json.loads(body_b)
                except Exception:
                    detail = body_b.decode(errors="replace")[:300]
            return JSONResponse(
                {"error": "stylize upstream failed", "detail": detail,
                 "upstream": STYLIZE_UPSTREAM, "hint": hint}, status_code=503)

    fast = bool(fast_stylize)
    disp_steps = n_steps or (d["fast_steps"] if fast else d["steps"])
    disp_cfg = n_cfg or (d["fast_cfg"] if fast else d["cfg"])
    stem = os.path.splitext(os.path.basename(file.filename or "image"))[0]
    mime = "image/png" if art[:8] == b"\x89PNG\r\n\x1a\n" else "image/jpeg"
    lp = {
        "artSrc": (None if job else
                   f"data:{mime};base64,{base64.b64encode(art).decode()}"),
        "job": job,
        "width_mm": width_mm, "dpi": dpi,
        "material": {"name": material, **mat},
        "seed": seed, "steps": disp_steps if stylize_first else None,
        "true_cfg": disp_cfg if stylize_first else None,
        "mp": (n_mp or d.get("mp", 1.0)) if stylize_first else None,
        "preset": preset or None,
        "prompt": prompt.strip() or None, "neg": neg.strip() or None,
        "crop": crop.strip() or None,
        "input_sha256": input_sha, "stem": stem,
        "stylized": bool(stylize_first),
    }
    recipe = (f'seed {seed} · steps {disp_steps} · cfg {disp_cfg:g}'
              + (" · custom prompt" if prompt.strip() and
                 prompt.strip() != info.get("house_prompt", "") else "")
              + (f" · preset {preset}" if preset and preset != "house" else ""))
    sty_head = (f'<h3>Stylized line art <small>{recipe} · '
                f'<a id="openart" href="#" target="_blank">open full</a></small></h3>'
                if stylize_first else
                '<h3>Input art <small><a id="openart" href="#" target="_blank">'
                'open full</a></small></h3>')
    seed_note = f" · seed <b>{seed}</b>" if stylize_first else ""
    prog = ('<div id="progress"><div id="progbar"></div>'
            '<div id="progtext">submitting…</div></div>'
            if job and job.get("state") != "done" else
            '<div id="progress" style="display:none"><div id="progbar"></div>'
            '<div id="progtext"></div></div>')
    body = f"""
<p><a href="/">&larr; another</a></p>
<p><b id="dims">computing…</b> · {material}{seed_note}</p>
<div id="warn"></div>
{prog}
<div class="row">
 <label>Mode <select id="mode">{_mode_options(bool(stylize_first))}</select></label>
 <label>Cut <input type="range" id="cut" min="0" max="255" value="128"
   style="width:150px"> <span id="cutv"></span></label>
 <label title="density control — THE knob for dither modes">γ
   <input type="range" id="gamma" min="0.3" max="3" step="0.05" value="1"
   style="width:120px"></label>
 <span id="ink"></span>
</div>
<div class="row">
 <button id="save">💾 Save for laser (PNG + DPI)</button>
 <a id="openfull" href="#" target="_blank">open full</a>
 <button id="snapbtn" title="pin the current config/result for side-by-side comparison">📌 Snapshot</button>
 <button id="contact" title="all contact thresholds tiled on one sheet for a calibration burn">🎯 Contact sheet</button>
</div>
<details>
 <summary>Advanced raster</summary>
 <div class="row">
  <label>Black pt <input type="number" id="bp" value="0" min="0" max="254"
    style="width:4.5em"></label>
  <label>White pt <input type="number" id="wp" value="255" min="1" max="255"
    style="width:4.5em"></label>
  <label>Unsharp mm <input type="number" id="usm_mm" value="0" min="0" max="3"
    step="0.1" style="width:4.5em"></label>
  <label>amount <input type="number" id="usm_amt" value="1" min="0" max="4"
    step="0.1" style="width:4.5em"></label>
  <label title="grow (+) or thin (−) ink; cork wants +1 so hatching survives">
    Morph px <input type="number" id="morph" value="0" min="-2" max="3"
    style="width:4em"></label>
  <label><input type="checkbox" id="despeckle" checked> despeckle</label>
  <label><input type="checkbox" id="serpentine" checked> serpentine</label>
 </div>
</details>
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
features ≥ {mat.get("min_feature_mm", "?")}mm (checked live above) — burn a
test tile before trusting dithered tone. Saved PNG embeds DPI + all
parameters and can be dropped on the front page to restore them.</small></p>
<script>window.LP = {json.dumps(lp)};</script>
<script src="/prep.js?v={_JS_VER}"></script>"""
    return _PAGE.format(version=app.version, body=body)


@app.get("/stylize/status")
def stylize_status():
    h = _upstream_health()
    code = 200 if h.get("model_loaded") else 503
    return JSONResponse({"upstream": STYLIZE_UPSTREAM,
                         "ok": h.get("model_loaded", False), "detail": h}, code)


def _proxy(method: str, path: str, body: bytes | None = None,
           timeout: float = 240) -> Response:
    req = urllib.request.Request(
        STYLIZE_UPSTREAM + path, data=body, method=method,
        headers={"Content-Type": "application/octet-stream"} if body else {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return Response(content=r.read(),
                            media_type=r.headers.get("Content-Type",
                                                     "application/json"),
                            headers={k: v for k, v in r.headers.items()
                                     if k.lower().startswith("x-")})
    except urllib.error.HTTPError as e:
        return Response(content=e.read(), status_code=e.code,
                        media_type=e.headers.get("Content-Type",
                                                 "application/json"))
    except Exception as e:
        return JSONResponse({"error": f"stylize upstream unavailable: {e}",
                             "upstream": STYLIZE_UPSTREAM}, status_code=503)


@app.post("/stylize/mask")
async def stylize_mask(request: Request):
    return _proxy("POST", "/mask", await request.body())


@app.post("/stylize/jobs")
async def stylize_jobs(request: Request):
    qs = str(request.query_params)
    return _proxy("POST", "/jobs" + (f"?{qs}" if qs else ""),
                  await request.body(), timeout=60)


@app.get("/stylize/progress/{key}")
def stylize_progress(key: str):
    return _proxy("GET", f"/progress/{key}", timeout=15)


@app.get("/stylize/result/{key}")
def stylize_result(key: str):
    return _proxy("GET", f"/result/{key}", timeout=120)


@app.post("/stylize")
async def stylize(request: Request, file: UploadFile = File(...)):
    """Raw synchronous proxy (CLI convenience); query params pass through."""
    data = await file.read()
    qs = str(request.query_params)
    return _proxy("POST", "/stylize" + (f"?{qs}" if qs else ""), data,
                  timeout=1900)
