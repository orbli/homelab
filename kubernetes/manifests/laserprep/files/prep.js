/* laserprep client-side engine: tone + threshold/dither + morphology +
 * despeckle + PNG metadata.
 *
 * The cached diffusion output is the artifact; everything here is a cheap
 * deterministic transform the browser recomputes live. Canonical order
 * (REDESIGN P1-1 — invert must run on GRAY, before binarize, because
 * error-diffusion kernels are not invert-symmetric):
 *
 *   gray (Rec.601, alpha over white; alpha channel = subject mask)
 *     -> invert (material preset) -> black/white point + gamma -> unsharp
 *     -> binarize (otsu | manual | adaptive | error-diffusion | bayer)
 *     -> morphology (dilate/erode) -> despeckle -> ink % inside mask
 *
 * The download button splices pHYs (DPI) and tEXt (params) chunks into the
 * canvas PNG so xTool Studio imports at true physical size.
 *
 * Core functions are DOM-free so node can unit-test them.
 */
"use strict";

/* ---------- image ops (gray = Float32Array, bin = Uint8Array 0=ink) ---- */

function toGray(rgba, n) {
  const g = new Float32Array(n);
  for (let i = 0; i < n; i++) {
    const o = i * 4, a = rgba[o + 3] / 255;
    // luma, alpha composited onto white
    const y = 0.299 * rgba[o] + 0.587 * rgba[o + 1] + 0.114 * rgba[o + 2];
    g[i] = y * a + 255 * (1 - a);
  }
  return g;
}

function maskFromAlpha(rgba, n) {
  // subject mask rides in the alpha channel of the stylize RGBA artifact
  let any = false;
  const m = new Uint8Array(n);
  for (let i = 0; i < n; i++) {
    m[i] = rgba[i * 4 + 3] > 127 ? 1 : 0;
    if (!m[i]) any = true;
  }
  return any ? m : null;  // fully opaque image -> no mask
}

function applyLevels(gray, bp, wp, gamma) {
  const out = new Float32Array(gray.length);
  const span = Math.max(1, wp - bp), inv = 1 / gamma;
  for (let i = 0; i < gray.length; i++) {
    let v = (gray[i] - bp) / span;
    v = v < 0 ? 0 : v > 1 ? 1 : v;
    out[i] = 255 * Math.pow(v, inv);
  }
  return out;
}

function boxBlur(gray, w, h, r) {
  // separable box blur, one pass each axis; run 3x for a gaussian-ish kernel
  const tmp = new Float32Array(gray.length), out = new Float32Array(gray.length);
  const win = 2 * r + 1;
  for (let y = 0; y < h; y++) {
    let s = 0;
    const row = y * w;
    for (let x = -r; x <= r; x++) s += gray[row + Math.min(w - 1, Math.max(0, x))];
    for (let x = 0; x < w; x++) {
      tmp[row + x] = s / win;
      s += gray[row + Math.min(w - 1, x + r + 1)] - gray[row + Math.max(0, x - r)];
    }
  }
  for (let x = 0; x < w; x++) {
    let s = 0;
    for (let y = -r; y <= r; y++) s += tmp[Math.min(h - 1, Math.max(0, y)) * w + x];
    for (let y = 0; y < h; y++) {
      out[y * w + x] = s / win;
      s += tmp[Math.min(h - 1, y + r + 1) * w + x] - tmp[Math.max(0, y - r) * w + x];
    }
  }
  return out;
}

function unsharp(gray, w, h, radiusPx, amount) {
  if (radiusPx < 1 || amount <= 0) return gray;
  let blur = gray;
  for (let i = 0; i < 3; i++) blur = boxBlur(blur, w, h, radiusPx);
  const out = new Float32Array(gray.length);
  for (let i = 0; i < gray.length; i++) {
    const v = gray[i] + amount * (gray[i] - blur[i]);
    out[i] = v < 0 ? 0 : v > 255 ? 255 : v;
  }
  return out;
}

function otsuCut(gray) {
  const hist = new Float64Array(256);
  for (let i = 0; i < gray.length; i++) hist[Math.max(0, Math.min(255, gray[i] | 0))]++;
  const total = gray.length;
  let sum = 0;
  for (let t = 0; t < 256; t++) sum += t * hist[t];
  let sumB = 0, wB = 0, best = 0, cut = 127;
  for (let t = 0; t < 256; t++) {
    wB += hist[t];
    if (!wB) continue;
    const wF = total - wB;
    if (!wF) break;
    sumB += t * hist[t];
    const mB = sumB / wB, mF = (sum - sumB) / wF;
    const between = wB * wF * (mB - mF) * (mB - mF);
    if (between > best) { best = between; cut = t; }
  }
  return cut;
}

function applyManual(gray, cut) {
  const b = new Uint8Array(gray.length);
  for (let i = 0; i < gray.length; i++) b[i] = gray[i] > cut ? 255 : 0;
  return b;
}

function applyAdaptive(gray, w, h, blockPx, C) {
  const bw = Math.max(3, blockPx | 1), r = bw >> 1;
  // integral image for O(1) local means
  const I = new Float64Array((w + 1) * (h + 1));
  for (let y = 0; y < h; y++) {
    let rowsum = 0;
    for (let x = 0; x < w; x++) {
      rowsum += gray[y * w + x];
      I[(y + 1) * (w + 1) + x + 1] = I[y * (w + 1) + x + 1] + rowsum;
    }
  }
  const b = new Uint8Array(gray.length);
  for (let y = 0; y < h; y++) {
    const y0 = Math.max(0, y - r), y1 = Math.min(h - 1, y + r);
    for (let x = 0; x < w; x++) {
      const x0 = Math.max(0, x - r), x1 = Math.min(w - 1, x + r);
      const area = (y1 - y0 + 1) * (x1 - x0 + 1);
      const s = I[(y1 + 1) * (w + 1) + x1 + 1] - I[y0 * (w + 1) + x1 + 1]
              - I[(y1 + 1) * (w + 1) + x0] + I[y0 * (w + 1) + x0];
      b[y * w + x] = gray[y * w + x] > s / area - C ? 255 : 0;
    }
  }
  return b;
}

const DITHER_KERNELS = {
  floyd:    { d: 16, k: [[0,1,7],[1,-1,3],[1,0,5],[1,1,1]] },
  jarvis:   { d: 48, k: [[0,1,7],[0,2,5],[1,-2,3],[1,-1,5],[1,0,7],[1,1,5],[1,2,3],
                         [2,-2,1],[2,-1,3],[2,0,5],[2,1,3],[2,2,1]] },
  stucki:   { d: 42, k: [[0,1,8],[0,2,4],[1,-2,2],[1,-1,4],[1,0,8],[1,1,4],[1,2,2],
                         [2,-2,1],[2,-1,2],[2,0,4],[2,1,2],[2,2,1]] },
  atkinson: { d: 8,  k: [[0,1,1],[0,2,1],[1,-1,1],[1,0,1],[1,1,1],[2,0,1]] },
  sierra:   { d: 32, k: [[0,1,5],[0,2,3],[1,-2,2],[1,-1,4],[1,0,5],[1,1,4],[1,2,2],
                         [2,-1,2],[2,0,3],[2,1,2]] },
};

const BAYER8 = [
  [0,32,8,40,2,34,10,42],[48,16,56,24,50,18,58,26],[12,44,4,36,14,46,6,38],
  [60,28,52,20,62,30,54,22],[3,35,11,43,1,33,9,41],[51,19,59,27,49,17,57,25],
  [15,47,7,39,13,45,5,37],[63,31,55,23,61,29,53,21]];

function applyDither(gray, w, h, mode, serpentine) {
  const b = new Uint8Array(gray.length);
  if (mode === "bayer") {
    for (let y = 0; y < h; y++)
      for (let x = 0; x < w; x++)
        b[y * w + x] = gray[y * w + x] / 255 >= (BAYER8[y & 7][x & 7] + 1) / 65 ? 255 : 0;
    return b;
  }
  const { d, k } = DITHER_KERNELS[mode];
  const buf = Float32Array.from(gray);
  // serpentine scan (REDESIGN P1-2): plain raster order worms horizontally,
  // aligning with the P3's own scan lines and reading as banding in the burn
  for (let y = 0; y < h; y++) {
    const rev = serpentine && (y & 1);
    for (let xi = 0; xi < w; xi++) {
      const x = rev ? w - 1 - xi : xi;
      const i = y * w + x, old = buf[i], nv = old >= 128 ? 255 : 0;
      b[i] = nv;
      const err = (old - nv) / d;
      if (err)
        for (const [dy, dx, wt] of k) {
          const nx = rev ? x - dx : x + dx, ny = y + dy;
          if (nx >= 0 && nx < w && ny < h) buf[ny * w + nx] += err * wt;
        }
    }
  }
  return b;
}

/* ---------- binary morphology & cleanup (bin: 0 = ink/burn) ------------ */

function morphology(bin, w, h, px) {
  // px > 0 dilates ink (grow black), px < 0 erodes; 8-neighbour square SE
  if (!px) return bin;
  const grow = px > 0;
  let cur = bin;
  for (let it = 0; it < Math.abs(px); it++) {
    const tmp = new Uint8Array(cur.length), out = new Uint8Array(cur.length);
    for (let y = 0; y < h; y++) {          // horizontal pass
      const row = y * w;
      for (let x = 0; x < w; x++) {
        const a = cur[row + Math.max(0, x - 1)], b2 = cur[row + x],
              c = cur[row + Math.min(w - 1, x + 1)];
        tmp[row + x] = grow ? Math.min(a, b2, c) : Math.max(a, b2, c);
      }
    }
    for (let y = 0; y < h; y++) {          // vertical pass
      for (let x = 0; x < w; x++) {
        const a = tmp[Math.max(0, y - 1) * w + x], b2 = tmp[y * w + x],
              c = tmp[Math.min(h - 1, y + 1) * w + x];
        out[y * w + x] = grow ? Math.min(a, b2, c) : Math.max(a, b2, c);
      }
    }
    cur = out;
  }
  return cur;
}

function despeckle(bin, w, h, speckPx, holePx) {
  // pass 1: ink components smaller than speckPx -> white
  // pass 2: white components not connected to the border and smaller than
  //         holePx -> ink (pinholes become unburned dots on inverted media)
  const out = Uint8Array.from(bin);
  const seen = new Uint8Array(bin.length);
  const stack = new Int32Array(bin.length);
  const comp = new Int32Array(bin.length);

  function flood(start, value) {
    let sp = 0, len = 0;
    stack[sp++] = start;
    seen[start] = 1;
    while (sp) {
      const i = stack[--sp];
      comp[len++] = i;
      const x = i % w, y = (i / w) | 0;
      for (let dy = -1; dy <= 1; dy++)
        for (let dx = -1; dx <= 1; dx++) {
          if (!dx && !dy) continue;
          const nx = x + dx, ny = y + dy;
          if (nx < 0 || nx >= w || ny < 0 || ny >= h) continue;
          const ni = ny * w + nx;
          if (!seen[ni] && out[ni] === value) { seen[ni] = 1; stack[sp++] = ni; }
        }
    }
    return len;
  }

  if (speckPx > 0) {
    for (let i = 0; i < out.length; i++)
      if (out[i] === 0 && !seen[i]) {
        const len = flood(i, 0);
        if (len < speckPx) for (let j = 0; j < len; j++) out[comp[j]] = 255;
      }
  }
  if (holePx > 0) {
    seen.fill(0);
    for (let x = 0; x < w; x++) {          // border-connected white = outside
      if (out[x] === 255 && !seen[x]) flood(x, 255);
      const b2 = (h - 1) * w + x;
      if (out[b2] === 255 && !seen[b2]) flood(b2, 255);
    }
    for (let y = 0; y < h; y++) {
      const l = y * w, r = y * w + w - 1;
      if (out[l] === 255 && !seen[l]) flood(l, 255);
      if (out[r] === 255 && !seen[r]) flood(r, 255);
    }
    for (let i = 0; i < out.length; i++)
      if (out[i] === 255 && !seen[i]) {
        const len = flood(i, 255);
        if (len < holePx) for (let j = 0; j < len; j++) out[comp[j]] = 0;
      }
  }
  return out;
}

function inkPct(bin, mask) {
  // coverage inside the subject mask (REDESIGN P0-1c): without the mask the
  // number changes meaning with subject size and is useless for cork
  let n = 0, denom = 0;
  for (let i = 0; i < bin.length; i++) {
    if (mask && !mask[i]) continue;
    denom++;
    if (!bin[i]) n++;
  }
  return denom ? 100 * n / denom : 0;
}

function minFeatureLoss(bin, w, h, minPx) {
  // fraction of ink living in strokes thinner than minPx: morphological
  // opening at radius minPx/2, measure ink that does not survive
  const r = Math.round(minPx / 2);
  if (r < 1) return 0;
  let total = 0;
  for (let i = 0; i < bin.length; i++) if (!bin[i]) total++;
  if (!total) return 0;
  const opened = morphology(morphology(bin, w, h, -r), w, h, r);
  let surv = 0;
  for (let i = 0; i < opened.length; i++) if (!opened[i]) surv++;
  return 100 * (total - surv) / total;
}

function midtonePct(gray) {
  let n = 0;
  for (let i = 0; i < gray.length; i++) if (gray[i] > 64 && gray[i] < 192) n++;
  return 100 * n / gray.length;
}

/* ---------- PNG chunk splicing (pHYs DPI + tEXt params) ---------------- */

const CRC_TABLE = (() => {
  const t = new Uint32Array(256);
  for (let n = 0; n < 256; n++) {
    let c = n;
    for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
    t[n] = c >>> 0;
  }
  return t;
})();

function crc32(bytes) {
  let c = 0xffffffff;
  for (let i = 0; i < bytes.length; i++)
    c = CRC_TABLE[(c ^ bytes[i]) & 0xff] ^ (c >>> 8);
  return (c ^ 0xffffffff) >>> 0;
}

function makeChunk(type, data) {
  const out = new Uint8Array(12 + data.length);
  const dv = new DataView(out.buffer);
  dv.setUint32(0, data.length);
  for (let i = 0; i < 4; i++) out[4 + i] = type.charCodeAt(i);
  out.set(data, 8);
  dv.setUint32(8 + data.length, crc32(out.subarray(4, 8 + data.length)));
  return out;
}

function pngWithMeta(pngBytes, dpi, textObj) {
  const sig = [137, 80, 78, 71, 13, 10, 26, 10];
  for (let i = 0; i < 8; i++)
    if (pngBytes[i] !== sig[i]) throw new Error("not a PNG");
  // pHYs: pixels per metre, both axes, unit=1 (metre)
  const ppm = Math.round(dpi / 0.0254);
  const phys = new Uint8Array(9);
  new DataView(phys.buffer).setUint32(0, ppm);
  new DataView(phys.buffer).setUint32(4, ppm);
  phys[8] = 1;
  const chunks = [makeChunk("pHYs", phys)];
  const kv = "laserprep\0" + JSON.stringify(textObj);
  chunks.push(makeChunk("tEXt", Uint8Array.from(kv, c => c.charCodeAt(0) & 0xff)));
  // splice before first IDAT
  let off = 8;
  const dv = new DataView(pngBytes.buffer, pngBytes.byteOffset);
  while (off < pngBytes.length) {
    const len = dv.getUint32(off);
    const type = String.fromCharCode(...pngBytes.subarray(off + 4, off + 8));
    if (type === "IDAT") break;
    off += 12 + len;
  }
  const extra = chunks.reduce((s, c) => s + c.length, 0);
  const out = new Uint8Array(pngBytes.length + extra);
  out.set(pngBytes.subarray(0, off), 0);
  let p = off;
  for (const c of chunks) { out.set(c, p); p += c.length; }
  out.set(pngBytes.subarray(off), p);
  return out;
}

function readMeta(pngBytes) {
  // inverse of pngWithMeta: find the laserprep tEXt chunk, return the object
  const sig = [137, 80, 78, 71, 13, 10, 26, 10];
  for (let i = 0; i < 8; i++) if (pngBytes[i] !== sig[i]) return null;
  const dv = new DataView(pngBytes.buffer, pngBytes.byteOffset);
  let off = 8;
  while (off + 12 <= pngBytes.length) {
    const len = dv.getUint32(off);
    const type = String.fromCharCode(...pngBytes.subarray(off + 4, off + 8));
    if (type === "tEXt") {
      const data = pngBytes.subarray(off + 8, off + 8 + len);
      const txt = String.fromCharCode(...data);
      const nul = txt.indexOf("\0");
      if (txt.slice(0, nul) === "laserprep") {
        try { return JSON.parse(txt.slice(nul + 1)); } catch (e) { return null; }
      }
    }
    if (type === "IEND") break;
    off += 12 + len;
  }
  return null;
}

/* ---------- browser UI ------------------------------------------------- */

if (typeof document !== "undefined") window.lpReadMeta = readMeta;

if (typeof document !== "undefined" && window.LP) (function () {
  const cfg = window.LP;  // injected by server: art/job, physical, material
  const $ = id => document.getElementById(id);
  const img = new Image();
  let gray = null, mask = null, W = 0, H = 0, curBin = null,
      blobUrl = null, artUrl = null, snapUrl = null;
  let procCache = { key: null, gray: null };
  let lastAutoMorph = 0;

  function fmt(x) { return Math.round(x * 10) / 10; }
  function mmToPx(mm) { return mm / 25.4 * cfg.dpi; }

  const knobs = {
    bp: () => +$("bp").value, wp: () => +$("wp").value,
    gamma: () => +$("gamma").value,
    usmMm: () => +$("usm_mm").value, usmAmt: () => +$("usm_amt").value,
    morph: () => +$("morph").value,
    despeckle: () => $("despeckle").checked,
    serpentine: () => $("serpentine").checked,
  };

  function processedGray() {
    const key = [cfg.material.invert, knobs.bp(), knobs.wp(), knobs.gamma(),
                 knobs.usmMm(), knobs.usmAmt()].join("|");
    if (procCache.key === key) return procCache.gray;
    let g = gray;
    if (cfg.material.invert) {
      g = new Float32Array(g.length);
      for (let i = 0; i < g.length; i++) g[i] = 255 - gray[i];
    }
    g = applyLevels(g, knobs.bp(), knobs.wp(), knobs.gamma());
    g = unsharp(g, W, H, Math.round(mmToPx(knobs.usmMm())), knobs.usmAmt());
    procCache = { key, gray: g };
    return g;
  }

  function currentBin() {
    const g = processedGray();
    const mode = $("mode").value;
    let b;
    if (mode === "otsu") {
      const oc = otsuCut(g);
      $("mode").dataset.otsu = oc;
      b = applyManual(g, oc);
    } else if (mode === "manual") b = applyManual(g, +$("cut").value);
    else if (mode === "adaptive") {
      const blockPx = Math.round(mmToPx(cfg.material.adaptive_block_mm));
      b = applyAdaptive(g, W, H, blockPx, cfg.material.adaptive_c);
    } else b = applyDither(g, W, H, mode, knobs.serpentine());
    b = morphology(b, W, H, knobs.morph());
    if (knobs.despeckle()) {
      const pxPerMm2 = mmToPx(1) * mmToPx(1);
      b = despeckle(b, W, H,
                    Math.round(cfg.material.speck_area_mm2 * pxPerMm2),
                    Math.round(cfg.material.speck_area_mm2 * pxPerMm2));
    }
    return b;
  }

  function describe() {
    const mode = $("mode").value;
    const cut = mode === "manual" ? $("cut").value
      : (mode === "otsu" ? $("mode").dataset.otsu : null);
    return `${mode}${cut !== null ? " (cut " + cut + ")" : ""}` +
      ` · γ${knobs.gamma()}` +
      (knobs.morph() ? ` · morph ${knobs.morph() > 0 ? "+" : ""}${knobs.morph()}px` : "") +
      ` · ink ${inkPct(curBin, mask).toFixed(1)}%` +
      (cfg.material.invert ? " · inverted" : "");
  }

  function render() {
    if (!gray) return;
    const mode = $("mode").value;
    $("cut").disabled = mode !== "manual";
    $("cutv").textContent = mode === "manual" ? $("cut").value
      : (mode === "otsu" ? ($("mode").dataset.otsu || "—") : "—");
    const t0 = performance.now();
    curBin = currentBin();
    const cv = $("preview");
    cv.width = W; cv.height = H;
    const ctx = cv.getContext("2d");
    const id = ctx.createImageData(W, H);
    for (let i = 0; i < curBin.length; i++) {
      const v = curBin[i];
      id.data[i * 4] = id.data[i * 4 + 1] = id.data[i * 4 + 2] = v;
      id.data[i * 4 + 3] = 255;
    }
    ctx.putImageData(id, 0, 0);

    const minPx = mmToPx(cfg.material.min_feature_mm);
    const loss = minFeatureLoss(curBin, W, H, minPx);
    const lossMsg = loss > 5
      ? ` · ⚠ ${loss.toFixed(0)}% of ink in strokes < ${cfg.material.min_feature_mm}mm — will drop out`
      : "";
    $("ink").innerHTML = `ink ${inkPct(curBin, mask).toFixed(1)}%` +
      (mask ? " (in subject)" : "") +
      ` · ${Math.round(performance.now() - t0)}ms` +
      `<span class="warn2">${lossMsg}</span>`;
    $("livecap").textContent = "live: " + describe();
    cv.toBlob(b => {
      if (blobUrl) URL.revokeObjectURL(blobUrl);
      blobUrl = URL.createObjectURL(b);
      $("openfull").href = blobUrl;
    }, "image/png");
  }

  let renderTimer = null;
  function renderSoon() {              // debounce slider drags at 2MP
    clearTimeout(renderTimer);
    renderTimer = setTimeout(render, 60);
  }

  img.onload = () => {
    W = Math.max(1, Math.round(mmToPx(cfg.width_mm)));
    H = Math.max(1, Math.round(img.naturalHeight * W / img.naturalWidth));
    const cv = document.createElement("canvas");
    cv.width = W; cv.height = H;
    cv.getContext("2d").drawImage(img, 0, 0, W, H);
    const data = cv.getContext("2d").getImageData(0, 0, W, H).data;
    mask = cfg.stylized ? maskFromAlpha(data, W * H) : null;
    gray = toGray(data, W * H);
    procCache = { key: null, gray: null };
    $("dims").textContent =
      `${W}×${H}px = ${fmt(cfg.width_mm)}×${fmt(cfg.width_mm * H / W)}mm @ ${cfg.dpi}dpi`;
    const mid = midtonePct(gray);
    if (mid > 40 && !cfg.stylized)
      $("warn").innerHTML = `<p class="warn">${mid.toFixed(0)}% midtones — this looks ` +
        `like a raw photo, not line art; tick “stylize photo first” on the form.</p>`;
    const oc = otsuCut(gray);
    $("mode").querySelector('option[value="otsu"]').textContent = `otsu (${oc})`;
    $("mode").dataset.otsu = oc;
    // context-aware default (REDESIGN P1-3): line art wants a threshold,
    // raw photos want error diffusion
    if (!cfg.stylized) $("mode").value = "jarvis";
    else if (cfg.material.default_strategy === "manual") {
      $("mode").value = "manual"; $("cut").value = cfg.material.manual_threshold;
    }
    lastAutoMorph = isDither($("mode").value) ? 0
      : (cfg.material.morph_px || 0);
    $("morph").value = lastAutoMorph;
    render();
  };

  function isDither(mode) {
    return mode === "bayer" || DITHER_KERNELS[mode] !== undefined;
  }

  $("mode").addEventListener("change", () => {
    // material morph default (+1px on cork) fattens hatching after a
    // threshold, but dilating dither dots fuses the pattern solid — the
    // default flips with the mode family; the operator can still override
    const want = isDither($("mode").value) ? 0 : (cfg.material.morph_px || 0);
    if (+$("morph").value === lastAutoMorph) $("morph").value = want;
    lastAutoMorph = want;
    render();
  });
  ["cut", "bp", "wp", "gamma", "usm_mm", "usm_amt", "morph"]
    .forEach(id => $(id).addEventListener("input", renderSoon));
  ["despeckle", "serpentine"].forEach(id => $(id).addEventListener("change", render));

  $("snapbtn").addEventListener("click", () => {
    const sc = $("snap");
    sc.width = W; sc.height = H;
    sc.getContext("2d").drawImage($("preview"), 0, 0);
    $("snapcap").textContent = "📌 " + describe();
    $("snappane").style.display = "";
    sc.toBlob(b => {
      if (snapUrl) URL.revokeObjectURL(snapUrl);
      snapUrl = URL.createObjectURL(b);
      $("opensnap").href = snapUrl;
    }, "image/png");
  });

  $("snapclear").addEventListener("click", e => {
    e.preventDefault();
    $("snappane").style.display = "none";
    if (snapUrl) { URL.revokeObjectURL(snapUrl); snapUrl = null; }
  });

  function metaObj(mode, cut) {
    return {
      tool: "laserprep-web", mode, cut, width_mm: cfg.width_mm, dpi: cfg.dpi,
      material: cfg.material.name, invert: cfg.material.invert,
      bp: knobs.bp(), wp: knobs.wp(), gamma: knobs.gamma(),
      usm_mm: knobs.usmMm(), usm_amt: knobs.usmAmt(),
      morph_px: knobs.morph(), despeckle: knobs.despeckle(),
      serpentine: knobs.serpentine(),
      seed: cfg.seed, steps: cfg.steps, true_cfg: cfg.true_cfg, mp: cfg.mp,
      preset: cfg.preset || undefined,
      prompt: cfg.prompt || undefined, neg: cfg.neg || undefined,
      crop: cfg.crop || undefined,
      input_sha256: cfg.input_sha256,
      ink_pct: fmt(inkPct(curBin, mask)),
    };
  }

  $("save").addEventListener("click", () => {
    $("preview").toBlob(async b => {
      const raw = new Uint8Array(await b.arrayBuffer());
      const mode = $("mode").value;
      const cut = mode === "manual" ? +$("cut").value
        : (mode === "otsu" ? +$("mode").dataset.otsu : null);
      const meta = pngWithMeta(raw, cfg.dpi, metaObj(mode, cut));
      const a = document.createElement("a");
      a.href = URL.createObjectURL(new Blob([meta], { type: "image/png" }));
      a.download = `${cfg.stem}_${mode}${cut !== null ? "_" + cut : ""}_1bit.png`;
      a.click();
      setTimeout(() => URL.revokeObjectURL(a.href), 30000);
    }, "image/png");
  });

  $("contact").addEventListener("click", () => {
    // calibration contact sheet: the material's cut list on one canvas,
    // burned once to pick a threshold (REDESIGN P2)
    const cuts = cfg.material.contact_thresholds || [96, 112, 128, 144, 160, 176];
    const g = processedGray();
    const cols = 2, rows = Math.ceil(cuts.length / cols), label = 28;
    const cv = document.createElement("canvas");
    cv.width = W * cols; cv.height = (H + label) * rows;
    const ctx = cv.getContext("2d");
    ctx.fillStyle = "#fff";
    ctx.fillRect(0, 0, cv.width, cv.height);
    cuts.forEach((cut, i) => {
      const b = applyManual(g, cut);
      const id = ctx.createImageData(W, H);
      for (let j = 0; j < b.length; j++) {
        id.data[j * 4] = id.data[j * 4 + 1] = id.data[j * 4 + 2] = b[j];
        id.data[j * 4 + 3] = 255;
      }
      const cx2 = (i % cols) * W, cy2 = ((i / cols) | 0) * (H + label);
      ctx.putImageData(id, cx2, cy2);
      ctx.fillStyle = "#000";
      ctx.font = "bold 20px monospace";
      ctx.fillText(`cut ${cut}`, cx2 + 8, cy2 + H + 20);
    });
    cv.toBlob(async b => {
      const raw = new Uint8Array(await b.arrayBuffer());
      const meta = pngWithMeta(raw, cfg.dpi,
        { tool: "laserprep-contact", cuts, material: cfg.material.name,
          width_mm: cfg.width_mm, dpi: cfg.dpi });
      const a = document.createElement("a");
      a.href = URL.createObjectURL(new Blob([meta], { type: "image/png" }));
      a.download = `${cfg.stem}_contact_1bit.png`;
      a.click();
      setTimeout(() => URL.revokeObjectURL(a.href), 30000);
    }, "image/png");
  });

  function showArt(url) {
    artUrl = url;
    $("openart").href = artUrl;
    img.src = artUrl;
    $("art").src = artUrl;
  }

  /* art delivery: either inline (raw path) or async job (stylize path) */
  if (cfg.artSrc) {
    fetch(cfg.artSrc).then(r => r.blob())
      .then(b => showArt(URL.createObjectURL(b)));
  } else if (cfg.job) {
    const t0 = performance.now();
    const eta = cfg.job.eta_s;
    const tick = async () => {
      try {
        const p = await (await fetch(`/stylize/progress/${cfg.job.key}`)).json();
        const el = (performance.now() - t0) / 1000 +
                   (cfg.job.elapsed_s || 0);
        if (p.state === "done") {
          $("progress").style.display = "none";
          const r = await fetch(`/stylize/result/${cfg.job.key}`);
          showArt(URL.createObjectURL(await r.blob()));
          return;
        }
        if (p.state === "error" || p.state === "unknown") {
          $("progtext").textContent = "render failed: " + (p.error || "unknown");
          $("progbar").style.background = "#c33";
          return;
        }
        const e2 = p.eta_s || eta;
        $("progtext").textContent =
          `${p.state}… ${Math.round(p.elapsed_s || el)}s` +
          (e2 ? ` / ~${Math.round(e2)}s` : "");
        if (e2) $("progbar").style.width =
          Math.min(97, 100 * (p.elapsed_s || el) / e2) + "%";
        setTimeout(tick, 2000);
      } catch (e) { setTimeout(tick, 4000); }
    };
    tick();
  }
})();

if (typeof module !== "undefined")
  module.exports = { toGray, maskFromAlpha, applyLevels, boxBlur, unsharp,
                     otsuCut, applyManual, applyAdaptive, applyDither,
                     morphology, despeckle, inkPct, minFeatureLoss,
                     midtonePct, crc32, makeChunk, pngWithMeta, readMeta };
