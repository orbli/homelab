/* laserprep client-side engine: threshold + dither + PNG metadata.
 *
 * The cached diffusion output is the artifact; everything here is a cheap
 * deterministic transform the browser recomputes live. The download button
 * splices pHYs (DPI) and tEXt (params) chunks into the canvas PNG so xTool
 * Studio imports at true physical size.
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

function applyDither(gray, w, h, mode) {
  const b = new Uint8Array(gray.length);
  if (mode === "bayer") {
    for (let y = 0; y < h; y++)
      for (let x = 0; x < w; x++)
        b[y * w + x] = gray[y * w + x] / 255 >= (BAYER8[y & 7][x & 7] + 1) / 65 ? 255 : 0;
    return b;
  }
  const { d, k } = DITHER_KERNELS[mode];
  const buf = Float32Array.from(gray);
  for (let y = 0; y < h; y++) {
    for (let x = 0; x < w; x++) {
      const i = y * w + x, old = buf[i], nv = old >= 128 ? 255 : 0;
      b[i] = nv;
      const err = (old - nv) / d;
      if (err)
        for (const [dy, dx, wt] of k) {
          const nx = x + dx, ny = y + dy;
          if (nx >= 0 && nx < w && ny < h) buf[ny * w + nx] += err * wt;
        }
    }
  }
  return b;
}

function inkPct(bin) {
  let n = 0;
  for (let i = 0; i < bin.length; i++) if (!bin[i]) n++;
  return 100 * n / bin.length;
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

/* ---------- browser UI ------------------------------------------------- */

if (typeof document !== "undefined") (function () {
  const cfg = window.LP;  // injected by server: art, physical, material, render info
  const $ = id => document.getElementById(id);
  const img = new Image();
  let gray = null, W = 0, H = 0, curBin = null, blobUrl = null, artUrl = null;

  function fmt(x) { return Math.round(x * 10) / 10; }

  img.onload = () => {
    W = Math.max(1, Math.round(cfg.width_mm / 25.4 * cfg.dpi));
    H = Math.max(1, Math.round(img.naturalHeight * W / img.naturalWidth));
    const cv = document.createElement("canvas");
    cv.width = W; cv.height = H;
    cv.getContext("2d").drawImage(img, 0, 0, W, H);
    gray = toGray(cv.getContext("2d").getImageData(0, 0, W, H).data, W * H);
    $("dims").textContent =
      `${W}×${H}px = ${fmt(cfg.width_mm)}×${fmt(cfg.width_mm * H / W)}mm @ ${cfg.dpi}dpi`;
    const mid = midtonePct(gray);
    if (mid > 40 && !cfg.stylized)
      $("warn").innerHTML = `<p class="warn">${mid.toFixed(0)}% midtones — this looks ` +
        `like a raw photo, not line art; tick “stylize photo first” on the form.</p>`;
    const oc = otsuCut(gray);
    $("mode").querySelector('option[value="otsu"]').textContent = `otsu (${oc})`;
    $("mode").dataset.otsu = oc;
    if (cfg.material.default_strategy === "manual") {
      $("mode").value = "manual"; $("cut").value = cfg.material.manual_threshold;
    }
    render();
  };

  function currentBin() {
    const mode = $("mode").value;
    if (mode === "otsu") return applyManual(gray, +$("mode").dataset.otsu);
    if (mode === "manual") return applyManual(gray, +$("cut").value);
    if (mode === "adaptive") {
      const blockPx = Math.round(cfg.material.adaptive_block_mm / 25.4 * cfg.dpi);
      return applyAdaptive(gray, W, H, blockPx, cfg.material.adaptive_c);
    }
    return applyDither(gray, W, H, mode);
  }

  function render() {
    const mode = $("mode").value;
    $("cut").disabled = mode !== "manual";
    $("cutv").textContent = mode === "manual" ? $("cut").value
      : (mode === "otsu" ? $("mode").dataset.otsu : "—");
    const t0 = performance.now();
    curBin = currentBin();
    const inv = cfg.material.invert;
    const cv = $("preview");
    cv.width = W; cv.height = H;
    const ctx = cv.getContext("2d");
    const id = ctx.createImageData(W, H);
    for (let i = 0; i < curBin.length; i++) {
      const v = inv ? 255 - curBin[i] : curBin[i];
      id.data[i * 4] = id.data[i * 4 + 1] = id.data[i * 4 + 2] = v;
      id.data[i * 4 + 3] = 255;
    }
    ctx.putImageData(id, 0, 0);
    $("ink").textContent = `ink ${inkPct(curBin).toFixed(1)}% · ` +
      `${Math.round(performance.now() - t0)}ms` + (inv ? " · inverted output" : "");
    cv.toBlob(b => {
      if (blobUrl) URL.revokeObjectURL(blobUrl);
      blobUrl = URL.createObjectURL(b);
      $("openfull").href = blobUrl;
    }, "image/png");
  }

  $("mode").addEventListener("change", render);
  $("cut").addEventListener("input", render);

  $("save").addEventListener("click", () => {
    $("preview").toBlob(async b => {
      const raw = new Uint8Array(await b.arrayBuffer());
      const mode = $("mode").value;
      const cut = mode === "manual" ? +$("cut").value
        : (mode === "otsu" ? +$("mode").dataset.otsu : null);
      const meta = pngWithMeta(raw, cfg.dpi, {
        tool: "laserprep-web", mode, cut, width_mm: cfg.width_mm, dpi: cfg.dpi,
        material: cfg.material.name, invert: cfg.material.invert,
        seed: cfg.seed, steps: cfg.steps, input_sha256: cfg.input_sha256,
        ink_pct: fmt(inkPct(curBin)),
      });
      const a = document.createElement("a");
      a.href = URL.createObjectURL(new Blob([meta], { type: "image/png" }));
      a.download = `${cfg.stem}_${mode}${cut !== null ? "_" + cut : ""}_1bit.png`;
      a.click();
      setTimeout(() => URL.revokeObjectURL(a.href), 30000);
    }, "image/png");
  });

  // stylized art: clickable full view via blob URL (data: URIs won't open in a tab)
  fetch(cfg.artSrc).then(r => r.blob()).then(b => {
    artUrl = URL.createObjectURL(b);
    $("openart").href = artUrl;
  });
  img.src = cfg.artSrc;
  $("art").src = cfg.artSrc;
})();

if (typeof module !== "undefined")
  module.exports = { toGray, otsuCut, applyManual, applyAdaptive, applyDither,
                     inkPct, midtonePct, crc32, makeChunk, pngWithMeta };
