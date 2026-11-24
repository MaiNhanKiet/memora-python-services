// MEMORA test UI: guided simulation (tabs 1 and 2). The page plays Gateway + Operations, no backend needed.
// Loaded after app.js and reuses its globals: $, el, log, pretty, uuid, workerCall, presign, uploadRaw,
// encodeWav, swContentType. Never logs or stores the MQTT password or presigned URLs.
(() => {
  'use strict';

  const MAX_ATTEMPTS = 3;
  const TTL_MS = 15 * 60000;
  const NONCE_RE = /^[A-Za-z0-9_-]{16,64}$/;
  const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
  const SIM_PREFIX = 'personalization/test-ui/';
  const DEFAULT_DEVICE = 'esp32-store-01';
  const hhmmss = (t = Date.now()) => new Date(t).toLocaleTimeString('en-GB');
  const setText = (id, t, cls) => { const n = $(id); n.textContent = t; if (cls !== undefined) n.className = cls; };

  // ------------------------------------------------------------ tabs
  const tabBtns = [...document.querySelectorAll('.tabs button')];
  for (const b of tabBtns) {
    b.onclick = () => {
      for (const x of tabBtns) x.classList.toggle('active', x === b);
      for (const p of document.querySelectorAll('.tabpanel')) p.hidden = p.id !== b.dataset.tab;
      $('order-card').hidden = b.dataset.tab === 'tab-adv';
    };
  }

  // ------------------------------------------------------------ timeline
  /** Copy of an object with the image base64 shortened. */
  function short(obj) {
    const c = JSON.parse(JSON.stringify(obj ?? {}));
    if (typeof c.imageBase64 === 'string') c.imageBase64 = `<base64 ${c.imageBase64.length} chars>`;
    return c;
  }
  const ARROW = { in: '<-', out: '->', worker: 'W', staff: 'S', note: '#' };
  function tl(kind, msg, obj) {
    const box = $('tl');
    const body = obj === undefined ? msg : `${msg} ${JSON.stringify(short(obj))}`;
    box.append(el('div', { class: 'frame' }, el('b', { class: kind === 'in' ? 'in' : 'out' }, `${ARROW[kind] || '#'} `), `${hhmmss()} ${body}`));
    while (box.childNodes.length > 300) box.firstChild.remove();
    box.scrollTop = box.scrollHeight;
    log(`SIM ${ARROW[kind] || '#'} ${msg}`);
  }
  $('tl-clear').onclick = () => { $('tl').textContent = ''; };

  // ------------------------------------------------------------ header: worker health
  async function health() {
    try {
      const res = await fetch('/worker/health');
      const j = await res.json().catch(() => ({}));
      if (res.ok && j.data) {
        const d = j.data; const f = (v) => (v ? 'ok' : 'lỗi');
        setText('h-worker', `worker ${d.status || ''}: potrace ${f(d.potrace)} - ffmpeg ${f(d.ffmpeg)} - storage ${f(d.storage)}`,
          `pill ${d.potrace && d.ffmpeg && d.storage ? 'ok' : 'warn'}`);
      } else setText('h-worker', `worker ${res.status} ${j.code || ''}: ${j.message || 'không có phản hồi'}`, 'pill err');
    } catch (e) { setText('h-worker', `worker: không gọi được (${e.message})`, 'pill err'); }
  }
  $('h-health').onclick = health;

  // ------------------------------------------------------------ mock order (shared by tabs 1 and 2)
  const ORDER_KEY = 'simOrder';
  const thumbs = { fp: null, sw: null }; // presigned URLs live ~5 min, kept in memory only
  let swBusy = false;
  const saveOrder = () => { try { sessionStorage.setItem(ORDER_KEY, JSON.stringify(order)); } catch { /* ignore */ } };
  const freshOrder = () => ({ orderId: uuid(), item1Id: uuid(), item2Id: uuid(), fpChosen: null, swDone: false });
  let order = (() => {
    try { const o = JSON.parse(sessionStorage.getItem(ORDER_KEY)); if (o && o.orderId) return { item1Id: uuid(), item2Id: uuid(), ...o }; } catch { /* ignore */ }
    return freshOrder();
  })();
  saveOrder();

  // Garage layout: personalization/test-ui/{fingerprint,soundwave}/{orderId}/...
  const fpBase = (orderId) => `${SIM_PREFIX}fingerprint/${orderId}/`;
  const swBase = (orderId) => `${SIM_PREFIX}soundwave/${orderId}/`;
  const EXT = { 'audio/wav': 'wav', 'audio/mpeg': 'mp3', 'audio/mp4': 'm4a', 'audio/aac': 'aac' };
  /** Delete every object under a test-ui prefix (server enforces the prefix). Returns the count. */
  async function deletePrefix(prefix) {
    const res = await fetch(`/dev/delete-prefix?prefix=${encodeURIComponent(prefix)}`, { method: 'POST' });
    const j = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(`${res.status} ${j.message || j.error || ''}`);
    return j.deleted || 0;
  }

  function itemState(no) {
    if (no === 1) {
      if (order.fpChosen) return `Đã chọn vân tay lần ${order.fpChosen}`;
      return sess && sess.attempts.some((a) => a.worker.state === 'running') ? 'Đang xử lý' : 'Chưa có nguồn';
    }
    if (order.swDone) return 'Đã có WAVECODE';
    return swBusy ? 'Đang xử lý' : 'Chưa có nguồn';
  }
  function renderOrder() {
    const item = (no, pkg, tpl, thumb) => el('div', { class: 'oitem' },
      el('div', {}, el('b', {}, `Item ${no}`), ` - gói ${pkg} - template `, el('code', {}, tpl)),
      el('div', {}, 'Trạng thái cá nhân hóa: ', el('span', { class: `badge ${itemState(no).startsWith('Đã') ? 'ok' : itemState(no) === 'Đang xử lý' ? 'warn' : ''}` }, itemState(no))),
      thumb ? el('img', { src: thumb, alt: '', class: 'checker' }) : '');
    $('order-body').replaceChildren(
      el('div', {}, 'orderId ', el('code', {}, order.orderId), ' - khách: ', el('b', {}, 'Khách test'),
        ' - trạng thái: ', el('b', {}, 'Đã thanh toán P1'), ' - consent: ', el('b', {}, 'Đã đồng ý (mô phỏng)')),
      el('div', { class: 'order-items' },
        item(1, 'FINGERPRINT', 'SIGNET_001_FP', order.fpChosen ? thumbs.fp : null),
        item(2, 'SOUNDWAVE', 'BAR_001_SW', order.swDone ? thumbs.sw : null)));
  }
  $('order-clean').onclick = async () => {
    const msg = $('order-clean-msg'); msg.textContent = 'đang xóa...';
    try {
      const a = await deletePrefix(fpBase(order.orderId)); const b = await deletePrefix(swBase(order.orderId));
      msg.textContent = `đã xóa ${a + b} object (vân tay ${a}, soundwave ${b})`;
      tl('note', `Dọn dữ liệu test của đơn ${order.orderId}: ${a + b} object`);
    } catch (e) { msg.textContent = `lỗi: ${e.message}`; }
  };
  $('order-new').onclick = () => {
    order = freshOrder(); saveOrder(); thumbs.fp = null; thumbs.sw = null;
    sess = null; $('fp-cards').replaceChildren(); swResetResult(); if (window.FpDesign) window.FpDesign.reset();
    setText('s-msg', ''); renderOrder(); updatePanel(); tl('note', `Tạo đơn mới ${order.orderId}`);
  };

  // ------------------------------------------------------------ header: MQTT
  let mq = null;
  let devEdited = false;
  let lastHello = null; // {device, at}
  const devName = () => $('h-device').value.trim();
  $('h-device').addEventListener('input', () => { devEdited = true; });

  $('h-connect').onclick = () => {
    if (typeof mqtt === 'undefined') { log('Không tải được mqtt.js (cần Internet tới unpkg.com).', 'err'); return; }
    const pass = $('h-pass').value;
    if (!pass) { log('Nhập mật khẩu MQTT, không được lưu lại.', 'err'); return; }
    mq = mqtt.connect($('h-url').value.trim(), {
      username: $('h-user').value.trim(), password: pass,
      clientId: `memora-test-ui-${Math.random().toString(16).slice(2, 10)}`, // never memora-operations
      clean: true, reconnectPeriod: 3000, connectTimeout: 10000, protocolVersion: 4,
    });
    setText('h-mqtt-state', 'MQTT: đang kết nối...', 'pill warn');
    mq.on('connect', () => {
      setText('h-mqtt-state', 'MQTT: đã kết nối', 'pill ok');
      $('h-connect').disabled = true; $('h-disconnect').disabled = false;
      mq.subscribe(['memora/capture/+/attempts', 'memora/capture/+/hello'], { qos: 1 }, (err) => {
        if (err) log(`Subscribe lỗi: ${err.message}`, 'err'); else log('MQTT đã subscribe memora/capture/+/attempts, +/hello');
      });
    });
    mq.on('reconnect', () => setText('h-mqtt-state', 'MQTT: mất kết nối, đang thử lại...', 'pill warn'));
    mq.on('error', (e) => { setText('h-mqtt-state', `MQTT lỗi: ${e.message}`, 'pill err'); log(`MQTT lỗi: ${e.message}`, 'err'); });
    mq.on('close', () => { if (!mq || mq.disconnecting) setText('h-mqtt-state', 'MQTT: đã ngắt', 'pill'); });
    mq.on('message', (topic, buf) => {
      const parts = topic.split('/'); // memora/capture/{device}/{kind}
      const device = parts[2]; const kind = parts[3];
      let p; try { p = JSON.parse(new TextDecoder().decode(buf)); } catch { tl('note', `${topic}: JSON lỗi, bỏ qua`); return; }
      tl('in', `${kind} (${device})`, p);
      if (kind === 'hello') onHello(device);
      else if (kind === 'attempts') onAttemptMsg(device, p);
    });
  };
  $('h-disconnect').onclick = () => {
    if (mq) { mq.end(true); mq = null; }
    $('h-connect').disabled = false; $('h-disconnect').disabled = true; setText('h-mqtt-state', 'MQTT: đã ngắt', 'pill');
  };
  /** Publish on memora/capture/{device}/{kind}, QoS 1, retain false. */
  function mqPublish(device, kind, payload) {
    if (!mq || !mq.connected) { log('Chưa kết nối MQTT.', 'err'); return false; }
    mq.publish(`memora/capture/${device}/${kind}`, JSON.stringify(payload), { qos: 1, retain: false });
    tl('out', `${kind} (${device})`, payload);
    return true;
  }
  function updateDevState() {
    if (lastHello && lastHello.device === devName()) {
      setText('h-dev-state', `online - hello lúc ${hhmmss(lastHello.at)} (${Math.round((Date.now() - lastHello.at) / 1000)} s trước)`, 'pill ok');
    } else setText('h-dev-state', 'chưa thấy hello', 'pill');
  }

  // ------------------------------------------------------------ capture session (page = Operations)
  /** @type {null | {id:string, device:string, expiresAt:number, max:number, used:number, status:string, waiting:boolean, attempts:object[], byNonce:Map}} */
  let sess = null;
  const open = () => sess && sess.status === 'IN_PROGRESS';
  const pending = () => sess.attempts.some((a) => a.state === 'PENDING_REVIEW');
  const startPayload = () => ({ type: 'start', sessionId: sess.id, expiresAt: new Date(sess.expiresAt).toISOString(), maxAttempts: sess.max });
  const threshold = () => { const v = Number($('fp-threshold').value); return Number.isFinite(v) ? v : 50; };

  function onHello(device) {
    lastHello = { device, at: Date.now() };
    if (!open() && !devEdited && devName() === DEFAULT_DEVICE && device !== devName()) $('h-device').value = device;
    updateDevState();
    if (open() && sess.device === device && !checkExpiry()) {
      tl('note', 'hello trong phiên đang mở: gửi lại start');
      mqPublish(device, 'commands', startPayload());
    }
  }

  $('s-create').onclick = () => {
    const device = devName();
    if (!/^[A-Za-z0-9_-]{1,80}$/.test(device)) { setText('s-msg', 'Mã thiết bị không hợp lệ ([A-Za-z0-9_-], 1..80).'); return; }
    if (!mq || !mq.connected) { setText('s-msg', 'Chưa kết nối MQTT (thanh trên cùng).'); return; }
    if (open()) { setText('s-msg', 'Phiên hiện tại còn mở: hãy chọn một lần hoặc kết thúc trước.'); return; }
    sess = { id: uuid(), device, expiresAt: Date.now() + TTL_MS, max: MAX_ATTEMPTS, used: 0, status: 'IN_PROGRESS', waiting: true, attempts: [], byNonce: new Map() };
    order.fpChosen = null; thumbs.fp = null; saveOrder();
    $('fp-cards').replaceChildren(); setText('s-msg', ''); if (window.FpDesign) window.FpDesign.reset();
    tl('staff', `Tạo phiên ${sess.id} cho ${device}, hết hạn ${new Date(sess.expiresAt).toISOString()}`);
    mqPublish(device, 'commands', startPayload());
    renderOrder(); updatePanel();
  };

  function updatePanel() {
    if (!sess) {
      setText('s-status', 'chưa có phiên', 'badge'); setText('s-countdown', '--:--'); setText('s-used', `0/${MAX_ATTEMPTS}`); setText('s-id', '-');
      $('s-next').disabled = true; $('s-cancel').disabled = true; $('s-create').disabled = false; return;
    }
    const cls = { IN_PROGRESS: 'info', COMPLETED: 'ok', CANCELLED: 'warn', FAILED: 'err', EXPIRED: 'err' }[sess.status] || '';
    setText('s-status', sess.status, `badge ${cls}`); setText('s-id', sess.id); setText('s-used', `${sess.used}/${sess.max}`);
    $('s-create').disabled = open();
    $('s-next').disabled = !open() || sess.used >= sess.max || sess.waiting;
    $('s-cancel').disabled = !open();
    for (const a of sess.attempts) updateCard(a);
  }
  function tickCountdown() {
    if (!sess) return;
    checkExpiry();
    setText('s-countdown', open() ? `${String(Math.floor(Math.max(0, sess.expiresAt - Date.now()) / 60000)).padStart(2, '0')}:${String(Math.floor((Math.max(0, sess.expiresAt - Date.now()) % 60000) / 1000)).padStart(2, '0')}` : '--:--');
  }
  /** Auto-expire at expiresAt (UI only; Operations sends no command for expiry). Returns true if expired. */
  function checkExpiry() {
    if (!open() || Date.now() < sess.expiresAt) return !open() && sess.status === 'EXPIRED';
    sess.status = 'EXPIRED';
    for (const a of sess.attempts) if (a.state === 'PENDING_REVIEW') { a.state = 'REJECTED'; a.reason = 'EXPIRED'; cleanupAttempt(a); }
    tl('note', 'Phiên hết hạn (EXPIRED), các lần chờ duyệt chuyển REJECTED/EXPIRED');
    updatePanel(); renderOrder();
    return true;
  }
  setInterval(() => { tickCountdown(); updateDevState(); }, 1000);

  /** Result message for an attempt, per contract sections 4.3 / 4.4. */
  function resultOf(att) {
    const base = { sessionId: sess.id, nonce: att.nonce, ok: true, attemptNo: att.no };
    const tail = { remainingAttempts: Math.max(0, sess.max - sess.used), sessionStatus: sess.status };
    if (att.state === 'REJECTED') {
      return { ...base, result: 'REJECTED', ...(att.reason === 'CAPTURE_FAILED' || att.reason === 'STAFF_REJECTED' ? { reason: att.reason } : {}), ...tail };
    }
    return { ...base, result: 'PENDING_REVIEW', suggestion: att.suggestion, ...tail };
  }

  /** Decode and validate the BMP per section 4.5. Returns {bytes} or {error}. */
  function decodeBmp(b64) {
    if (b64.length % 4 || !/^[A-Za-z0-9+/]*={0,2}$/.test(b64)) return { error: 'INVALID_MESSAGE' };
    let bin; try { bin = atob(b64); } catch { return { error: 'INVALID_MESSAGE' }; }
    if (bin.length > 180000) return { error: 'IMAGE_TOO_LARGE' };
    const bytes = new Uint8Array(bin.length);
    for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
    if (bytes.length < 14 || bytes[0] !== 0x42 || bytes[1] !== 0x4d) return { error: 'INVALID_MESSAGE' };
    if (new DataView(bytes.buffer).getUint32(2, true) !== bytes.length) return { error: 'INVALID_MESSAGE' };
    return { bytes };
  }

  function onAttemptMsg(device, p) {
    if (device !== devName() && !(sess && sess.device === device)) { tl('note', `attempts của thiết bị khác (${device}), bỏ qua`); return; }
    if (!p || !UUID_RE.test(String(p.sessionId || '')) || !NONCE_RE.test(String(p.nonce || ''))) {
      tl('note', 'attempts thiếu sessionId/nonce hợp lệ: bỏ qua, không trả lời (hợp đồng mục 4.2)'); return;
    }
    const fail = (error) => mqPublish(device, 'results', { sessionId: p.sessionId, nonce: p.nonce, ok: false, error });
    if (!sess || sess.id !== p.sessionId) { fail('SESSION_NOT_FOUND'); return; }
    if (sess.device !== device) { fail('DEVICE_MISMATCH'); return; }
    const dup = sess.byNonce.get(p.nonce);
    if (dup) { tl('note', `nonce lặp (lần ${dup.no}): gửi lại kết quả đã lưu`); mqPublish(device, 'results', resultOf(dup)); return; }
    checkExpiry();
    if (!open()) {
      fail({ EXPIRED: 'SESSION_EXPIRED', COMPLETED: 'ATTEMPT_ALREADY_ACCEPTED', FAILED: 'MAX_ATTEMPTS_REACHED' }[sess.status] || 'SESSION_NOT_OPEN'); return;
    }
    if (sess.used >= sess.max) { fail('MAX_ATTEMPTS_REACHED'); return; }
    const hasImage = typeof p.imageBase64 === 'string' && p.imageBase64.length > 0;
    let bytes = null;
    if (hasImage) {
      const q = p.qualityScore;
      if (typeof q !== 'number' || !Number.isFinite(q) || q < 0 || q > 1000) { fail('INVALID_MESSAGE'); return; }
      const d = decodeBmp(p.imageBase64);
      if (d.error) { fail(d.error); return; }
      bytes = d.bytes;
    }
    const att = {
      no: sess.used + 1, nonce: p.nonce, sessionId: sess.id, score: hasImage ? p.qualityScore : null, hasImage,
      state: hasImage ? 'PENDING_REVIEW' : 'REJECTED', reason: hasImage ? '' : 'CAPTURE_FAILED',
      suggestion: hasImage ? (p.qualityScore >= threshold() ? 'PASS' : 'LOW_QUALITY') : '',
      bmp: bytes ? new Blob([bytes], { type: 'image/bmp' }) : null, worker: { state: hasImage ? 'idle' : 'none', msg: '' },
      orderId: order.orderId, item1Id: order.item1Id, cleaned: false, selected: null,
    };
    if (hasImage) {
      att.rawKey = `${fpBase(att.orderId)}sessions/${sess.id}/attempt-${att.no}.bmp`;
      att.previewPrefix = `${fpBase(att.orderId)}sessions/${sess.id}/preview-${att.no}/`;
    }
    sess.used += 1; sess.byNonce.set(att.nonce, att); sess.attempts.push(att);
    if (!hasImage) {
      if (sess.used >= sess.max && !pending()) sess.status = 'FAILED';
      sess.waiting = sess.status === 'IN_PROGRESS' && sess.used < sess.max; // device goes back to CAPTURING
    } else sess.waiting = false;
    mqPublish(device, 'results', resultOf(att));
    if (sess.status === 'FAILED') mqPublish(device, 'commands', { type: 'end', sessionId: sess.id, status: 'FAILED' });
    buildCard(att); updatePanel(); renderOrder();
    if (hasImage) runAttempt(att);
  }

  // ------------------------------------------------------------ worker pipeline per attempt
  async function runAttempt(att) {
    att.worker = { state: 'running', msg: '' }; updateCard(att); renderOrder();
    try {
      tl('worker', `lần ${att.no}: upload BMP`);
      const up = await uploadRaw(att.bmp, 'image/bmp', att.rawKey);
      const sourceId = uuid();
      const outputPrefix = att.previewPrefix;
      const context = { orderId: att.orderId, sessionId: att.sessionId, attemptNo: att.no };
      tl('worker', `lần ${att.no}: POST /worker/v1/fingerprint/process (preview)`, { sourceId, rawKey: up.key, outputPrefix, context });
      const r = await workerCall('/v1/fingerprint/process', { sourceId, rawKey: up.key, outputPrefix, context });
      if (!r.ok || !r.data) {
        att.worker = { state: 'error', msg: `${r.code || r.status}: ${r.message || ''}` };
        tl('worker', `lần ${att.no}: lỗi ${att.worker.msg}`);
      } else {
        att.data = r.data;
        const o = r.data.outputs || {};
        att.pngUrl = o.alphaPng ? await presign(o.alphaPng.key) : null;
        att.svgUrl = o.vectorSvg ? await presign(o.vectorSvg.key) : null;
        att.worker = { state: 'done', msg: '' };
        tl('worker', `lần ${att.no}: xong (${r.data.status}, quality ${r.data.quality ? r.data.quality.score : '?'})`);
      }
    } catch (e) { att.worker = { state: 'error', msg: e.message }; tl('worker', `lần ${att.no}: lỗi ${e.message}`); }
    // The attempt may have been rejected / expired / cancelled while the worker was still writing: clean again.
    if (att.cleaned) { att.cleaned = false; cleanupAttempt(att); }
    updateCard(att); renderOrder();
  }
  /** Production-like deletion of a non-selected attempt: its raw BMP and preview-{n}/ folder. */
  async function cleanupAttempt(att, previewOnly = false) {
    if (!att.hasImage || att.cleaned) return;
    if (!previewOnly) att.cleaned = true;
    updateCard(att);
    try {
      const n = (previewOnly ? 0 : await deletePrefix(att.rawKey)) + await deletePrefix(att.previewPrefix);
      tl('worker', `lần ${att.no}: đã xóa ${previewOnly ? 'preview' : 'raw + preview'} (${n} object)`);
    } catch (e) { tl('worker', `lần ${att.no}: xóa lỗi ${e.message}`); }
  }

  // ------------------------------------------------------------ attempt cards
  function buildCard(att) {
    const r = att.refs = {};
    r.badge = el('span', { class: 'badge' });
    r.score = el('div', { class: 'muted' });
    r.worker = el('div', {});
    r.paths = el('div', { class: 'path' });
    r.canvas = el('canvas', { width: 240, height: 240, hidden: true });
    r.svg = el('img', { class: 'checker', alt: '', hidden: true });
    r.choose = el('button', { onclick: () => choose(att) }, 'Chọn lần này');
    r.reject = el('button', { class: 'danger', onclick: () => reject(att) }, 'Từ chối');
    const raw = att.bmp ? el('img', { class: 'raw', alt: 'BMP gốc', src: URL.createObjectURL(att.bmp) }) : el('div', { class: 'muted' }, '(không có ảnh)');
    att.refs.root = el('div', { class: 'acard' },
      el('div', {}, el('b', {}, `Lần ${att.no}`), ' ', r.badge), r.score, el('div', { class: 'muted' }, 'BMP gốc'), raw,
      el('div', { class: 'muted' }, 'alpha PNG + hull'), r.canvas, el('div', { class: 'muted' }, 'SVG'), r.svg, r.worker, r.paths,
      el('div', { class: 'row' }, r.choose, r.reject));
    $('fp-cards').append(att.refs.root);
    updateCard(att);
  }
  function drawHull(att) {
    const c = att.refs.canvas;
    const img = new Image();
    img.onload = () => {
      const s = Math.min(1, 240 / Math.max(img.width, img.height));
      c.width = Math.round(img.width * s); c.height = Math.round(img.height * s); c.hidden = false;
      const g = c.getContext('2d'); g.drawImage(img, 0, 0, c.width, c.height);
      const hull = (att.data && att.data.artwork && att.data.artwork.hull) || [];
      if (hull.length) { // hull is normalised 0..1, origin top-left
        g.strokeStyle = 'red'; g.lineWidth = 2; g.beginPath();
        hull.forEach(([x, y], i) => (i ? g.lineTo(x * c.width, y * c.height) : g.moveTo(x * c.width, y * c.height)));
        g.closePath(); g.stroke();
      }
    };
    img.src = att.pngUrl;
  }
  function updateCard(att) {
    const r = att.refs; if (!r) return;
    const cls = { PENDING_REVIEW: 'warn', REJECTED: 'err', ACCEPTED: 'ok', NOT_SELECTED: '' }[att.state] || '';
    r.badge.textContent = att.state + (att.state === 'REJECTED' && att.reason ? ` (${att.reason})` : ''); r.badge.className = `badge ${cls}`;
    r.score.textContent = att.hasImage ? `quality ${att.score} - gợi ý ${att.suggestion}` : 'Không có ảnh (CAPTURE_FAILED)';
    const w = att.worker;
    r.worker.textContent = { none: '', idle: 'worker: chờ', running: 'worker: đang xử lý...', done: 'worker: xong', error: `worker: lỗi ${w.msg}` }[w.state] || '';
    r.worker.className = w.state === 'error' ? 'err' : w.state === 'done' ? 'ok' : 'muted';
    if (w.state === 'done' && !att.drawn) {
      att.drawn = true;
      if (att.pngUrl) drawHull(att);
      if (att.svgUrl) { r.svg.src = att.svgUrl; r.svg.hidden = false; }
    }
    r.paths.replaceChildren(...(att.hasImage ? [
      el('div', {}, `raw: ${att.rawKey}${att.cleaned ? ' (đã xóa)' : ''}`),
      el('div', {}, `preview: ${att.previewPrefix}${att.cleaned || att.previewDeleted ? ' (đã xóa)' : ''}`),
      ...(att.selected ? [el('div', {}, `selected: ${att.selected.prefix}`)] : []),
    ] : []));
    const act = open() && att.state === 'PENDING_REVIEW';
    r.choose.disabled = !(act && w.state === 'done' && !sess.choosing);
    r.reject.disabled = !act;
  }

  // ------------------------------------------------------------ staff actions
  function reject(att) {
    if (!open() || att.state !== 'PENDING_REVIEW') return;
    att.state = 'REJECTED'; att.reason = 'STAFF_REJECTED';
    const left = sess.max - sess.used;
    if (left <= 0 && !pending()) sess.status = 'FAILED';
    sess.waiting = sess.status === 'IN_PROGRESS' && left > 0; // device goes back to CAPTURING
    tl('staff', `Từ chối lần ${att.no} (STAFF_REJECTED)`);
    cleanupAttempt(att);
    mqPublish(sess.device, 'results', resultOf(att));
    if (sess.status === 'FAILED') mqPublish(sess.device, 'commands', { type: 'end', sessionId: sess.id, status: 'FAILED' });
    updatePanel(); renderOrder();
  }
  async function choose(att) {
    if (!open() || att.state !== 'PENDING_REVIEW' || sess.choosing) return;
    // Like production: only the chosen attempt is processed into selected/{sourceId}/.
    sess.choosing = true; updatePanel();
    const sourceId = uuid();
    const outputPrefix = `${fpBase(att.orderId)}selected/${sourceId}/`;
    const context = { orderId: att.orderId, sessionId: att.sessionId, attemptNo: att.no, ringItemIds: [att.item1Id] };
    let r;
    try {
      tl('worker', `lần ${att.no}: POST /worker/v1/fingerprint/process (selected)`, { sourceId, rawKey: att.rawKey, outputPrefix, context });
      r = await workerCall('/v1/fingerprint/process', { sourceId, rawKey: att.rawKey, outputPrefix, context });
    } catch (e) { r = { ok: false, message: e.message }; }
    sess.choosing = false;
    if (!r.ok || !r.data) {
      setText('s-msg', `Xử lý bản chọn lỗi: ${r.code || r.status || ''} ${r.message || ''}`);
      tl('worker', `lần ${att.no}: chọn lỗi ${r.code || r.status || ''} ${r.message || ''}`);
      updatePanel(); return;
    }
    if (!open() || att.state !== 'PENDING_REVIEW') { // expired or cancelled meanwhile
      tl('note', `Phiên đã đổi trạng thái khi đang xử lý lần ${att.no}: bỏ bản chọn`);
      deletePrefix(outputPrefix).catch(() => {}); updatePanel(); return;
    }
    att.state = 'ACCEPTED';
    for (const a of sess.attempts) if (a !== att && a.state === 'PENDING_REVIEW') { a.state = 'NOT_SELECTED'; cleanupAttempt(a); }
    sess.status = 'COMPLETED'; sess.waiting = false;
    order.fpChosen = att.no; saveOrder();
    const o = r.data.outputs || {};
    att.selected = { sourceId, prefix: outputPrefix, svgKey: o.vectorSvg && o.vectorSvg.key, pngKey: o.alphaPng && o.alphaPng.key };
    tl('staff', `Chọn lần ${att.no}, nguồn ${sourceId}`);
    mqPublish(sess.device, 'commands', { type: 'end', sessionId: sess.id, status: 'COMPLETED', acceptedAttemptNo: att.no });
    att.previewDeleted = true; cleanupAttempt(att, true); // preview of the chosen attempt is no longer needed
    if (window.FpDesign) window.FpDesign.open({ outputPrefix, sourceId, context });
    thumbs.fp = att.selected.svgKey ? await presign(att.selected.svgKey) : att.pngUrl;
    updatePanel(); renderOrder();
  }
  $('s-next').onclick = () => {
    if (!open() || sess.used >= sess.max || sess.waiting) return;
    sess.waiting = true;
    tl('staff', `Yêu cầu quét lần ${sess.used + 1}`);
    mqPublish(sess.device, 'commands', { type: 'next', sessionId: sess.id, attemptNo: sess.used + 1 });
    updatePanel();
  };
  $('s-cancel').onclick = () => {
    if (!open()) return;
    for (const a of sess.attempts) if (a.state === 'PENDING_REVIEW') { a.state = 'NOT_SELECTED'; cleanupAttempt(a); }
    sess.status = 'CANCELLED'; sess.waiting = false;
    tl('staff', 'Kết thúc không chọn (CANCELLED)');
    mqPublish(sess.device, 'commands', { type: 'end', sessionId: sess.id, status: 'CANCELLED' });
    updatePanel(); renderOrder();
  };

  // ============================================================ tab 2: soundwave
  const SW2 = { blob: null, ct: '', durationMs: 0, peaks: null, startMs: 0, url: '', name: '', sourceId: '', outId: '', data: null, rec: null, playT: 0, style: 'CLASSIC', styles: [], accepted: null, resegs: 0 };
  const MAX_RESEGS = 10;
  const REF = new Set([0, 11, 22]);
  const CHK = new Set([10, 21]);

  function swResetResult() {
    $('sw2-result').hidden = true; $('sw2-render').hidden = true;
    SW2.styles = []; SW2.accepted = null; SW2.data = null; SW2.resegs = 0; refreshStyleUi();
  }
  function swValidate() {
    const d = SW2.durationMs;
    const ok = !!SW2.blob && d >= 3000 && d <= 30000;
    $('sw2-run').disabled = !ok || swBusy; $('sw2-play-sel').disabled = !ok;
    if (SW2.blob && !ok) setText('sw2-state', `Thời lượng ${d} ms ngoài khoảng 3000..30000 ms.`, 'err');
    else if (ok) setText('sw2-state', '', 'muted');
    return ok;
  }
  const maxStart = () => Math.max(0, Math.min(27000, SW2.durationMs - 3000));
  function setStart(ms) {
    if (SW2.accepted) return;
    SW2.startMs = Math.round(Math.max(0, Math.min(maxStart(), Number(ms) || 0)));
    $('sw2-start').value = SW2.startMs; $('sw2-start').max = maxStart(); drawWave();
  }
  $('sw2-start').addEventListener('input', () => setStart($('sw2-start').value));

  function drawWave() {
    const c = $('sw2-wave'); if (!SW2.peaks) return;
    const g = c.getContext('2d'); const css = getComputedStyle(document.documentElement);
    const accent = css.getPropertyValue('--accent').trim() || '#2f6fed'; const muted = css.getPropertyValue('--muted').trim() || '#888';
    g.clearRect(0, 0, c.width, c.height);
    g.fillStyle = muted;
    SW2.peaks.forEach((v, i) => { const h = Math.max(1, v * c.height * 0.95); g.fillRect(i, (c.height - h) / 2, 1, h); });
    const x0 = (SW2.startMs / SW2.durationMs) * c.width; const w = (3000 / SW2.durationMs) * c.width;
    g.globalAlpha = 0.25; g.fillStyle = accent; g.fillRect(x0, 0, w, c.height); g.globalAlpha = 1;
    g.strokeStyle = accent; g.lineWidth = 2; g.strokeRect(x0 + 1, 1, Math.max(2, w - 2), c.height - 2);
  }
  const wave = $('sw2-wave');
  let dragging = false;
  const dragTo = (ev) => {
    const rect = wave.getBoundingClientRect();
    setStart(((ev.clientX - rect.left) / rect.width) * SW2.durationMs - 1500);
  };
  wave.addEventListener('pointerdown', (ev) => { if (!SW2.peaks || SW2.accepted) return; dragging = true; wave.setPointerCapture(ev.pointerId); dragTo(ev); });
  wave.addEventListener('pointermove', (ev) => { if (dragging) dragTo(ev); });
  wave.addEventListener('pointerup', () => { dragging = false; });
  wave.addEventListener('pointercancel', () => { dragging = false; });

  /** Decode, convert unsupported types to mono 16-bit WAV, compute peaks and wire the player. */
  async function swLoad(blob, name, forceWav) {
    swResetResult(); setText('sw2-state', 'đang giải mã...', 'muted');
    const ctx = new (window.AudioContext || window.webkitAudioContext)();
    let decoded;
    try { decoded = await ctx.decodeAudioData(await blob.arrayBuffer()); } catch (e) {
      setText('sw2-state', `Không giải mã được audio: ${e.message || 'định dạng không hỗ trợ'}`, 'err'); return;
    } finally { ctx.close(); }
    let ct = forceWav ? '' : swContentType({ type: blob.type, name });
    let out = blob;
    if (!ct) { out = encodeWav(decoded); ct = 'audio/wav'; log(`Đã đổi audio sang WAV (${out.size} bytes).`, 'ok'); }
    Object.assign(SW2, { blob: out, ct, name, durationMs: Math.round(decoded.duration * 1000) });
    const cols = $('sw2-wave').width; const peaks = new Float32Array(cols);
    const chans = Array.from({ length: Math.min(2, decoded.numberOfChannels) }, (_, i) => decoded.getChannelData(i));
    const per = decoded.length / cols;
    for (let i = 0; i < cols; i++) {
      let m = 0; const e = Math.min(decoded.length, Math.floor((i + 1) * per));
      for (const ch of chans) for (let j = Math.floor(i * per); j < e; j++) { const v = Math.abs(ch[j]); if (v > m) m = v; }
      peaks[i] = m;
    }
    SW2.peaks = peaks;
    if (SW2.url) URL.revokeObjectURL(SW2.url);
    SW2.url = URL.createObjectURL(out);
    $('sw2-audio').src = SW2.url; $('sw2-audio').hidden = false; $('sw2-wave').hidden = false;
    setText('sw2-meta', `${name || 'audio'} - ${ct} - ${out.size} bytes - ${SW2.durationMs} ms`, 'muted');
    setStart(0); swValidate();
  }
  $('sw2-file').onchange = (e) => { const f = e.target.files[0]; if (f) swLoad(f, f.name, false); };

  $('sw2-rec').onclick = async () => {
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const chunks = []; const mr = new MediaRecorder(stream); const t0 = Date.now();
      const timer = setInterval(() => {
        const s = (Date.now() - t0) / 1000; setText('sw2-timer', `đang ghi ${s.toFixed(1)} / 30 s`);
        if (s >= 30 && mr.state === 'recording') mr.stop();
      }, 200);
      SW2.rec = mr;
      mr.ondataavailable = (e) => chunks.push(e.data);
      mr.onstop = () => {
        clearInterval(timer); stream.getTracks().forEach((t) => t.stop());
        $('sw2-rec').disabled = false; $('sw2-stop').disabled = true; setText('sw2-timer', 'đã ghi xong');
        swLoad(new Blob(chunks, { type: mr.mimeType || 'audio/webm' }), 'recording.wav', true);
      };
      mr.start(); $('sw2-rec').disabled = true; $('sw2-stop').disabled = false;
    } catch (e) { setText('sw2-state', `Mic lỗi: ${e.message}`, 'err'); }
  };
  $('sw2-stop').onclick = () => { if (SW2.rec && SW2.rec.state === 'recording') SW2.rec.stop(); };

  $('sw2-play-sel').onclick = () => {
    const a = $('sw2-audio'); clearTimeout(SW2.playT);
    a.currentTime = SW2.startMs / 1000; a.play().catch(() => {});
    SW2.playT = setTimeout(() => a.pause(), 3000);
  };

  function drawWavecode(box, bars) {
    box.replaceChildren(...bars.map((v, i) => el('div', { class: `wbar ${REF.has(i) ? 'ref' : CHK.has(i) ? 'chk' : ''}`, title: `bar ${i}: level ${v}` },
      el('i', { style: `height:${6 + v * 8}px` }), el('span', {}, v))));
  }

  $('sw2-run').onclick = async () => {
    if (!swValidate()) return;
    swBusy = true; renderOrder(); $('sw2-run').disabled = true; swResetResult();
    try {
      setText('sw2-state', 'đang upload...', 'muted');
      SW2.sourceId = uuid(); SW2.orderId = order.orderId;
      const rawKey = `${swBase(SW2.orderId)}uploads/${SW2.sourceId}/raw.${EXT[SW2.ct] || 'wav'}`;
      const up = await uploadRaw(SW2.blob, SW2.ct, rawKey);
      SW2.rawKey = up.key; SW2.outPrefix = `${swBase(SW2.orderId)}selected/${SW2.sourceId}/`;
      SW2.context = { orderId: SW2.orderId, ringItemIds: [order.item2Id] };
      const body = {
        sourceId: SW2.sourceId, rawKey: up.key, outputPrefix: SW2.outPrefix, contentType: SW2.ct,
        declaredDurationMs: SW2.durationMs, segmentStartMs: SW2.startMs, segmentDurationMs: 3000, style: SW2.style, context: SW2.context,
      };
      setText('sw2-state', 'đang xử lý...', 'muted');
      const r = await workerCall('/v1/soundwave/process', body);
      if (!r.ok || !r.data) { setText('sw2-state', `${r.code || r.status}: ${r.message || ''}`, 'err'); return; }
      SW2.data = r.data; SW2.levels = r.data.wavecode.levels;
      SW2.styles = Object.keys(r.data.wavecode.styles || {}); SW2.accepted = null; refreshStyleUi(); setText('sw2-state', 'xong', 'ok');
      await showSwResult(r.data);
      order.swDone = true; saveOrder();
    } catch (e) { setText('sw2-state', `lỗi: ${e.message}`, 'err'); } finally { swBusy = false; renderOrder(); swValidate(); refreshStyleUi(); }
  };

  // ------------------------------------------------------------ re-select the 3 s segment
  $('sw2-reseg').onclick = async () => {
    if (!SW2.data || SW2.accepted || SW2.resegs >= MAX_RESEGS || swBusy || !swValidate()) return;
    swBusy = true; renderOrder(); refreshStyleUi(); $('sw2-run').disabled = true;
    try {
      setText('sw2-state', 'đang xóa kết quả cũ...', 'muted');
      await deletePrefix(SW2.outPrefix);
      SW2.styles = []; SW2.data = null; $('sw2-render').hidden = true; refreshStyleUi();
      const body = {
        sourceId: SW2.sourceId, rawKey: SW2.rawKey, outputPrefix: SW2.outPrefix, contentType: SW2.ct,
        declaredDurationMs: SW2.durationMs, segmentStartMs: SW2.startMs, segmentDurationMs: 3000,
        style: SW2.style, context: SW2.context,
      };
      setText('sw2-state', 'đang xử lý lại (không upload lại)...', 'muted');
      const r = await workerCall('/v1/soundwave/process', body);
      if (!r.ok || !r.data) {
        $('sw2-result').hidden = true;
        setText('sw2-state', `${r.code || r.status}: ${r.message || ''} (kết quả cũ đã bị xóa)`, 'err'); return;
      }
      SW2.resegs += 1;
      SW2.data = r.data; SW2.levels = r.data.wavecode.levels;
      SW2.styles = Object.keys(r.data.wavecode.styles || {});
      setText('sw2-state', 'xong', 'ok');
      setText('sw2-style-msg', `đã chọn lại đoạn bắt đầu ${SW2.startMs} ms, các kiểu đã tạo được đặt lại`, 'muted');
      await showSwResult(r.data);
    } catch (e) { setText('sw2-state', `lỗi: ${e.message}`, 'err'); } finally { swBusy = false; renderOrder(); swValidate(); refreshStyleUi(); }
  };

  // ------------------------------------------------------------ resume from Garage
  const objFetch = (key) => fetch(`/dev/object?key=${encodeURIComponent(key)}&t=${Date.now()}`);
  $('sw2-resume').onclick = async () => {
    const box = $('sw2-resume-list');
    box.replaceChildren(el('span', { class: 'muted' }, 'đang liệt kê Garage...'));
    try {
      const prefix = `${swBase(order.orderId)}selected/`;
      const res = await fetch(`/dev/list?prefix=${encodeURIComponent(prefix)}`);
      const j = await res.json();
      if (!res.ok) throw new Error(`${res.status} ${j.message || j.error || ''}`);
      const manifests = j.keys.filter((k) => k.key.endsWith('/manifest.json'))
        .sort((a, b) => b.lastModified.localeCompare(a.lastModified));
      if (!manifests.length) { box.replaceChildren(el('span', { class: 'muted' }, `không có manifest nào dưới ${prefix}`)); return; }
      const docs = await Promise.all(manifests.map(async (m) => {
        try { return { m, doc: await (await objFetch(m.key)).json() }; } catch { return { m, doc: null }; }
      }));
      box.replaceChildren(el('table', {}, el('tbody', {}, docs.map(({ m, doc }) => {
        const wc = (doc && doc.wavecode) || {}; const rq = (doc && doc.request) || {};
        return el('tr', {},
          el('td', {}, m.lastModified.replace('T', ' ').slice(0, 19)),
          el('td', {}, rq.sourceId || m.key.split('/').slice(-2)[0]),
          el('td', {}, `${wc.style || '?'}${doc && doc.accepted ? ' (đã chấp nhận)' : ''}`),
          el('td', {}, `start ${rq.segmentStartMs ?? '?'} ms`),
          el('td', {}, `dài ${doc && doc.measuredDurationMs != null ? doc.measuredDurationMs : '?'} ms`),
          el('td', {}, el('button', { class: 'secondary', disabled: doc ? undefined : '', onclick: () => resumeFrom(m.key, doc) }, 'Chọn')));
      }))));
    } catch (e) { box.replaceChildren(el('span', { class: 'err' }, `lỗi: ${e.message}`)); }
  };

  async function resumeFrom(manifestKey, doc) {
    const wc = doc.wavecode || {}; const rq = doc.request || {};
    const outPrefix = manifestKey.slice(0, manifestKey.length - 'manifest.json'.length);
    setText('sw2-state', 'đang dựng lại từ manifest...', 'muted');
    try {
      let audioLoaded = false;
      if (rq.rawKey) {
        const r = await objFetch(rq.rawKey);
        if (r.ok) { await swLoad(await r.blob(), rq.rawKey.split('/').pop(), false); audioLoaded = !!SW2.peaks; }
      }
      swResetResult();
      if (audioLoaded) setStart(rq.segmentStartMs || 0);
      Object.assign(SW2, {
        sourceId: rq.sourceId || outPrefix.split('/').slice(-2)[0], orderId: order.orderId, rawKey: rq.rawKey || '', outPrefix,
        context: doc.context || { orderId: order.orderId, ringItemIds: [order.item2Id] },
        style: wc.style || 'CLASSIC', styles: Object.keys(wc.styles || {}),
        accepted: doc.accepted ? (wc.style || null) : null, levels: wc.levels, resegs: 0,
      });
      if (rq.contentType) SW2.ct = rq.contentType;
      SW2.data = {
        measuredDurationMs: doc.measuredDurationMs, processorVersion: doc.processorVersion,
        outputs: { ...(doc.outputs || {}) }, wavecode: { ...wc, nudgeCandidates: [] },
      };
      refreshStyleUi(); swValidate();
      await showSwResult(SW2.data);
      if (doc.outputs && doc.outputs.audioSegment) $('sw2-seg').src = (await presign(doc.outputs.audioSegment.key)) || '';
      $('sw2-seg').play && $('sw2-seg').play().catch(() => {});
      setText('sw2-state', `đã tải lại ${SW2.sourceId}${SW2.accepted ? ' (đã chấp nhận, khóa)' : ''}${audioLoaded ? '' : ' (không có raw: chỉ xem kết quả)'}`, 'ok');
      setText('sw2-style-msg', 'ứng viên nudge không có trong manifest nên danh sách trống; số lần chọn lại tính lại từ 0.', 'muted');
      $('sw2-resume-list').replaceChildren();
      order.swDone = true; saveOrder(); renderOrder();
    } catch (e) { setText('sw2-state', `lỗi: ${e.message}`, 'err'); }
  }

  async function showSwResult(d) {
    const wc = d.wavecode || { levels: [], checkBars: [], bars: [], nudgeCandidates: [] };
    $('sw2-result').hidden = false;
    setText('sw2-paths', `raw: ${SW2.rawKey} | selected: ${SW2.outPrefix} | ${(d.outputs.vectorSvg || {}).key || ''}`);
    setText('sw2-info', `measuredDurationMs ${d.measuredDurationMs} - processor ${d.processorVersion} - checkBars ${wc.checkBars.join(', ')} - style ${wc.style || 'CLASSIC'}`);
    setText('sw2-levels', `levels (${wc.levels.length}): ${wc.levels.join(' ')}`);
    drawWavecode($('sw2-bars'), wc.bars);
    const o = d.outputs || {};
    $('sw2-png').src = o.alphaPng ? (await presign(o.alphaPng.key)) || '' : '';
    const svg = o.vectorSvg ? await presign(o.vectorSvg.key) : null;
    $('sw2-svg').src = svg || ''; thumbs.sw = svg;
    SW2.svgKey = o.vectorSvg && o.vectorSvg.key; update3d();
    if (o.audioSegment) $('sw2-seg').src = (await presign(o.audioSegment.key)) || '';
    const cands = wc.nudgeCandidates || [];
    $('sw2-nudge').replaceChildren(cands.length ? el('table', {}, el('tbody', {}, cands.map((c, i) => el('tr', {},
      el('td', {}, `#${i + 1}`), el('td', {}, c.join(' ')),
      el('td', {}, el('button', { class: 'secondary', onclick: () => renderCandidate(c) }, 'Render mã này')))))) : el('span', { class: 'muted' }, '(không có ứng viên)'));
  }
  async function renderCandidate(levels, restyle) {
    setText('sw2-state', 'đang render lại...', 'muted');
    const r = await workerCall('/v1/soundwave/render-wavecode', { sourceId: SW2.sourceId, outputPrefix: SW2.outPrefix, levels, style: SW2.style, context: SW2.context });
    if (!r.ok || !r.data) { setText('sw2-state', `${r.code || r.status}: ${r.message || ''}`, 'err'); setText('sw2-style-msg', `render lỗi ${r.code || r.status}: ${r.message || ''}`, 'err'); return; }
    setText('sw2-state', 'render xong', 'ok');
    $('sw2-render').hidden = false;
    setText('sw2-rpaths', `selected (ghi đè): ${SW2.outPrefix}`);
    drawWavecode($('sw2-rbars'), (r.data.wavecode && r.data.wavecode.bars) || []);
    const o = r.data.outputs || {};
    const png = o.alphaPng ? objUrl(o.alphaPng.key) : '', svgU = o.vectorSvg ? objUrl(o.vectorSvg.key) : '';
    $('sw2-rpng').src = png; $('sw2-rsvg').src = svgU;
    if (restyle) { $('sw2-png').src = png; $('sw2-svg').src = svgU; drawWavecode($('sw2-bars'), (r.data.wavecode && r.data.wavecode.bars) || []); }
    SW2.styles = Object.keys((r.data.wavecode && r.data.wavecode.styles) || {});
    const used = (r.data.wavecode && r.data.wavecode.style) || SW2.style;
    setText('sw2-style-msg', r.data.reused ? `kiểu ${used}: lấy từ bản đã có (không vẽ lại)` : `đã vẽ kiểu ${used} (cùng levels, không xử lý lại audio)`, 'ok');
    setText('sw2-state', r.data.reused ? 'lấy từ bản đã có' : 'render xong', 'ok');
    refreshStyleUi();
    const svg = o.vectorSvg ? await presign(o.vectorSvg.key) : null;
    if (svg) { thumbs.sw = svg; renderOrder(); }
    SW2.levels = levels; SW2.svgKey = o.vectorSvg && o.vectorSvg.key; update3d();
  }

  // ------------------------------------------------------------ wavecode style presets
  let PRESETS = null;
  function svgDataUrl(svg) { return `data:image/svg+xml;charset=utf-8,${encodeURIComponent(svg)}`; }
  const objUrl = (key) => `/dev/object?key=${encodeURIComponent(key)}&t=${Date.now()}`;
  function paintStyleCards() {
    if (!PRESETS) return;
    $('sw2-styles').replaceChildren(...PRESETS.styles.map((st) => el('button', {
      class: 'stylecard', type: 'button', 'aria-pressed': String(st.code === SW2.style),
      ...(SW2.accepted && SW2.accepted !== st.code ? { disabled: '' } : {}),
      onclick: () => pickStyle(st.code),
    },
    el('strong', {}, `${st.name} (${st.code})`, SW2.styles.includes(st.code)
      ? el('span', { class: 'badge ok', style: 'margin-left:6px' }, SW2.accepted === st.code ? 'đã chấp nhận' : 'đã tạo') : ''),
    el('img', { src: svgDataUrl(st.sampleSvg), alt: `Mẫu ${st.name}` }),
    el('small', {}, st.description),
    el('small', {}, `decoder: ${st.decoder}`))));
  }
  async function pickStyle(code) {
    if (swBusy || code === SW2.style || (SW2.accepted && SW2.accepted !== code)) return;
    SW2.style = code; refreshStyleUi();
    if (SW2.data && SW2.levels) await renderCandidate(SW2.levels, true);
  }
  function refreshStyleUi() {
    paintStyleCards();
    $('sw2-start').disabled = !!SW2.accepted;
    $('sw2-reseg').disabled = !(SW2.data && !SW2.accepted && SW2.resegs < MAX_RESEGS && !swBusy);
    setText('sw2-reseg-count', `lần chọn lại: ${SW2.resegs}/${MAX_RESEGS}${SW2.accepted ? ' (đã chấp nhận, khóa)' : ''}`);
    $('sw2-accept').disabled = !(SW2.data && !SW2.accepted && SW2.styles.includes(SW2.style) && !swBusy);
  }
  $('sw2-accept').onclick = async () => {
    if (!SW2.data || SW2.accepted) return;
    const style = SW2.style;
    $('sw2-accept').disabled = true; setText('sw2-style-msg', 'đang chấp nhận...', 'muted');
    const r = await workerCall('/v1/soundwave/select-wavecode-style',
      { sourceId: SW2.sourceId, outputPrefix: SW2.outPrefix, style, context: SW2.context });
    if (!r.ok || !r.data) {
      setText('sw2-style-msg', `${r.code || r.status}: ${r.message || ''}`, 'err'); refreshStyleUi(); return;
    }
    SW2.accepted = style; SW2.styles = Object.keys(r.data.wavecode.styles || {});
    SW2.svgKey = r.data.outputs.vectorSvg.key;
    setText('sw2-paths', `raw: ${SW2.rawKey} | selected: ${SW2.outPrefix} | ${SW2.svgKey}`);
    setText('sw2-style-msg', `Đã chấp nhận kiểu ${style}, đã xóa các kiểu khác (${(r.data.deleted || []).length} file)`, 'ok');
    refreshStyleUi();
  };
  async function loadPresets() {
    $('sw2-styles').replaceChildren(el('span', { class: 'muted' }, 'đang tải kiểu...'));
    try {
      const res = await fetch('/worker/v1/soundwave/presets');
      const json = await res.json().catch(() => null);
      log(`GET /worker/v1/soundwave/presets -> ${res.status}`, res.ok ? 'ok' : 'err');
      if (!res.ok || !json || !json.data) {
        throw new Error(`${(json && json.code) || res.status}: ${(json && json.message) || 'không có dữ liệu'}`);
      }
      PRESETS = json.data;
      if (!PRESETS.styles.some((s) => s.code === SW2.style) || !SW2.data) SW2.style = PRESETS.defaultStyle || 'CLASSIC';
      refreshStyleUi();
    } catch (e) {
      PRESETS = null;
      $('sw2-styles').replaceChildren(el('div', { class: 'stylecard err' },
        el('strong', { class: 'err' }, 'Không tải được danh sách kiểu'), el('small', {}, e.message),
        el('small', {}, 'Worker chưa chạy hoặc sai secret? Vẫn có thể xử lý với CLASSIC.')));
    }
  }

  // ------------------------------------------------------------ 3D preview (BAR_001_SW)
  let viewer3d = null;
  async function update3d() {
    if (!viewer3d || !SW2.svgKey) return;
    try {
      const text = await (await fetch(objUrl(SW2.svgKey))).text();
      await viewer3d.setSvgText(text);
    } catch (e) { log(`3D: ${e.message}`, 'err'); }
  }
  $('sw2-3d-open').onclick = async () => {
    $('sw2-3d-open').disabled = true;
    try {
      viewer3d = await window.Ring3D.mount($('sw2-3d'), { model: 'BAR_001_SW', kind: 'WAVECODE' });
      await update3d();
    } catch (e) { viewer3d = null; $('sw2-3d').replaceChildren(el('span', { class: 'err' }, `3D lỗi: ${e.message}`)); }
  };

  // ------------------------------------------------------------ tab 1: fingerprint on SIGNET_001_FP
  let fpViewer = null;
  function fpAssetKey() {
    const pool = (sess && sess.attempts) || [];
    const chosen = pool.find((a) => a.selected && a.selected.pngKey);
    const latest = [...pool].reverse().find((a) => a.data && a.data.outputs && a.data.outputs.alphaPng);
    return (window.FpDesign && window.FpDesign.currentPngKey()) || (chosen ? chosen.selected.pngKey : latest && latest.data.outputs.alphaPng.key);
  }
  // Selected assets of the simulated order item, for the quick 3D panel.
  window.SimAssets = {
    swKey: () => SW2.svgKey || null,
    fpKey: () => fpAssetKey() || null,
    url: (key) => objUrl(key),
  };
  $('fp-3d-open').onclick = async () => {
    const key = fpAssetKey();
    if (!key) { setText('fp-3d-msg', 'chưa có fingerprint_alpha.png (quét và chờ worker xong trước).', 'err'); return; }
    setText('fp-3d-msg', 'đang dựng...', 'muted');
    try {
      const blob = await (await fetch(objUrl(key))).blob();
      if (!fpViewer) fpViewer = await window.Ring3D.mount($('fp-3d'), { model: 'SIGNET_001_FP', kind: 'FINGERPRINT' });
      await fpViewer.setImageUrl(URL.createObjectURL(blob));
    } catch (e) { fpViewer = null; $('fp-3d').replaceChildren(el('span', { class: 'err' }, `3D lỗi: ${e.message}`)); }
  };

  // ------------------------------------------------------------ boot
  if (window.FpDesign) window.FpDesign.configure({ selectedPrefix: () => `${fpBase(order.orderId)}selected/` });
  loadPresets();
  $('sw2-styles-reload').onclick = loadPresets;
  renderOrder(); updatePanel(); health();
})();
