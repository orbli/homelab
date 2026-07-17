"""stylize — ComfyUI-backed stylization service for the MCGT laser pipeline.

CPU-only k8s pod (ns laserprep). The GPU work runs on spark1's headless
ComfyUI, reached through the networking-egress Service
(comfyui-spark1.networking:8188). This service owns everything ComfyUI
shouldn't: the versioned HOUSE workflow template (the production recipe),
the render cache on a PVC, and the render manifests.

POST /stylize  body = raw image bytes (png/jpg/webp)
               query: steps, seed, fast (0/1 Lightning LoRA), cfg,
                      prompt / neg (recipe overrides — versioned, cacheable)
GET  /healthz  always 200; "model_loaded" = ComfyUI reachable (it loads
               weights lazily, so first render after a restart is slow)
GET  /info     recipe: prompts, defaults, workflow version, model files

Cache: {input-sha256}_{seed}_{steps}_{ver}_c{cfg}[_fast]
       (.jpg/.png = input bytes, _processed.png = model output)
ver = sha256(workflow-shape + prompt + neg)[:8] — any recipe change
(graph rewire, model swap, prompt edit) lands in a fresh version instead of
poisoning old entries. Same request = same bytes with zero GPU time.
"""

import hashlib
import json
import os
import time
import uuid
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
POLL_BUDGET_S = 1100  # first render after restart pays the model load too

MODEL = "qwen_image_edit_2511_bf16.safetensors"
TEXT_ENCODER = "qwen_2.5_vl_7b_fp8_scaled.safetensors"
VAE = "qwen_image_vae.safetensors"
LIGHTNING_LORA = "Qwen-Image-Edit-2511-Lightning-4steps-V1.0-bf16.safetensors"

DEFAULTS = {"steps": 40, "cfg": 4.0, "fast_steps": 4, "fast_cfg": 1.0}

NEGATIVE_PROMPT = (
    "photo, photorealistic, color, gray shading, solid black areas, blurry, "
    "soft focus, background scenery, cast shadow, ground shadow, drop shadow, "
    "watermark, text"
)

HOUSE_PROMPT = (
    "Convert this photo into a highly detailed black and white pen-and-ink "
    "line drawing. Directional hatching strokes that follow the fur growth, "
    "stipple shading, pure white background, no solid black blocks, no gray "
    "tones. The subject only, background removed, no cast shadow and no "
    "ground shadow under the subject."
)


def build_workflow(image_name: str, prompt: str, neg: str, seed: int,
                   steps: int, cfg: float, fast: bool, prefix: str) -> dict:
    """House recipe as a ComfyUI API-format graph. This dict IS the contract:
    tune interactively in the ComfyUI web UI, export API format, merge the
    winning changes here — the shape hash below versions the cache."""
    model_src = "lora" if fast else "unet"
    wf = {
        "unet": {"class_type": "UNETLoader",
                 "inputs": {"unet_name": MODEL, "weight_dtype": "default"}},
        "clip": {"class_type": "CLIPLoader",
                 "inputs": {"clip_name": TEXT_ENCODER, "type": "qwen_image"}},
        "vae": {"class_type": "VAELoader", "inputs": {"vae_name": VAE}},
        "load": {"class_type": "LoadImage", "inputs": {"image": image_name}},
        "scale": {"class_type": "ImageScaleToTotalPixels",
                  "inputs": {"image": ["load", 0], "upscale_method": "lanczos",
                             "megapixels": 1.0}},
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
        "save": {"class_type": "SaveImage",
                 "inputs": {"images": ["dec", 0],
                            "filename_prefix": f"stylize/{prefix}"}},
    }
    if fast:
        wf["lora"] = {"class_type": "LoraLoaderModelOnly",
                      "inputs": {"model": ["unet", 0],
                                 "lora_name": LIGHTNING_LORA,
                                 "strength_model": 1.0}}
    return wf


# Graph-shape version: sentinel params, so only rewires/model swaps bump it;
# the per-request prompt text folds into the cache key separately.
WORKFLOW_SHAPE = hashlib.sha256(json.dumps(
    build_workflow("X.png", "P", "N", 0, 1, 1.0, False, "X"),
    sort_keys=True).encode()).hexdigest()[:8]

app = FastAPI(title="stylize", version="0.2.0")


def _sniff(data: bytes) -> str | None:
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    if data[:3] == b"\xff\xd8\xff":
        return "jpg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    return None


def _log(entry: dict) -> None:
    with LOG_PATH.open("a") as f:
        f.write(json.dumps(entry) + "\n")


@app.get("/healthz")
def healthz():
    try:
        up = requests.get(COMFY + "/system_stats", timeout=3).ok
        detail = {}
    except Exception as e:
        up, detail = False, {"comfy_error": str(e)}
    return {"ok": True, "model_loaded": up, "workflow_shape": WORKFLOW_SHAPE,
            "comfy": COMFY, **detail}


@app.get("/info")
def info():
    return {
        "house_prompt": HOUSE_PROMPT,
        "negative_prompt": NEGATIVE_PROMPT,
        "defaults": DEFAULTS,
        "workflow_shape": WORKFLOW_SHAPE,
        "model": MODEL,
        "lightning_lora": LIGHTNING_LORA,
        "sampler": "euler / simple, shift 3.1, CFGNorm",
        "comfy": COMFY,
        "notes": ("fast=1 uses the Lightning LoRA (4 steps, cfg 1.0, negative "
                  "prompt inactive); cfg>1 roughly doubles render time; "
                  "prompt/neg overrides are cached under their own version."),
    }


@app.post("/stylize")
async def stylize(request: Request,
                  steps: int = Query(0, ge=0, le=100),
                  seed: int = Query(42),
                  fast: int = Query(0),
                  cfg: float = Query(0.0, ge=0.0, le=20.0),
                  prompt: str = Query(""),
                  neg: str = Query("")):
    data = await request.body()
    if len(data) > 30 * 2**20:
        return JSONResponse({"error": "upload > 30MB"}, status_code=413)
    ext = _sniff(data)
    if ext is None:
        return JSONResponse({"error": "unsupported image (png/jpg/webp only)"},
                            status_code=422)

    n_steps = steps or (DEFAULTS["fast_steps"] if fast else DEFAULTS["steps"])
    true_cfg = cfg or (DEFAULTS["fast_cfg"] if fast else DEFAULTS["cfg"])
    p = prompt or HOUSE_PROMPT
    n = neg or NEGATIVE_PROMPT

    input_hash = hashlib.sha256(data).hexdigest()
    ver = hashlib.sha256(
        (WORKFLOW_SHAPE + p + "\x1f" + n).encode()).hexdigest()[:8]
    cache_key = (f"{input_hash}_{seed}_{n_steps}_{ver}_c{true_cfg:g}"
                 + ("_fast" if fast else ""))
    cache_out = CACHE_DIR / f"{cache_key}_processed.png"
    if cache_out.exists():
        _log({"ts": datetime.now(timezone.utc).isoformat(),
              "input_sha256": input_hash, "seed": seed, "steps": n_steps,
              "cfg": true_cfg, "ver": ver, "cached": True})
        return Response(content=cache_out.read_bytes(), media_type="image/png",
                        headers={"X-Stylize-Cache": "hit",
                                 "X-Render-Manifest": json.dumps(
                                     {"seed": seed, "steps": n_steps,
                                      "cfg": true_cfg, "cached": True})})

    t0 = time.time()
    try:
        up = requests.post(COMFY + "/upload/image",
                           files={"image": (f"{input_hash}.{ext}", data)},
                           data={"overwrite": "true"}, timeout=60)
        up.raise_for_status()
        img_name = up.json()["name"]
        wf = build_workflow(img_name, p, n, seed, n_steps, true_cfg,
                            bool(fast), cache_key)
        sub = requests.post(COMFY + "/prompt",
                            json={"prompt": wf,
                                  "client_id": f"stylize-{uuid.uuid4().hex[:8]}"},
                            timeout=30)
        if sub.status_code != 200:
            try:
                detail = sub.json()
            except ValueError:
                detail = sub.text[:500]
            return JSONResponse({"error": "comfy rejected workflow",
                                 "detail": detail}, status_code=502)
        pid = sub.json()["prompt_id"]
    except requests.RequestException as e:
        return JSONResponse({"error": "model backend unreachable",
                             "detail": str(e), "upstream": COMFY},
                            status_code=503)

    outputs = None
    try:
        while time.time() - t0 < POLL_BUDGET_S:
            time.sleep(2)
            h = requests.get(f"{COMFY}/history/{pid}", timeout=10).json()
            if pid not in h:
                continue
            status = h[pid].get("status") or {}
            if status.get("status_str") == "error":
                msgs = [m[1] for m in status.get("messages", [])
                        if m[0] == "execution_error"]
                return JSONResponse({"error": "comfy execution failed",
                                     "detail": msgs[-1] if msgs else status},
                                    status_code=502)
            outputs = h[pid].get("outputs")
            if outputs:
                break
        if not outputs:
            return JSONResponse({"error": "render timed out",
                                 "prompt_id": pid,
                                 "budget_s": POLL_BUDGET_S}, status_code=504)
        f0 = next(v["images"] for v in outputs.values() if v.get("images"))[0]
        png = requests.get(COMFY + "/view",
                           params={"filename": f0["filename"],
                                   "subfolder": f0.get("subfolder", ""),
                                   "type": f0.get("type", "output")},
                           timeout=60).content
    except requests.RequestException as e:
        return JSONResponse({"error": "lost contact with comfy mid-render",
                             "detail": str(e), "prompt_id": pid},
                            status_code=502)
    wall = round(time.time() - t0, 1)

    (CACHE_DIR / f"{cache_key}.{ext}").write_bytes(data)
    cache_out.write_bytes(png)

    manifest = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "input_sha256": input_hash,
        "model": MODEL, "lora": LIGHTNING_LORA if fast else None,
        "workflow_shape": WORKFLOW_SHAPE, "ver": ver,
        "seed": seed, "steps": n_steps, "cfg": true_cfg,
        "prompt": p, "negative_prompt": n,
        "comfy_prompt_id": pid, "wall_s": wall, "cached": False,
        "cache_key": cache_key,
    }
    _log(manifest)
    print("render", cache_key[:32], f"{wall}s", flush=True)

    return Response(content=png, media_type="image/png",
                    headers={"X-Stylize-Cache": "miss",
                             "X-Render-Manifest": json.dumps(
                                 {"seed": seed, "steps": n_steps,
                                  "cfg": true_cfg, "wall_s": wall})})
