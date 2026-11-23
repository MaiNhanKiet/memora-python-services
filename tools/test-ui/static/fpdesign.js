// Fingerprint artwork design panel (customer side, tab 1). Dev tool only.
// Loaded after app.js (uses $, el, log, workerCall) and before sim.js, which calls
//   FpDesign.configure({ selectedPrefix: () => 'personalization/test-ui/fingerprint/{orderId}/selected/' })
//   FpDesign.open({ outputPrefix, sourceId, context })   after the staff picked an attempt
//   FpDesign.reset()                                       when the mock order/session is reset
//   FpDesign.currentPngKey()                               key of the current version PNG
(() => {
  const SIZE = 640; // editor canvas, shows the whole square base.png
  const MAX_VERSIONS = 10;
  const MAX_STROKES = 50;
  const MAX_POINTS = 200;
  const S = {
    cfg: { selectedPrefix: () => '' }, prefix: '', sourceId: '', context: null, base: null,
    versions: [], currentId: null, accepted: null, shape: 'NATURAL', ox: 0, oy: 0, zoom: 1,
    strokes: [], redo: [], mode: 'move', brush: 0.03, busy: false, presets: null,
    drag: null, cur: null, viewer: null, hover: null,
  };
  const objUrl = (key) => `/dev/object?key=${encodeURIComponent(key)}&t=${Date.now()}`;
  const setText = (id, t, cls) => { const n = $(id); n.textContent = t; if (cls !== undefined) n.className = cls; };
  const cv = $('fd-canvas');
  const g = cv.getContext('2d');
  const locked = () => !!S.accepted;

  // ------------------------------------------------------------ frame outlines (match the worker)
  function heartPoints(b) {
    const pts = []; let minX = 1e9, maxX = -1e9, minY = 1e9, maxY = -1e9;
    for (let k = 0; k < 360; k++) {
      const t = (k * Math.PI) / 180;
      const x = 16 * Math.sin(t) ** 3;
      const y = 13 * Math.cos(t) - 5 * Math.cos(2 * t) - 2 * Math.cos(3 * t) - Math.cos(4 * t);
      pts.push([x, y]); minX = Math.min(minX, x); maxX = Math.max(maxX, x); minY = Math.min(minY, y); maxY = Math.max(maxY, y);
    }
    const m = 0.02 * b;
    return pts.map(([x, y]) => [m + ((x - minX) / (maxX - minX)) * (b - 2 * m), m + (1 - (y - minY) / (maxY - minY)) * (b - 2 * m)]);
  }
  /** Adds the frame outline (box of side b centred on cx, cy) to the current path. */
  function shapePath(shape, cx, cy, b) {
    const x0 = cx - b / 2, y0 = cy - b / 2;
    g.moveTo(0, 0);
    if (shape === 'CIRCLE') { g.moveTo(cx + 0.49 * b, cy); g.arc(cx, cy, 0.49 * b, 0, Math.PI * 2); }
    else if (shape === 'SQUARE') { const m = 0.01 * b; g.roundRect(x0 + m, y0 + m, b - 2 * m, b - 2 * m, 0.12 * b); }
    else if (shape === 'HEART') {
      heartPoints(b).forEach(([x, y], i) => (i ? g.lineTo(x0 + x, y0 + y) : g.moveTo(x0 + x, y0 + y)));
      g.closePath();
    } else { g.moveTo(cx + 0.47 * b, cy); g.ellipse(cx, cy, 0.47 * b, 0.5 * b, 0, 0, Math.PI * 2); } // NATURAL: approximate
  }

  // ------------------------------------------------------------ drawing
  function frameBox() { return { b: SIZE / S.zoom, cx: SIZE / 2 + (S.ox * SIZE) / 2, cy: SIZE / 2 + (S.oy * SIZE) / 2 }; }
  function drawStroke(st) {
    g.strokeStyle = '#fff'; g.fillStyle = '#fff'; g.lineCap = 'round'; g.lineJoin = 'round';
    g.lineWidth = st.radius * SIZE * 2;
    g.beginPath();
    st.points.forEach(([x, y], i) => (i ? g.lineTo(x * SIZE, y * SIZE) : g.moveTo(x * SIZE, y * SIZE)));
    if (st.points.length === 1) { g.arc(st.points[0][0] * SIZE, st.points[0][1] * SIZE, st.radius * SIZE, 0, Math.PI * 2); g.fill(); } else g.stroke();
  }
  function draw() {
    g.clearRect(0, 0, SIZE, SIZE); g.fillStyle = '#fff'; g.fillRect(0, 0, SIZE, SIZE);
    if (S.base) g.drawImage(S.base, 0, 0, SIZE, SIZE);
    else { g.fillStyle = '#888'; g.font = '14px sans-serif'; g.fillText('không có base.png (đã chấp nhận hoặc đã xóa)', 12, 24); }
    const all = S.cur ? [...S.strokes, S.cur] : S.strokes;
    all.forEach(drawStroke);
    const { b, cx, cy } = frameBox();
    g.save(); g.beginPath(); g.rect(0, 0, SIZE, SIZE); shapePath(S.shape, cx, cy, b);
    g.fillStyle = 'rgba(20,30,60,0.5)'; g.fill('evenodd'); g.restore();
    g.beginPath(); shapePath(S.shape, cx, cy, b); g.strokeStyle = '#2f6fed'; g.lineWidth = 2; g.stroke();
    if (S.mode === 'erase' && S.hover && !locked()) {
      g.beginPath(); g.arc(S.hover[0] * SIZE, S.hover[1] * SIZE, S.brush * SIZE, 0, Math.PI * 2);
      g.strokeStyle = '#d4372c'; g.lineWidth = 1.5; g.stroke();
    }
  }
  const norm = (ev) => { const r = cv.getBoundingClientRect(); return [(ev.clientX - r.left) / r.width, (ev.clientY - r.top) / r.height]; };
  const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));

  // ------------------------------------------------------------ editor interaction
  cv.addEventListener('pointerdown', (ev) => {
    if (locked() || S.busy) return;
    cv.setPointerCapture(ev.pointerId);
    const p = norm(ev);
    if (S.mode === 'erase') {
      if (S.strokes.length >= MAX_STROKES) { setText('fd-msg', `tối đa ${MAX_STROKES} nét tẩy`, 'err'); return; }
      S.cur = { radius: S.brush, points: [p] }; S.redo = [];
    } else S.drag = { x: ev.clientX, y: ev.clientY, ox: S.ox, oy: S.oy };
    draw();
  });
  cv.addEventListener('pointermove', (ev) => {
    const p = norm(ev); S.hover = p;
    if (S.cur) {
      const last = S.cur.points[S.cur.points.length - 1];
      if (Math.hypot(p[0] - last[0], p[1] - last[1]) > 0.004) {
        if (S.cur.points.length >= MAX_POINTS) { // start a new stroke (points per stroke are limited)
          S.strokes.push(S.cur);
          if (S.strokes.length >= MAX_STROKES) { S.cur = null; refresh(); draw(); return; }
          S.cur = { radius: S.brush, points: [last] };
        }
        S.cur.points.push(p);
      }
    } else if (S.drag) {
      const r = cv.getBoundingClientRect();
      S.ox = clamp(S.drag.ox + ((ev.clientX - S.drag.x) / r.width) * 2, -1, 1);
      S.oy = clamp(S.drag.oy + ((ev.clientY - S.drag.y) / r.height) * 2, -1, 1);
      syncReadout();
    }
    draw();
  });
  const endPointer = () => {
    if (S.cur) { S.strokes.push(S.cur); S.cur = null; refresh(); }
    S.drag = null; draw();
  };
  cv.addEventListener('pointerup', endPointer);
  cv.addEventListener('pointercancel', endPointer);
  cv.addEventListener('pointerleave', () => { S.hover = null; draw(); });
  cv.addEventListener('wheel', (ev) => {
    if (locked()) return;
    ev.preventDefault(); setZoom(S.zoom * (ev.deltaY < 0 ? 1.06 : 1 / 1.06));
  }, { passive: false });

  function setZoom(z) { S.zoom = Math.round(clamp(z, 1, 3) * 100) / 100; $('fd-zoom').value = S.zoom; syncReadout(); draw(); }
  function syncReadout() {
    setText('fd-readout', `zoom ${S.zoom.toFixed(2)} - offsetX ${S.ox.toFixed(2)} - offsetY ${S.oy.toFixed(2)} - ${S.strokes.length}/${MAX_STROKES} nét tẩy`, 'muted');
  }
  $('fd-zoom').oninput = () => setZoom(Number($('fd-zoom').value));
  $('fd-brush').oninput = () => { S.brush = Number($('fd-brush').value); setText('fd-brush-label', `cỡ cọ ${(S.brush * 100).toFixed(1)} %`, 'muted'); draw(); };
  $('fd-mode-move').onclick = () => { S.mode = 'move'; refresh(); };
  $('fd-mode-erase').onclick = () => { S.mode = 'erase'; refresh(); };
  $('fd-undo').onclick = () => { if (S.strokes.length) { S.redo.push(S.strokes.pop()); refresh(); draw(); } };
  $('fd-redo').onclick = () => { if (S.redo.length) { S.strokes.push(S.redo.pop()); refresh(); draw(); } };
  $('fd-clear').onclick = () => { S.strokes = []; S.redo = []; refresh(); draw(); };

  // ------------------------------------------------------------ frames from the worker
  function paintFrames() {
    if (!S.presets) return;
    $('fd-frames').replaceChildren(...S.presets.shapes.map((sh) => el('button', {
      class: 'stylecard', type: 'button', 'aria-pressed': String(sh.code === S.shape), ...(locked() ? { disabled: '' } : {}),
      onclick: () => { S.shape = sh.code; paintFrames(); draw(); },
    }, el('strong', {}, `${sh.name} (${sh.code})`),
    el('img', { src: `data:image/svg+xml;charset=utf-8,${encodeURIComponent(sh.sampleSvg)}`, alt: `Khung ${sh.name}` }),
    el('small', {}, sh.description))));
  }
  async function loadFrames() {
    try {
      const res = await fetch('/worker/v1/fingerprint/presets');
      const json = await res.json().catch(() => null);
      log(`GET /worker/v1/fingerprint/presets -> ${res.status}`, res.ok ? 'ok' : 'err');
      if (!res.ok || !json || !json.data) throw new Error(`${(json && json.code) || res.status}: ${(json && json.message) || ''}`);
      S.presets = json.data; paintFrames();
    } catch (e) {
      $('fd-frames').replaceChildren(el('div', { class: 'stylecard err' }, el('strong', { class: 'err' }, 'Không tải được danh sách khung'), el('small', {}, e.message)));
    }
  }

  // ------------------------------------------------------------ versions
  const nextVersionId = () => {
    const nums = S.versions.map((v) => Number((/^v(\d+)$/.exec(v.versionId) || [])[1])).filter((n) => !Number.isNaN(n));
    return `v${nums.length ? Math.max(...nums) + 1 : 1}`;
  };
  const versionOf = (id) => S.versions.find((v) => v.versionId === id);
  function useVersion(v) {
    S.currentId = v.versionId;
    if (!locked()) { S.shape = v.shape; S.ox = v.offsetX; S.oy = v.offsetY; S.zoom = v.zoom; S.strokes = (v.strokes || []).map((s) => ({ radius: s.radius, points: s.points.map((p) => [...p]) })); S.redo = []; $('fd-zoom').value = S.zoom; }
    paintFrames(); paintVersions(); syncReadout(); draw(); refresh(); update3d();
  }
  function paintVersions() {
    $('fd-versions').replaceChildren(...S.versions.map((v) => el('div', { class: `card${v.versionId === S.currentId ? ' fd-current' : ''}` },
      el('img', { src: objUrl(v.pngKey), alt: v.versionId, class: 'checker', style: 'width:120px;height:120px;object-fit:contain' }),
      el('div', {}, el('strong', {}, v.versionId), v.versionId === S.currentId ? ' (đang dùng)' : '', S.accepted === v.versionId ? ' (đã chấp nhận)' : ''),
      el('small', { class: 'muted' }, `${v.shape} - zoom ${v.zoom} - tẩy ${(v.erasedRatio * 100).toFixed(1)} % - phủ ${(v.coverage * 100).toFixed(0)} % - tối thiểu ${v.minEngraveSizeMm} mm`),
      el('div', {}, el('button', { class: 'secondary', type: 'button', onclick: () => useVersion(v) }, 'Dùng bản này')))));
  }
  const fromWorker = (ver, outputs, strokes) => ({
    ...ver, strokes, svgKey: outputs.vectorSvg.key, pngKey: outputs.alphaPng.key,
  });

  // ------------------------------------------------------------ actions
  function refresh() {
    const lock = locked();
    for (const id of ['fd-zoom', 'fd-brush', 'fd-mode-move', 'fd-mode-erase', 'fd-clear']) $(id).disabled = lock || S.busy;
    $('fd-undo').disabled = lock || S.busy || !S.strokes.length;
    $('fd-redo').disabled = lock || S.busy || !S.redo.length;
    $('fd-render').disabled = lock || S.busy || !S.base || S.versions.length >= MAX_VERSIONS;
    $('fd-accept').disabled = lock || S.busy || !S.currentId;
    $('fd-mode-move').setAttribute('aria-pressed', String(S.mode === 'move'));
    $('fd-mode-erase').setAttribute('aria-pressed', String(S.mode === 'erase'));
    cv.style.cursor = lock ? 'not-allowed' : S.mode === 'erase' ? 'crosshair' : 'move';
    syncReadout(); paintFrames();
  }
  $('fd-render').onclick = async () => {
    if (locked() || S.busy || !S.base) return;
    S.busy = true; refresh(); setText('fd-msg', 'đang xử lý...', 'muted');
    const versionId = nextVersionId();
    const strokes = S.strokes.map((s) => ({ radius: s.radius, points: s.points.map((p) => [Math.min(1, Math.max(0, p[0])), Math.min(1, Math.max(0, p[1]))]) }));
    const body = {
      sourceId: S.sourceId, outputPrefix: S.prefix, versionId, shape: S.shape,
      offsetX: Math.round(S.ox * 1000) / 1000, offsetY: Math.round(S.oy * 1000) / 1000, zoom: S.zoom,
      eraseStrokes: strokes, ...(S.context ? { context: S.context } : {}),
    };
    try {
      const r = await workerCall('/v1/fingerprint/render-crop', body);
      if (!r.ok || !r.data) { setText('fd-msg', `${r.code || r.status}: ${r.message || ''}`, 'err'); return; }
      const v = fromWorker(r.data.version, r.data.outputs, strokes);
      S.versions = [...S.versions.filter((x) => x.versionId !== v.versionId), v];
      setText('fd-msg', `đã tạo ${v.versionId}`, 'ok');
      useVersion(v);
    } catch (e) { setText('fd-msg', `lỗi: ${e.message}`, 'err'); } finally { S.busy = false; refresh(); }
  };
  $('fd-accept').onclick = async () => {
    if (locked() || S.busy || !S.currentId) return;
    S.busy = true; refresh(); setText('fd-msg', 'đang chấp nhận...', 'muted');
    try {
      const r = await workerCall('/v1/fingerprint/select-version', { sourceId: S.sourceId, outputPrefix: S.prefix, versionId: S.currentId, ...(S.context ? { context: S.context } : {}) });
      if (!r.ok || !r.data) { setText('fd-msg', `${r.code || r.status}: ${r.message || ''}`, 'err'); return; }
      S.accepted = S.currentId; S.versions = S.versions.filter((v) => v.versionId === S.accepted); // base.png is deleted on the server; the loaded copy stays for display
      setText('fd-msg', `Đã chấp nhận bản ${S.accepted}, đã xóa các bản khác và base.png (${(r.data.deleted || []).length} file)`, 'ok');
      paintVersions();
    } catch (e) { setText('fd-msg', `lỗi: ${e.message}`, 'err'); } finally { S.busy = false; refresh(); }
  };

  // ------------------------------------------------------------ 3D on SIGNET_001_FP
  async function update3d() {
    const v = versionOf(S.currentId);
    if (!S.viewer || !v) return;
    try {
      const blob = await (await fetch(objUrl(v.pngKey))).blob();
      await S.viewer.setImageUrl(URL.createObjectURL(blob));
    } catch (e) { log(`3D: ${e.message}`, 'err'); }
  }
  $('fd-3d-open').onclick = async () => {
    if (!versionOf(S.currentId)) { setText('fd-msg', 'chưa có bản nào để xem', 'err'); return; }
    try {
      if (!S.viewer) S.viewer = await window.Ring3D.mount($('fd-3d'), { model: 'SIGNET_001_FP', kind: 'FINGERPRINT' });
      await update3d();
    } catch (e) { S.viewer = null; $('fd-3d').replaceChildren(el('span', { class: 'err' }, `3D lỗi: ${e.message}`)); }
  };

  // ------------------------------------------------------------ open / resume
  async function loadBase(key) {
    S.base = null;
    if (!key) return;
    try {
      const res = await fetch(objUrl(key));
      if (!res.ok) return;
      const img = new Image(); const url = URL.createObjectURL(await res.blob());
      await new Promise((ok, fail) => { img.onload = ok; img.onerror = () => fail(new Error('base.png lỗi')); img.src = url; });
      S.base = img;
    } catch (e) { log(`base.png: ${e.message}`, 'err'); }
  }
  function resetState() {
    Object.assign(S, { versions: [], currentId: null, accepted: null, shape: 'NATURAL', ox: 0, oy: 0, zoom: 1, strokes: [], redo: [], mode: 'move', cur: null, drag: null });
    if (S.viewer) { S.viewer.dispose(); S.viewer = null; $('fd-3d').replaceChildren($('fd-3d-open')); }
    $('fd-zoom').value = 1;
  }
  async function open({ outputPrefix, sourceId, context }) {
    resetState();
    Object.assign(S, { prefix: outputPrefix, sourceId, context: context || null });
    $('fd-panel').hidden = false; setText('fd-msg', 'đang tải ảnh nền...', 'muted');
    await loadFrames();
    await loadBase(`${outputPrefix}base.png`);
    // v0 (default NATURAL version) was created by fingerprint/process
    const res = await fetch(objUrl(`${outputPrefix}manifest.json`));
    if (res.ok) applyManifest(await res.json());
    setText('fd-msg', S.base ? 'kéo để dời khung, cuộn để zoom, chế độ Tẩy để xóa nốt đen' : 'không tải được base.png', S.base ? 'muted' : 'err');
    $('fd-brush').oninput(); refresh(); draw();
  }
  function applyManifest(doc) {
    const vs = Object.values(doc.versions || {});
    S.versions = vs.map((v) => fromWorker(v, { vectorSvg: v.vectorSvg, alphaPng: v.alphaPng }, v.eraseStrokes || []));
    S.accepted = doc.accepted ? doc.currentVersionId : null;
    const cur = versionOf(doc.currentVersionId) || S.versions[S.versions.length - 1];
    if (cur) { S.accepted = doc.accepted ? cur.versionId : null; useVersion(cur); } else { paintVersions(); }
  }
  $('fd-resume').onclick = async () => {
    const box = $('fd-resume-list');
    box.replaceChildren(el('span', { class: 'muted' }, 'đang liệt kê Garage...'));
    try {
      const prefix = S.cfg.selectedPrefix();
      const res = await fetch(`/dev/list?prefix=${encodeURIComponent(prefix)}`);
      const j = await res.json();
      if (!res.ok) throw new Error(`${res.status} ${j.message || j.error || ''}`);
      const manifests = j.keys.filter((k) => k.key.endsWith('/manifest.json')).sort((a, b) => b.lastModified.localeCompare(a.lastModified));
      const docs = (await Promise.all(manifests.map(async (m) => {
        try { return { m, doc: await (await fetch(objUrl(m.key))).json() }; } catch { return null; }
      }))).filter((x) => x && x.doc && x.doc.kind === 'FINGERPRINT' && x.doc.versions);
      if (!docs.length) { box.replaceChildren(el('span', { class: 'muted' }, `không có manifest vân tay dưới ${prefix}`)); return; }
      box.replaceChildren(el('table', {}, el('tbody', {}, docs.map(({ m, doc }) => el('tr', {},
        el('td', {}, m.lastModified.replace('T', ' ').slice(0, 19)),
        el('td', {}, (doc.request || {}).sourceId || ''),
        el('td', {}, `${Object.keys(doc.versions).length} bản, hiện ${doc.currentVersionId || '?'}${doc.accepted ? ' (đã chấp nhận)' : ''}`),
        el('td', {}, el('button', { class: 'secondary', type: 'button', onclick: () => resume(doc) }, 'Chọn')))))));
    } catch (e) { box.replaceChildren(el('span', { class: 'err' }, `lỗi: ${e.message}`)); }
  };
  async function resume(doc) {
    const rq = doc.request || {};
    resetState();
    Object.assign(S, { prefix: rq.outputPrefix, sourceId: rq.sourceId, context: doc.context || null });
    $('fd-panel').hidden = false; $('fd-resume-list').replaceChildren();
    await loadFrames();
    await loadBase(doc.base && doc.base.key);
    applyManifest(doc);
    setText('fd-msg', `đã tải lại ${rq.sourceId}${S.accepted ? ' (đã chấp nhận, khóa)' : ''}${S.base ? '' : ' (không còn base.png)'}`, 'ok');
    $('fd-brush').oninput(); refresh(); draw();
  }

  window.FpDesign = {
    configure(cfg) { Object.assign(S.cfg, cfg); },
    open,
    reset() { resetState(); $('fd-panel').hidden = true; },
    currentPngKey() { const v = versionOf(S.currentId); return v ? v.pngKey : null; },
  };
})();
