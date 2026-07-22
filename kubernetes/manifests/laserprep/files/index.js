/* laserprep upload-page engine: crop rectangle (pre-filled from BiRefNet),
 * prompt presets, 4-seed Lightning preview, params-restore from a saved
 * PNG's tEXt chunk, and per-material localStorage.
 *
 * The crop is the single biggest quality lever (REDESIGN P0-2): it points
 * the model's megapixel budget at the subject instead of the carpet. The
 * mask call is cheap (~2s) and cached server-side, so it runs on every
 * file select; the crop box is normalized [0,1] so the backend never needs
 * the pixel dimensions.
 */
"use strict";
(function () {
  const $ = id => document.getElementById(id);
  const PRESETS = window.LP_PRESETS || {};
  let imgW = 0, imgH = 0, box = null, dragging = null, photoUrl = null;
  let fileBytes = null;

  /* ---------- crop rectangle over the photo ---------- */

  const wrap = $("cropwrap"), cv = $("cropcv");

  function aspectVal() {
    const v = $("aspect").value;
    return v === "free" ? 0 : v.split(":").reduce((a, b) => a / b);
  }

  function clampBox() {
    box.w = Math.min(box.w, 1);
    box.h = Math.min(box.h, 1);
    box.x = Math.max(0, Math.min(box.x, 1 - box.w));
    box.y = Math.max(0, Math.min(box.y, 1 - box.h));
  }

  function applyAspect() {
    const a = aspectVal();
    if (!a || !box) return;
    // a = w/h in IMAGE-NORMALIZED units must account for pixel aspect
    const target = a * imgH / imgW;   // normalized w per normalized h
    if (box.w / box.h < target) box.w = Math.min(1, box.h * target);
    else box.h = Math.min(1, box.w / target);
    clampBox();
  }

  function drawBox() {
    if (!box) return;
    const ctx = cv.getContext("2d");
    ctx.drawImage($("photo"), 0, 0, cv.width, cv.height);
    ctx.fillStyle = "rgba(0,0,0,.45)";
    const bx = box.x * cv.width, by = box.y * cv.height,
          bw = box.w * cv.width, bh = box.h * cv.height;
    ctx.fillRect(0, 0, cv.width, by);
    ctx.fillRect(0, by, bx, bh);
    ctx.fillRect(bx + bw, by, cv.width - bx - bw, bh);
    ctx.fillRect(0, by + bh, cv.width, cv.height - by - bh);
    ctx.strokeStyle = "#fff";
    ctx.lineWidth = 2;
    ctx.strokeRect(bx, by, bw, bh);
    ctx.fillStyle = "#fff";
    for (const [hx, hy] of corners())
      ctx.fillRect(hx - 5, hy - 5, 10, 10);
    $("crop").value =
      [box.x, box.y, box.w, box.h].map(v => v.toFixed(4)).join(",");
  }

  function corners() {
    const bx = box.x * cv.width, by = box.y * cv.height,
          bw = box.w * cv.width, bh = box.h * cv.height;
    return [[bx, by], [bx + bw, by], [bx, by + bh], [bx + bw, by + bh]];
  }

  function hit(px, py) {
    const cs = corners();
    for (let i = 0; i < 4; i++)
      if (Math.abs(px - cs[i][0]) < 12 && Math.abs(py - cs[i][1]) < 12)
        return { corner: i };
    const bx = box.x * cv.width, by = box.y * cv.height;
    if (px > bx && px < bx + box.w * cv.width &&
        py > by && py < by + box.h * cv.height)
      return { move: true, ox: px - bx, oy: py - by };
    return null;
  }

  function evPos(e) {
    const r = cv.getBoundingClientRect();
    const t = e.touches ? e.touches[0] : e;
    return [(t.clientX - r.left) * cv.width / r.width,
            (t.clientY - r.top) * cv.height / r.height];
  }

  function onDown(e) {
    if (!box) return;
    const [px, py] = evPos(e);
    dragging = hit(px, py);
    if (dragging) e.preventDefault();
  }

  function onMove(e) {
    if (!dragging || !box) return;
    e.preventDefault();
    const [px, py] = evPos(e);
    if (dragging.move) {
      box.x = (px - dragging.ox) / cv.width;
      box.y = (py - dragging.oy) / cv.height;
    } else {
      const i = dragging.corner;
      const fx = (i % 2) ? box.x : box.x + box.w;   // fixed opposite corner
      const fy = (i < 2) ? box.y + box.h : box.y;
      let nx = px / cv.width, ny = py / cv.height;
      box.x = Math.min(fx, nx); box.w = Math.abs(nx - fx);
      box.y = Math.min(fy, ny); box.h = Math.abs(ny - fy);
      box.w = Math.max(0.03, box.w); box.h = Math.max(0.03, box.h);
      applyAspect();
    }
    clampBox();
    drawBox();
  }

  cv.addEventListener("mousedown", onDown);
  cv.addEventListener("touchstart", onDown, { passive: false });
  window.addEventListener("mousemove", onMove);
  window.addEventListener("touchmove", onMove, { passive: false });
  window.addEventListener("mouseup", () => dragging = null);
  window.addEventListener("touchend", () => dragging = null);
  $("aspect").addEventListener("change", () => {
    applyAspect(); if (box) drawBox();
  });
  $("cropreset").addEventListener("click", e => {
    e.preventDefault();
    box = { x: 0, y: 0, w: 1, h: 1 };
    applyAspect(); drawBox();
  });

  function boxFromMask(maskData, mw, mh) {
    // bbox of bright mask pixels + the house 9% pad, normalized
    let x0 = mw, x1 = 0, y0 = mh, y1 = 0, any = false;
    for (let y = 0; y < mh; y++)
      for (let x = 0; x < mw; x++)
        if (maskData[(y * mw + x) * 4] > 127) {
          any = true;
          if (x < x0) x0 = x; if (x > x1) x1 = x;
          if (y < y0) y0 = y; if (y > y1) y1 = y;
        }
    if (!any) return { x: 0, y: 0, w: 1, h: 1 };
    const px = (x1 - x0) * 0.09, py = (y1 - y0) * 0.09;
    const nx0 = Math.max(0, x0 - px), ny0 = Math.max(0, y0 - py);
    const nx1 = Math.min(mw, x1 + px), ny1 = Math.min(mh, y1 + py);
    return { x: nx0 / mw, y: ny0 / mh,
             w: (nx1 - nx0) / mw, h: (ny1 - ny0) / mh };
  }

  async function fileChanged() {
    const f = $("file").files[0];
    if (!f) return;
    fileBytes = new Uint8Array(await f.arrayBuffer());

    // saved laserprep PNG? restore every knob instead of treating as photo
    const meta = window.lpReadMeta ? window.lpReadMeta(fileBytes) : null;
    if (meta && meta.tool === "laserprep-web") { restoreParams(meta); }

    if (photoUrl) URL.revokeObjectURL(photoUrl);
    photoUrl = URL.createObjectURL(f);
    const photo = $("photo");
    photo.onload = async () => {
      imgW = photo.naturalWidth; imgH = photo.naturalHeight;
      cv.width = Math.min(760, imgW);
      cv.height = Math.round(cv.width * imgH / imgW);
      wrap.style.display = "";
      box = { x: 0, y: 0, w: 1, h: 1 };
      drawBox();
      $("cropstatus").textContent = "finding subject…";
      try {
        const r = await fetch("/stylize/mask", { method: "POST", body: fileBytes });
        if (!r.ok) throw new Error((await r.json()).error || r.status);
        const mb = await r.blob();
        const mi = new Image();
        mi.onload = () => {
          const mc = document.createElement("canvas");
          const sw = 256, sh = Math.round(256 * mi.naturalHeight / mi.naturalWidth);
          mc.width = sw; mc.height = sh;
          mc.getContext("2d").drawImage(mi, 0, 0, sw, sh);
          box = boxFromMask(
            mc.getContext("2d").getImageData(0, 0, sw, sh).data, sw, sh);
          applyAspect();
          clampBox();
          drawBox();
          $("cropstatus").textContent =
            "subject found — drag box or corners to adjust";
          URL.revokeObjectURL(mi.src);
        };
        mi.src = URL.createObjectURL(mb);
      } catch (e) {
        $("cropstatus").textContent =
          "auto-crop unavailable (" + e.message + ") — full frame; drag to crop";
      }
    };
    photo.src = photoUrl;
  }
  $("file").addEventListener("change", fileChanged);

  /* ---------- prompt presets ---------- */

  $("preset").addEventListener("change", () => {
    const p = PRESETS[$("preset").value];
    if (!p) return;
    $("prompt").value = p.prompt;
    $("neg").value = p.neg;
    $("shadow_lift").value = p.shadow_lift;
  });

  /* ---------- 4-seed Lightning preview ---------- */

  $("seedprev").addEventListener("click", async e => {
    e.preventDefault();
    if (!fileBytes) { alert("choose an image first"); return; }
    const base = +$("seed").value || 42;
    const grid = $("seedgrid");
    grid.style.display = "";
    grid.innerHTML = "";
    const cells = [];
    for (let i = 0; i < 4; i++) {
      const d = document.createElement("div");
      d.className = "seedcell";
      d.innerHTML = `<div class="cap">seed ${base + i} — queued…</div>`;
      grid.appendChild(d);
      cells.push(d);
    }
    const q = new URLSearchParams({ fast: 1, crop: $("crop").value || "" });
    const prompt = $("prompt").value.trim(), neg = $("neg").value.trim();
    if (prompt) q.set("prompt", prompt);
    if (neg) q.set("neg", neg);
    if (+$("shadow_lift").value < 1) q.set("shadow_lift", $("shadow_lift").value);
    await Promise.all(cells.map(async (cell, i) => {
      const seed = base + i;
      try {
        const sub = await fetch(`/stylize/jobs?seed=${seed}&` + q, {
          method: "POST", body: fileBytes });
        const j = await sub.json();
        if (!sub.ok) throw new Error(j.error || sub.status);
        for (;;) {
          const p = await (await fetch(`/stylize/progress/${j.key}`)).json();
          if (p.state === "done") break;
          if (p.state === "error") throw new Error(p.error);
          cell.querySelector(".cap").textContent =
            `seed ${seed} — ${p.state} ${Math.round(p.elapsed_s || 0)}s`;
          await new Promise(res => setTimeout(res, 2000));
        }
        const png = await (await fetch(`/stylize/result/${j.key}`)).blob();
        const url = URL.createObjectURL(png);
        cell.innerHTML =
          `<div class="cap">seed ${seed} — click to use</div><img src="${url}">`;
        cell.addEventListener("click", () => {
          $("seed").value = seed;
          cells.forEach(c => c.classList.remove("picked"));
          cell.classList.add("picked");
        });
      } catch (err) {
        cell.querySelector(".cap").textContent = `seed ${seed} — failed: ${err.message}`;
      }
    }));
  });

  /* ---------- params restore + localStorage ---------- */

  function restoreParams(m) {
    const set = (id, v) => { if (v !== undefined && $(id)) $(id).value = v; };
    set("seed", m.seed); set("steps", m.steps); set("cfg", m.true_cfg);
    set("prompt", m.prompt); set("neg", m.neg);
    set("width_mm", m.width_mm); set("dpi", m.dpi);
    set("crop", m.crop); set("preset", m.preset);
    if (m.material && $("material").querySelector(`option[value="${m.material}"]`))
      $("material").value = m.material;
    $("cropstatus").textContent = "params restored from saved PNG";
  }

  const LS_KEY = "laserprep_knobs";
  try {
    const saved = JSON.parse(localStorage.getItem(LS_KEY) || "null");
    if (saved) {
      for (const [id, v] of Object.entries(saved))
        if ($(id) && $(id).type !== "file") $(id).value = v;
      if (saved.__mat) $("material").value = saved.__mat;
    }
  } catch (e) { /* ignore */ }
  document.querySelector("form").addEventListener("submit", () => {
    const keep = {};
    for (const id of ["material", "width_mm", "dpi", "seed", "steps", "cfg",
                      "preset", "aspect"])
      if ($(id)) keep[id] = $(id).value;
    try { localStorage.setItem(LS_KEY, JSON.stringify(keep)); } catch (e) {}
  });

  // dpi follows material default until the operator edits it by hand
  let dpiTouched = false;
  $("dpi").addEventListener("input", () => dpiTouched = true);
  $("material").addEventListener("change", () => {
    const opt = $("material").selectedOptions[0];
    if (!dpiTouched && opt.dataset.dpi) $("dpi").value = opt.dataset.dpi;
  });
})();
