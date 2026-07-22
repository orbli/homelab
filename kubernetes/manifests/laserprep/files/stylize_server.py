"""stylize — ComfyUI-backed stylization service for the MCGT laser pipeline.

CPU-only k8s pod (ns laserprep). The GPU work runs on spark1's headless
ComfyUI (custom "laserprep" node pack: BiRefNet matte, subject crop,
mask whiteout + RGBA), reached through the networking-egress Service
(comfyui-spark1.networking:8188). This service owns everything ComfyUI
shouldn't: the versioned HOUSE workflow template, the render cache on a
PVC, render manifests, and the async job table.

The render pipeline (docs/REDESIGN.md P0-1..P0-3): BiRefNet matte ->
crop to subject bbox (or operator box) + composite on white -> lanczos
to `mp` megapixels -> Qwen-Image-Edit-2511 -> dilated-mask whiteout
(deterministic ground-shadow kill) -> RGBA PNG whose ALPHA CHANNEL is the
subject mask (browser uses it for ink % and despeckle denominators).

POST /mask     body = raw image bytes -> subject mask PNG (fast, cached);
               X-Image-Dims header = "WxH" of the source.
POST /jobs     body = raw image bytes, query = render params ->
               {key, state} immediately; render continues in a thread.
GET  /progress/{key}   {state, elapsed_s, eta_s, error?}
GET  /result/{key}     RGBA PNG once done (404 before)
POST /stylize  legacy synchronous wrapper around the same machinery.
GET  /healthz  instant (background-cached ComfyUI reachability)
GET  /info     recipe: prompts, presets, defaults, versions

Cache: {input-sha256}_{seed}_{steps}_{ver}_c{cfg}[_fast]_processed.png
ver = sha256(workflow-shape + prompts + mask/crop/resolution params)[:8]
— any recipe change lands in a fresh version instead of poisoning old
entries. The deterministic cache key doubles as the job id, so job state
survives pod restarts as "done" whenever the artifact exists.
"""

import asyncio
import hashlib
import json
import os
import struct
import threading
import time
import uuid
from collections import deque
from datetime import datetime, timezone
from pathlib import Path

import requests
from fastapi import FastAPI, Query, Request
from fastapi.responses import JSONResponse, Response

COMFY = os.environ.get(
    "COMFY_UPSTREAM", "http://comfyui-spark1.networking:8188").rstrip("/")
CACHE_DIR = Path(os.environ.get("CACHE_DIR", "/cache"))
CACHE_DIR.mkdir(parents=True, exist_ok=True)
LOG_PATH = CACHE_DIR / "stylize_renders.jsonl"
POLL_BUDGET_S = 1800  # bigger working resolutions + model load headroom

MODEL = "qwen_image_edit_2511_bf16.safetensors"
TEXT_ENCODER = "qwen_2.5_vl_7b_fp8_scaled.safetensors"
VAE = "qwen_image_vae.safetensors"
LIGHTNING_LORA = "Qwen-Image-Edit-2511-Lightning-4steps-V1.0-bf16.safetensors"
MATTE_MODEL = "ZhengPeng7/BiRefNet"

DEFAULTS = {"steps": 40, "cfg": 4.0, "fast_steps": 4, "fast_cfg": 1.0,
            "mp": 1.0, "pad_pct": 9.0, "dilate_pct": 2.0, "shadow_lift": 1.0}

NEGATIVE_PROMPT = (
    "photo, photorealistic, color, sepia, color tint, colored paper, "
    "parchment, beige background, watercolor, gray shading, solid black "
    "areas, blurry, soft focus, background scenery, cast shadow, ground "
    "shadow, drop shadow, watermark, text, broken whiskers, dashed lines, "
    "doubled outlines, extra whiskers, extra limbs, human face, collar text"
)

# "black-and-white … black ink" is load-bearing: without it the model
# drifts into sepia vintage engravings whose luma dithers to mud.
HOUSE_PROMPT = (
    "Convert your pet photo into a refined black-and-white pen-line "
    "engraving portrait, strictly monochrome black ink on pure white. "
    "Directional hatching strokes that follow the fur growth, "
    "stipple shading, pure white background, no solid black blocks, no gray "
    "tones, no color. The subject only, background removed, no cast shadow "
    "and no ground shadow under the subject."
)

WHISKER_ADD = (
    " Whiskers drawn as continuous unbroken single strokes extending well "
    "past the muzzle; eyes with a clean white catchlight left unhatched; "
    "ear fur as fine directional strokes following growth direction.")

# Prompt presets (REDESIGN P1-4): selecting one prefills the text boxes in
# the portal. shadow_lift < 1 is the dark-cat fix — a subject-only gamma
# lift BEFORE the edit model, because black fur has no recoverable detail
# for the model to draw and prompts can't conjure it.
PRESETS = {
    "house": {"label": "house (default)", "prompt": HOUSE_PROMPT,
              "neg": NEGATIVE_PROMPT, "shadow_lift": 1.0},
    "cat-short": {"label": "short-hair cat",
                  "prompt": HOUSE_PROMPT + WHISKER_ADD,
                  "neg": NEGATIVE_PROMPT, "shadow_lift": 1.0},
    "cat-long": {"label": "long-hair cat",
                 "prompt": HOUSE_PROMPT + WHISKER_ADD +
                 " Long flowing fur rendered with layered directional "
                 "strokes, distinct fur clumps.",
                 "neg": NEGATIVE_PROMPT, "shadow_lift": 1.0},
    "cat-dark": {"label": "dark / black cat",
                 "prompt": HOUSE_PROMPT + WHISKER_ADD +
                 " Dark fur suggested by dense but separated hatching with "
                 "white gaps between strokes, never solid fill.",
                 "neg": NEGATIVE_PROMPT, "shadow_lift": 0.6},
    "dog": {"label": "dog",
            "prompt": HOUSE_PROMPT +
            " Eyes with a clean white catchlight left unhatched; nose "
            "texture stippled; fur as directional strokes following growth.",
            "neg": NEGATIVE_PROMPT, "shadow_lift": 1.0},
}


def build_workflow(image_name: str, prompt: str, neg: str, seed: int,
                   steps: int, cfg: float, fast: bool, prefix: str,
                   crop: tuple[float, float, float, float] = (0, 0, 0, 0),
                   aspect: float = 0.0, pad_pct: float = 9.0,
                   shadow_lift: float = 1.0, mp: float = 1.0,
                   dilate_pct: float = 2.0) -> dict:
    """House recipe as a ComfyUI API-format graph. This dict IS the contract:
    tune interactively in the ComfyUI web UI, export API format, merge the
    winning changes here — the shape hash below versions the cache."""
    model_src = "lora" if fast else "unet"
    cx, cy, cw, ch = crop
    wf = {
        "unet": {"class_type": "UNETLoader",
                 "inputs": {"unet_name": MODEL, "weight_dtype": "default"}},
        "clip": {"class_type": "CLIPLoader",
                 "inputs": {"clip_name": TEXT_ENCODER, "type": "qwen_image"}},
        "vae": {"class_type": "VAELoader", "inputs": {"vae_name": VAE}},
        "load": {"class_type": "LoadImage", "inputs": {"image": image_name}},
        "matte": {"class_type": "LaserprepBiRefNetMatte",
                  "inputs": {"image": ["load", 0]}},
        "subj": {"class_type": "LaserprepSubjectPrep",
                 "inputs": {"image": ["load", 0], "mask": ["matte", 0],
                            "crop_x": cx, "crop_y": cy,
                            "crop_w": cw, "crop_h": ch,
                            "pad_pct": pad_pct, "aspect": aspect,
                            "shadow_lift": shadow_lift}},
        "scale": {"class_type": "ImageScaleToTotalPixels",
                  "inputs": {"image": ["subj", 0],
                             "upscale_method": "lanczos",
                             "megapixels": mp,
                             # multiple-of-8 dims: VAE latent grid, no padding
                             "resolution_steps": 8}},
        "shift": {"class_type": "ModelSamplingAuraFlow",
                  "inputs": {"model": [model_src, 0], "shift": 3.1}},
        "cfgnorm": {"class_type": "CFGNorm",
                    "inputs": {"model": ["shift", 0], "strength": 1.0}},
        "pos": {"class_type": "TextEncodeQwenImageEditPlus",
                "inputs": {"clip": ["clip", 0], "vae": ["vae", 0],
                           "image1": ["scale", 0], "prompt": prompt}},
        "negc": {"class_type": "TextEncodeQwenImageEditPlus",
                 "inputs": {"clip": ["clip", 0], "vae": ["vae", 0],
                            "image1": ["scale", 0], "prompt": neg}},
        "enc": {"class_type": "VAEEncode",
                "inputs": {"pixels": ["scale", 0], "vae": ["vae", 0]}},
        "sample": {"class_type": "KSampler",
                   "inputs": {"model": ["cfgnorm", 0], "positive": ["pos", 0],
                              "negative": ["negc", 0],
                              "latent_image": ["enc", 0],
                              "seed": seed, "steps": steps, "cfg": cfg,
                              "sampler_name": "euler", "scheduler": "simple",
                              "denoise": 1.0}},
        "dec": {"class_type": "VAEDecode",
                "inputs": {"samples": ["sample", 0], "vae": ["vae", 0]}},
        # deterministic ground-shadow kill + mask into the alpha channel
        "fin": {"class_type": "LaserprepFinish",
                "inputs": {"image": ["dec", 0], "mask": ["subj", 1],
                           "dilate_pct": dilate_pct}},
        "save": {"class_type": "SaveImage",
                 "inputs": {"images": ["fin", 0],
                            "filename_prefix": f"stylize/{prefix}"}},
    }
    if fast:
        wf["lora"] = {"class_type": "LoraLoaderModelOnly",
                      "inputs": {"model": ["unet", 0],
                                 "lora_name": LIGHTNING_LORA,
                                 "strength_model": 1.0}}
    return wf


def build_mask_workflow(image_name: str, prefix: str) -> dict:
    """Matting-only graph — cheap, runs at upload time so the portal can
    pre-fill the crop rectangle. Kept as a separate ComfyUI prompt so cache
    hits on the render never pay for matting."""
    return {
        "load": {"class_type": "LoadImage", "inputs": {"image": image_name}},
        "matte": {"class_type": "LaserprepBiRefNetMatte",
                  "inputs": {"image": ["load", 0]}},
        "m2i": {"class_type": "MaskToImage", "inputs": {"mask": ["matte", 0]}},
        "save": {"class_type": "SaveImage",
                 "inputs": {"images": ["m2i", 0],
                            "filename_prefix": f"mask/{prefix}"}},
    }


# Graph-shape versions: sentinel params, so only rewires/model swaps bump
# them; per-request values fold into the cache key via _version().
WORKFLOW_SHAPE = hashlib.sha256(json.dumps(
    build_workflow("X.png", "P", "N", 0, 1, 1.0, False, "X"),
    sort_keys=True).encode()).hexdigest()[:8]
MATTING_VER = hashlib.sha256((MATTE_MODEL + json.dumps(
    build_mask_workflow("X.png", "X"),
    sort_keys=True)).encode()).hexdigest()[:8]

app = FastAPI(title="stylize", version="0.3.0")


def _sniff(data: bytes) -> str | None:
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    if data[:3] == b"\xff\xd8\xff":
        return "jpg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    return None


def _is_heic(data: bytes) -> bool:
    return (len(data) > 12 and data[4:8] == b"ftyp"
            and data[8:12] in (b"heic", b"heix", b"hevc", b"heif",
                               b"mif1", b"msf1"))


def _png_dims(png: bytes) -> tuple[int, int]:
    """Width/height straight from the IHDR header — no image libs needed."""
    w, h = struct.unpack(">II", png[16:24])
    return w, h


def _log(entry: dict) -> None:
    with LOG_PATH.open("a") as f:
        f.write(json.dumps(entry) + "\n")


def _version(prompt: str, neg: str, crop, aspect, pad_pct, shadow_lift,
             mp, dilate_pct) -> str:
    blob = "\x1f".join([
        WORKFLOW_SHAPE, MATTING_VER, prompt, neg,
        ",".join(f"{c:.4f}" for c in crop),
        f"{aspect:g}", f"{pad_pct:g}", f"{shadow_lift:g}",
        f"{mp:g}", f"{dilate_pct:g}"])
    return hashlib.sha256(blob.encode()).hexdigest()[:8]


# ---------------------------------------------------------------- comfy I/O

def _comfy_upload(data: bytes, name: str) -> str:
    up = requests.post(COMFY + "/upload/image",
                       files={"image": (name, data)},
                       data={"overwrite": "true"}, timeout=60)
    up.raise_for_status()
    return up.json()["name"]


def _comfy_run(wf: dict, budget_s: float) -> bytes:
    """Submit a workflow, poll to completion, return the first output image.
    Raises RuntimeError with a operator-readable message on any failure."""
    t0 = time.time()
    sub = requests.post(
        COMFY + "/prompt",
        json={"prompt": wf, "client_id": f"stylize-{uuid.uuid4().hex[:8]}"},
        timeout=30)
    if sub.status_code != 200:
        try:
            detail = json.dumps(sub.json())[:800]
        except ValueError:
            detail = sub.text[:800]
        raise RuntimeError(f"comfy rejected workflow: {detail}")
    pid = sub.json()["prompt_id"]
    while time.time() - t0 < budget_s:
        time.sleep(2)
        h = requests.get(f"{COMFY}/history/{pid}", timeout=10).json()
        if pid not in h:
            continue
        status = h[pid].get("status") or {}
        if status.get("status_str") == "error":
            msgs = [m[1] for m in status.get("messages", [])
                    if m[0] == "execution_error"]
            err = (msgs[-1].get("exception_message", "")
                   if msgs else str(status))[:800]
            raise RuntimeError(f"comfy execution failed: {err}")
        outputs = h[pid].get("outputs")
        if outputs:
            f0 = next(v["images"] for v in outputs.values()
                      if v.get("images"))[0]
            return requests.get(COMFY + "/view",
                                params={"filename": f0["filename"],
                                        "subfolder": f0.get("subfolder", ""),
                                        "type": f0.get("type", "output")},
                                timeout=60).content
    raise RuntimeError(f"render timed out after {budget_s:.0f}s "
                       f"(comfy prompt {pid})")


# ---------------------------------------------------------------- job table

_jobs: dict[str, dict] = {}     # key -> {state, t0, error, manifest}
_jobs_lock = threading.Lock()
_recent_walls: dict[str, deque] = {}   # eta bucket -> recent wall times


def _eta_bucket(steps: int, cfg: float, mp: float) -> str:
    return f"{steps}_{cfg:g}_{mp:g}"


def _load_recent_walls() -> None:
    try:
        for line in LOG_PATH.read_text().splitlines()[-200:]:
            e = json.loads(line)
            if not e.get("cached") and e.get("wall_s"):
                b = _eta_bucket(e.get("steps", 0), e.get("cfg", 0),
                                e.get("mp", 1.0))
                _recent_walls.setdefault(b, deque(maxlen=5)).append(
                    e["wall_s"])
    except FileNotFoundError:
        pass
    except Exception as e:
        print("manifest replay failed:", e, flush=True)


_load_recent_walls()


def _eta(steps: int, cfg: float, mp: float) -> float | None:
    walls = _recent_walls.get(_eta_bucket(steps, cfg, mp))
    if walls:
        return round(sum(walls) / len(walls), 1)
    if not steps:
        return None
    # no history for this recipe yet: estimate from per-step cost measured
    # on the GB10 (cfg>1 doubles model passes; ~linear in megapixels)
    return round(steps * mp * (8.8 if cfg > 1 else 6.7) + 12, 1)


def _render_job(key: str, data: bytes, ext: str, params: dict) -> None:
    p = params
    t0 = time.time()
    try:
        img_name = _comfy_upload(data, f"{p['input_hash']}.{ext}")
        wf = build_workflow(img_name, p["prompt"], p["neg"], p["seed"],
                            p["steps"], p["cfg"], p["fast"], key,
                            crop=p["crop"], aspect=p["aspect"],
                            pad_pct=p["pad_pct"],
                            shadow_lift=p["shadow_lift"], mp=p["mp"],
                            dilate_pct=p["dilate_pct"])
        with _jobs_lock:
            _jobs[key]["state"] = "rendering"
        png = _comfy_run(wf, POLL_BUDGET_S)
        wall = round(time.time() - t0, 1)

        (CACHE_DIR / f"{key}.{ext}").write_bytes(data)
        (CACHE_DIR / f"{key}_processed.png").write_bytes(png)

        manifest = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "input_sha256": p["input_hash"],
            "model": MODEL, "lora": LIGHTNING_LORA if p["fast"] else None,
            "matte_model": MATTE_MODEL,
            "workflow_shape": WORKFLOW_SHAPE, "ver": p["ver"],
            "seed": p["seed"], "steps": p["steps"], "cfg": p["cfg"],
            "mp": p["mp"], "crop": list(p["crop"]), "aspect": p["aspect"],
            "shadow_lift": p["shadow_lift"], "dilate_pct": p["dilate_pct"],
            "prompt": p["prompt"], "negative_prompt": p["neg"],
            "wall_s": wall, "cached": False, "cache_key": key,
        }
        _log(manifest)
        _recent_walls.setdefault(
            _eta_bucket(p["steps"], p["cfg"], p["mp"]),
            deque(maxlen=5)).append(wall)
        with _jobs_lock:
            _jobs[key].update(state="done", manifest=manifest)
        print("render", key[:32], f"{wall}s", flush=True)
    except Exception as e:
        with _jobs_lock:
            _jobs[key].update(state="error", error=str(e))
        print("render FAILED", key[:32], str(e)[:200], flush=True)


def _parse_params(data: bytes, steps: int, seed: int, fast: int, cfg: float,
                  prompt: str, neg: str, crop: str, aspect: float,
                  shadow_lift: float, mp: float) -> dict | JSONResponse:
    if len(data) > 30 * 2**20:
        return JSONResponse({"error": "upload > 30MB"}, status_code=413)
    if _is_heic(data):
        return JSONResponse(
            {"error": "HEIC/HEIF not supported — export the photo as JPEG "
                      "(iPhone: Settings > Camera > Formats > Most "
                      "Compatible, or share via Mail/WhatsApp)"},
            status_code=422)
    ext = _sniff(data)
    if ext is None:
        return JSONResponse({"error": "unsupported image (png/jpg/webp only)"},
                            status_code=422)
    try:
        cvals = tuple(float(v) for v in crop.split(",")) if crop \
            else (0.0, 0.0, 0.0, 0.0)
        if len(cvals) != 4:
            raise ValueError
    except ValueError:
        return JSONResponse({"error": "crop must be cx,cy,cw,ch in [0,1]"},
                            status_code=422)

    n_steps = steps or (DEFAULTS["fast_steps"] if fast else DEFAULTS["steps"])
    true_cfg = cfg or (DEFAULTS["fast_cfg"] if fast else DEFAULTS["cfg"])
    p = prompt or HOUSE_PROMPT
    n = neg or NEGATIVE_PROMPT
    mp = mp or DEFAULTS["mp"]
    input_hash = hashlib.sha256(data).hexdigest()
    ver = _version(p, n, cvals, aspect, DEFAULTS["pad_pct"], shadow_lift,
                   mp, DEFAULTS["dilate_pct"])
    key = (f"{input_hash}_{seed}_{n_steps}_{ver}_c{true_cfg:g}"
           + ("_fast" if fast else ""))
    return {"ext": ext, "input_hash": input_hash, "ver": ver, "key": key,
            "seed": seed, "steps": n_steps, "cfg": true_cfg,
            "fast": bool(fast), "prompt": p, "neg": n, "crop": cvals,
            "aspect": aspect, "pad_pct": DEFAULTS["pad_pct"],
            "shadow_lift": shadow_lift, "mp": mp,
            "dilate_pct": DEFAULTS["dilate_pct"]}


def _start_job(data: bytes, params: dict) -> dict:
    """Idempotent: cache hit -> done; in-flight -> existing state."""
    key = params["key"]
    if (CACHE_DIR / f"{key}_processed.png").exists():
        _log({"ts": datetime.now(timezone.utc).isoformat(),
              "input_sha256": params["input_hash"], "seed": params["seed"],
              "steps": params["steps"], "cfg": params["cfg"],
              "mp": params["mp"], "ver": params["ver"], "cached": True})
        return {"key": key, "state": "done", "cached": True}
    with _jobs_lock:
        j = _jobs.get(key)
        if j and j["state"] in ("queued", "rendering"):
            return {"key": key, "state": j["state"], "cached": False,
                    "eta_s": _eta(params["steps"], params["cfg"],
                                  params["mp"])}
        _jobs[key] = {"state": "queued", "t0": time.time(), "error": None,
                      "params": {k: v for k, v in params.items()
                                 if k not in ("ext",)}}
    threading.Thread(target=_render_job,
                     args=(key, data, params["ext"], params),
                     daemon=True).start()
    return {"key": key, "state": "queued", "cached": False,
            "eta_s": _eta(params["steps"], params["cfg"], params["mp"])}


# ---------------------------------------------------------------- health

# ComfyUI reachability is polled in the background: the k8s readiness probe
# has a 1s timeout, and a synchronous /system_stats round-trip through the
# tailscale egress hop routinely exceeds it (pod flapped NotReady for days).
_comfy_state = {"model_loaded": False, "comfy_error": "not checked yet"}


def _probe_comfy_loop():
    while True:
        try:
            ok = requests.get(COMFY + "/system_stats", timeout=3).ok
            _comfy_state.update({"model_loaded": ok, "comfy_error": None})
        except Exception as e:
            _comfy_state.update({"model_loaded": False, "comfy_error": str(e)})
        time.sleep(15)


threading.Thread(target=_probe_comfy_loop, daemon=True).start()


@app.get("/healthz")
def healthz():
    detail = ({"comfy_error": _comfy_state["comfy_error"]}
              if _comfy_state["comfy_error"] else {})
    return {"ok": True, "model_loaded": _comfy_state["model_loaded"],
            "workflow_shape": WORKFLOW_SHAPE, "comfy": COMFY, **detail}


@app.get("/info")
def info():
    return {
        "house_prompt": HOUSE_PROMPT,
        "negative_prompt": NEGATIVE_PROMPT,
        "presets": {k: {"label": v["label"], "prompt": v["prompt"],
                        "neg": v["neg"], "shadow_lift": v["shadow_lift"]}
                    for k, v in PRESETS.items()},
        "defaults": DEFAULTS,
        "workflow_shape": WORKFLOW_SHAPE,
        "matting_ver": MATTING_VER,
        "model": MODEL,
        "matte_model": MATTE_MODEL,
        "lightning_lora": LIGHTNING_LORA,
        "sampler": "euler / simple, shift 3.1, CFGNorm",
        "comfy": COMFY,
        "notes": ("fast=1 uses the Lightning LoRA (4 steps, cfg 1.0, negative "
                  "prompt inactive); cfg>1 roughly doubles render time; "
                  "output PNG alpha channel = dilated subject mask; every "
                  "distinct recipe caches under its own version."),
    }


@app.post("/mask")
async def mask(request: Request):
    """Subject matte for the crop UI. Returns a grayscale mask PNG at source
    resolution; X-Image-Dims carries WxH so the portal can lay out the crop
    rectangle without decoding anything server-side."""
    data = await request.body()
    if _is_heic(data):
        return JSONResponse(
            {"error": "HEIC/HEIF not supported — export as JPEG"}, 422)
    ext = _sniff(data)
    if ext is None or len(data) > 30 * 2**20:
        return JSONResponse({"error": "png/jpg/webp only, max 30MB"}, 422)
    input_hash = hashlib.sha256(data).hexdigest()
    cache_out = CACHE_DIR / f"{input_hash}_mask_{MATTING_VER}.png"
    if not cache_out.exists():
        def _matte_miss() -> bytes:
            img_name = _comfy_upload(data, f"{input_hash}.{ext}")
            return _comfy_run(build_mask_workflow(img_name, input_hash[:16]),
                              budget_s=180)
        try:
            # to_thread: requests would otherwise block the event loop and
            # starve /healthz//jobs for the whole matting round-trip
            png = await asyncio.to_thread(_matte_miss)
        except requests.RequestException as e:
            return JSONResponse({"error": "model backend unreachable",
                                 "detail": str(e)}, status_code=503)
        except RuntimeError as e:
            return JSONResponse({"error": str(e)}, status_code=502)
        cache_out.write_bytes(png)
    png = cache_out.read_bytes()
    w, h = _png_dims(png)
    return Response(content=png, media_type="image/png",
                    headers={"X-Image-Dims": f"{w}x{h}",
                             "Access-Control-Expose-Headers": "X-Image-Dims"})


@app.post("/jobs")
async def jobs(request: Request,
               steps: int = Query(0, ge=0, le=100),
               seed: int = Query(42),
               fast: int = Query(0),
               cfg: float = Query(0.0, ge=0.0, le=20.0),
               prompt: str = Query(""),
               neg: str = Query(""),
               crop: str = Query(""),
               aspect: float = Query(0.0, ge=0.0, le=4.0),
               shadow_lift: float = Query(0.0, ge=0.0, le=1.0),
               mp: float = Query(0.0, ge=0.0, le=4.0)):
    data = await request.body()
    params = _parse_params(data, steps, seed, fast, cfg, prompt, neg,
                           crop, aspect, shadow_lift or 1.0, mp)
    if isinstance(params, JSONResponse):
        return params
    return _start_job(data, params)


@app.get("/progress/{key}")
def progress(key: str):
    if (CACHE_DIR / f"{key}_processed.png").exists():
        return {"state": "done"}
    with _jobs_lock:
        j = _jobs.get(key)
        if j is None:
            return JSONResponse({"state": "unknown",
                                 "error": "no such job (pod restarted?) — "
                                          "resubmit"}, status_code=404)
        p = j.get("params", {})
        out = {"state": j["state"],
               "elapsed_s": round(time.time() - j["t0"], 1),
               "eta_s": _eta(p.get("steps", 0), p.get("cfg", 0),
                             p.get("mp", 1.0))}
        if j["state"] == "error":
            out["error"] = j["error"]
        return out


@app.get("/result/{key}")
def result(key: str):
    f = CACHE_DIR / f"{key}_processed.png"
    if not f.exists():
        return JSONResponse({"error": "not ready"}, status_code=404)
    with _jobs_lock:
        j = _jobs.get(key) or {}
    manifest = j.get("manifest") or {"cache_key": key, "cached": True}
    slim = {k: manifest.get(k) for k in
            ("seed", "steps", "cfg", "mp", "wall_s", "cached")
            if manifest.get(k) is not None}
    return Response(content=f.read_bytes(), media_type="image/png",
                    headers={"X-Render-Manifest": json.dumps(slim)})


@app.post("/stylize")
async def stylize(request: Request,
                  steps: int = Query(0, ge=0, le=100),
                  seed: int = Query(42),
                  fast: int = Query(0),
                  cfg: float = Query(0.0, ge=0.0, le=20.0),
                  prompt: str = Query(""),
                  neg: str = Query(""),
                  crop: str = Query(""),
                  aspect: float = Query(0.0, ge=0.0, le=4.0),
                  shadow_lift: float = Query(0.0, ge=0.0, le=1.0),
                  mp: float = Query(0.0, ge=0.0, le=4.0)):
    """Synchronous wrapper over the job machinery (CLI / legacy callers)."""
    data = await request.body()
    params = _parse_params(data, steps, seed, fast, cfg, prompt, neg,
                           crop, aspect, shadow_lift or 1.0, mp)
    if isinstance(params, JSONResponse):
        return params
    st = _start_job(data, params)
    key = params["key"]
    t0 = time.time()
    while st["state"] not in ("done", "error"):
        if time.time() - t0 > POLL_BUDGET_S:
            return JSONResponse({"error": "render timed out", "key": key},
                                status_code=504)
        # asyncio.sleep, NOT time.sleep: this endpoint parks for the whole
        # render and a blocking sleep would freeze every other request
        await asyncio.sleep(2)
        with _jobs_lock:
            j = _jobs.get(key) or {"state": "error",
                                   "error": "job vanished"}
            st = {"state": j["state"], **({"error": j.get("error")}
                                          if j.get("error") else {})}
        if (CACHE_DIR / f"{key}_processed.png").exists():
            st = {"state": "done"}
    if st["state"] == "error":
        code = 503 if "unreachable" in (st.get("error") or "") else 502
        return JSONResponse({"error": st.get("error", "render failed")},
                            status_code=code)
    f = CACHE_DIR / f"{key}_processed.png"
    return Response(content=f.read_bytes(), media_type="image/png",
                    headers={"X-Stylize-Cache":
                             "hit" if st.get("cached") else "miss",
                             "X-Render-Manifest": json.dumps(
                                 {"seed": params["seed"],
                                  "steps": params["steps"],
                                  "cfg": params["cfg"],
                                  "mp": params["mp"]})})
