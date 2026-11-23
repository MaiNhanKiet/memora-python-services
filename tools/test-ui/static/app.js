// MEMORA local test UI. Plain vanilla JS, no build. Never logs tokens or presigned URLs.
'use strict';

const $ = (id) => document.getElementById(id);
const API = '/api/v1';
const WORKER_TMP = 'personalization/test-ui/';

// ---------------------------------------------------------------- helpers
/** Tiny DOM builder: el('td', {class:'x'}, 'text' | node ...) - text is always escaped. */
function el(tag, attrs = {}, ...kids) {
  const n = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k.startsWith('on')) n.addEventListener(k.slice(2), v);
    else if (v !== undefined && v !== null && v !== false) n.setAttribute(k, v === true ? '' : v);
  }
  for (const k of kids.flat()) n.append(k instanceof Node ? k : String(k ?? ''));
  return n;
}
const pick = (o, ...names) => { for (const n of names) if (o && o[n] !== undefined) return o[n]; return undefined; };
const unwrap = (r) => (r.json && typeof r.json === 'object' && 'data' in r.json ? r.json.data : r.json);
const pretty = (v) => JSON.stringify(v, null, 2);
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const uuid = () => crypto.randomUUID();

const logEl = $('log');
/** Append a line to the global console (path only, never query strings or secrets). */
function log(msg, cls) {
  const line = `${new Date().toLocaleTimeString('en-GB')}  ${msg}\n`;
  logEl.append(cls ? el('span', { class: cls }, line) : line);
  while (logEl.childNodes.length > 600) logEl.firstChild.remove();
  logEl.scrollTop = logEl.scrollHeight;
}
$('log-clear').onclick = () => { logEl.textContent = ''; };

// ---------------------------------------------------------------- API client
const tokens = (() => {
  try { return JSON.parse(sessionStorage.getItem('tokens') || '{}'); } catch { return {}; }
})();
const users = {};
const saveTokens = () => { try { sessionStorage.setItem('tokens', JSON.stringify(tokens)); } catch { /* ignore */ } };

/**
 * Call the gateway through the proxy. slot = 'customer' | 'staff' | null.
 * Returns {status, ok, json, text, ms}; never throws on HTTP errors.
 */
async function api(method, path, { slot = null, body, headers = {} } = {}) {
  const h = { Accept: 'application/json', ...headers };
  if (slot) {
    h['X-Test-Slot'] = slot;
    if (tokens[slot]) h.Authorization = `Bearer ${tokens[slot]}`;
  }
  if (body !== undefined) h['Content-Type'] = 'application/json';
  const t0 = performance.now();
  let res;
  try {
    res = await fetch(path, { method, headers: h, body: body === undefined ? undefined : JSON.stringify(body) });
  } catch (e) {
    log(`${method} ${path.split('?')[0]} NETWORK ERROR ${e.message}`, 'err');
    return { status: 0, ok: false, json: { error: 'network', message: e.message }, text: '', ms: 0 };
  }
  const ms = Math.round(performance.now() - t0);
  const text = await res.text();
  let json = null;
  try { json = text ? JSON.parse(text) : null; } catch { /* not JSON */ }
  log(`${method} ${path.split('?')[0]} -> ${res.status} (${ms} ms)`, res.ok ? 'ok' : 'err');
  return { status: res.status, ok: res.ok, json, text, ms };
}
/** Show status + body JSON of a failed call in the console. */
function logFail(r) { if (!r.ok) log(`  lỗi ${r.status}: ${r.json ? JSON.stringify(r.json) : r.text.slice(0, 300)}`, 'err'); }

// ---------------------------------------------------------------- A. accounts
for (const box of document.querySelectorAll('.slot')) {
  const slot = box.dataset.slot;
  const who = box.querySelector('.who');
  const showWho = () => {
    const u = users[slot];
    who.textContent = tokens[slot] ? `Đã đăng nhập: ${u ? (u.email || u.id || '') : '(token đã khôi phục)'} ${u && u.roles ? '[' + [].concat(u.roles).join(',') + ']' : ''}` : 'chưa đăng nhập';
    who.className = 'who ' + (tokens[slot] ? 'ok' : 'muted');
  };
  const take = (r) => {
    const d = unwrap(r) || {};
    if (r.ok && d.accessToken) { tokens[slot] = d.accessToken; if (d.user) users[slot] = d.user; saveTokens(); }
    else logFail(r);
    showWho();
  };
  box.querySelector('.login').onclick = async () => {
    take(await api('POST', `${API}/auth/login`, {
      slot, body: { email: box.querySelector('.email').value.trim(), password: box.querySelector('.password').value },
    }));
  };
  // Refresh = GET csrf (needs refresh cookie, kept by the proxy) then POST refresh with X-CSRF-Token.
  box.querySelector('.refresh').onclick = async () => {
    const c = await api('GET', `${API}/auth/csrf`, { slot });
    const csrf = (unwrap(c) || {}).csrfToken;
    if (!c.ok || !csrf) { logFail(c); return; }
    take(await api('POST', `${API}/auth/refresh`, { slot, headers: { 'X-CSRF-Token': csrf } }));
  };
  box.querySelector('.logout').onclick = () => { delete tokens[slot]; delete users[slot]; saveTokens(); showWho(); };
  showWho();
}

// ---------------------------------------------------------------- B. orders
const S = { orderId: '', items: [], consentId: '', sessionId: '', session: null, sources: [] };

$('orders-load').onclick = async () => {
  const r = await api('GET', `${API}/orders?limit=20`, { slot: 'customer' });
  if (!r.ok) { logFail(r); return; }
  const rows = unwrap(r) || [];
  const list = Array.isArray(rows) ? rows : (rows.items || []);
  $('orders-list').replaceChildren(el('table', {},
    el('thead', {}, el('tr', {}, ['orderNo', 'status', 'plan', 'items', 'id', ''].map((t) => el('th', {}, t)))),
    el('tbody', {}, list.map((o) => el('tr', { class: o.id === S.orderId ? 'sel' : '' },
      el('td', {}, o.orderNo), el('td', {}, o.status), el('td', {}, pick(o, 'sourcePlanMode', 'source_plan_mode')),
      el('td', {}, pick(o, 'itemsCount', 'items_count')), el('td', {}, el('code', {}, o.id)),
      el('td', {}, el('button', { onclick: () => selectOrder(o.id) }, 'Chọn')))))));
  if (!list.length) $('orders-list').textContent = 'Không có đơn.';
};
$('order-use').onclick = () => { const v = $('order-id').value.trim(); if (v) selectOrder(v); };

async function selectOrder(id) {
  S.orderId = id; S.items = []; $('order-id').value = id;
  const r = await api('GET', `${API}/orders/${id}`, { slot: 'customer' });
  const d = unwrap(r) || {};
  if (!r.ok) logFail(r);
  S.items = r.ok ? (d.items || []) : [];
  renderItems(r.ok ? d : null);
}
function renderItems(order) {
  const box = $('order-items');
  box.className = 'tablewrap';
  if (!order) { box.className = 'tablewrap muted'; box.textContent = `Order ${S.orderId} (không đọc được chi tiết; vẫn dùng id này)`; renderConsentItems(); return; }
  box.replaceChildren(el('div', { class: 'muted' }, `Order ${order.orderNo || ''} - ${order.status} - plan ${pick(order, 'sourcePlanMode', 'source_plan_mode') || '?'} (id ${S.orderId})`),
    el('table', {},
      el('thead', {}, el('tr', {}, ['#', 'template', 'package', 'version', 'memory', 'ringItemId'].map((t) => el('th', {}, t)))),
      el('tbody', {}, S.items.map((i) => el('tr', {},
        el('td', {}, pick(i, 'itemNo', 'item_no')), el('td', {}, pick(i, 'templateName', 'template_name', 'templateCode')),
        el('td', {}, pick(i, 'personalizationPackage', 'personalization_package')),
        el('td', {}, `v${pick(i, 'versionNo', 'version_no') ?? '?'}`),
        el('td', {}, String(pick(i, 'memoryEnabled', 'memory_enabled') ?? '')), el('td', {}, el('code', {}, i.id)))))));
  renderConsentItems();
}

// ---------------------------------------------------------------- C. consent
function renderConsentItems() {
  const box = $('consent-items');
  box.className = 'checks';
  if (!S.items.length) { box.className = 'checks muted'; box.textContent = '(không có ring item - nhập id thủ công)'; return; }
  box.replaceChildren(...S.items.map((i) => el('label', { class: 'inline' },
    el('input', { type: 'checkbox', value: i.id, checked: true }), ` #${pick(i, 'itemNo', 'item_no')} `)));
}
function consentRingIds() {
  const manual = $('consent-items-manual').value.split(',').map((s) => s.trim()).filter(Boolean);
  if (manual.length) return manual;
  return [...$('consent-items').querySelectorAll('input:checked')].map((c) => c.value);
}
const needOrder = () => { if (!S.orderId) { log('Chưa chọn order (mục B)', 'err'); return false; } return true; };

$('consent-accept').onclick = async () => {
  if (!needOrder()) return;
  const r = await api('POST', `${API}/orders/${S.orderId}/consents`, {
    slot: 'customer',
    body: { consentType: $('consent-type').value, policyVersion: $('consent-policy').value.trim(), ringItemIds: consentRingIds() },
  });
  if (r.ok) { const c = unwrap(r); S.consentId = c.consentId; $('sess-consent').value = c.consentId; await listConsents(); } else logFail(r);
};
$('consent-list').onclick = () => listConsents();
async function listConsents() {
  if (!needOrder()) return;
  const r = await api('GET', `${API}/orders/${S.orderId}/consents`, { slot: 'customer' });
  if (!r.ok) { logFail(r); return; }
  const d = unwrap(r) || {};
  const rows = d.items || [];
  $('consent-table').replaceChildren(el('table', {},
    el('thead', {}, el('tr', {}, ['type', 'status', 'policy', 'ringItems', 'consentId', ''].map((t) => el('th', {}, t)))),
    el('tbody', {}, rows.map((c) => el('tr', {},
      el('td', {}, c.consentType), el('td', {}, c.status), el('td', {}, c.policyVersion),
      el('td', {}, (c.ringItemIds || []).length), el('td', {}, el('code', {}, c.consentId)),
      el('td', {},
        el('button', { class: 'secondary', onclick: () => { $('sess-consent').value = c.consentId; S.consentId = c.consentId; } }, 'Dùng cho phiên'),
        el('button', { class: 'danger', onclick: () => revokeConsent(c.consentId) }, 'Thu hồi')))))));
}
async function revokeConsent(id) {
  const r = await api('POST', `${API}/orders/${S.orderId}/consents/${id}/revoke`, { slot: 'customer' });
  if (!r.ok) logFail(r);
  await listConsents();
}

// ---------------------------------------------------------------- D. capture session (staff)
$('sess-create').onclick = async () => {
  if (!needOrder()) return;
  const r = await api('POST', `${API}/orders/${S.orderId}/fingerprint/capture-sessions`, {
    slot: 'staff', body: { consentId: $('sess-consent').value.trim(), deviceCode: $('sess-device').value.trim() },
  });
  if (r.ok) { setSession(unwrap(r)); } else logFail(r);
};
$('sess-get').onclick = () => loadSession();
function setSession(s) {
  S.session = s; S.sessionId = s.sessionId; $('sess-id').value = s.sessionId;
  $('ser-session').value = s.sessionId; $('ser-device').value = $('ser-device').value || s.deviceCode;
  renderSession(s);
}
async function loadSession() {
  const id = $('sess-id').value.trim();
  if (!id) return;
  S.sessionId = id;
  const r = await api('GET', `${API}/fingerprint/capture-sessions/${id}`, { slot: 'staff' });
  if (r.ok) setSession(unwrap(r)); else logFail(r);
}
const imgCache = {}; // `${sessionId}:${attemptNo}` -> presigned image url (60 s)
async function fetchImage(sessionId, no) {
  const r = await api('GET', `${API}/fingerprint/capture-sessions/${sessionId}/attempts/${no}/image`, { slot: 'staff' });
  if (!r.ok) { logFail(r); return null; }
  const url = unwrap(r).imageUrl; imgCache[`${sessionId}:${no}`] = url; return url;
}
function renderSession(s) {
  $('sess-info').className = '';
  $('sess-info').textContent = `status ${s.status} - ${s.attemptCount}/${s.maxAttempts} lần - hết hạn ${s.expiresAt} - device ${s.deviceCode}` +
    (s.acceptedSourceId ? ` - acceptedSourceId ${s.acceptedSourceId}` : '');
  const open = s.status === 'IN_PROGRESS' || s.status === 'CREATED';
  $('sess-attempts').replaceChildren(el('table', {},
    el('thead', {}, el('tr', {}, ['#', 'result', 'score', 'suggestion', 'failureReason', 'ảnh', ''].map((t) => el('th', {}, t)))),
    el('tbody', {}, (s.attempts || []).map((a) => {
      const img = el('img', { class: 'thumb', alt: '' });
      const key = `${s.sessionId}:${a.attemptNo}`;
      const show = async () => { const u = await fetchImage(s.sessionId, a.attemptNo); if (u) img.src = u; };
      img.onerror = () => { img.removeAttribute('src'); };
      if (a.result !== 'ERROR' && a.failureReason !== 'CAPTURE_FAILED') { if (imgCache[key]) img.src = imgCache[key]; else show(); }
      return el('tr', {},
        el('td', {}, a.attemptNo), el('td', {}, a.result), el('td', {}, a.qualityScore ?? ''),
        el('td', {}, a.suggestion ?? ''), el('td', {}, a.failureReason ?? ''), el('td', {}, img, el('br'), el('button', { class: 'secondary', onclick: show }, 'tải lại')),
        el('td', {},
          el('button', { disabled: !open || a.result !== 'PENDING_REVIEW', onclick: () => sessAction('complete', a.attemptNo) }, 'Chọn lần này'),
          el('button', { class: 'danger', disabled: !open || a.result !== 'PENDING_REVIEW', onclick: () => sessAction('reject', a.attemptNo) }, 'Từ chối lần')));
    }))));
}
async function sessAction(kind, attemptNo) {
  const id = $('sess-id').value.trim();
  const base = `${API}/fingerprint/capture-sessions/${id}`;
  const r = kind === 'complete' ? await api('POST', `${base}/complete`, { slot: 'staff', body: { attemptNo } })
    : kind === 'reject' ? await api('POST', `${base}/attempts/${attemptNo}/reject`, { slot: 'staff' })
    : await api('POST', `${base}/${kind}`, { slot: 'staff' }); // next-attempt | close
  if (r.ok) setSession(unwrap(r)); else logFail(r);
}
$('sess-next').onclick = () => sessAction('next-attempt');
$('sess-close').onclick = () => sessAction('close');
setInterval(() => { if ($('sess-poll').checked && $('sess-id').value.trim() && tokens.staff) loadSession(); }, 2000);

// ---------------------------------------------------------------- E. sources + processing
const TERMINAL = ['READY', 'PROCESSING_FAILED'];
$('src-list').onclick = async () => {
  if (!needOrder()) return;
  const r = await api('GET', `${API}/orders/${S.orderId}/personalization-sources`, { slot: 'customer' });
  if (!r.ok) { logFail(r); renderSources([]); return; }
  const d = unwrap(r);
  S.sources = (Array.isArray(d) ? d : (d && d.items) || []).map((s) => ({ ...s, _id: pick(s, 'sourceId', 'id'), _proc: null }));
  renderSources(S.sources);
  for (const s of S.sources) pollSource(s);
};
async function pollSource(s) {
  const r = await api('GET', `${API}/personalization-sources/${s._id}/processing`, { slot: 'customer' });
  s._proc = r.ok ? unwrap(r) : { error: r.status };
  renderSources(S.sources);
}
function renderSources(list) {
  $('src-table').replaceChildren(el('table', {},
    el('thead', {}, el('tr', {}, ['sourceId', 'type', 'status', 'processing', ''].map((t) => el('th', {}, t)))),
    el('tbody', {}, list.map((s) => el('tr', {},
      el('td', {}, el('code', {}, s._id)), el('td', {}, pick(s, 'sourceType', 'type') || ''), el('td', {}, s.status || ''),
      el('td', {}, s._proc ? JSON.stringify(s._proc) : ''),
      el('td', {}, el('button', { class: 'secondary', onclick: () => pollSource(s) }, 'Cập nhật')))))));
  if (!list.length) $('src-table').textContent = 'Chưa có nguồn.';
  const st = (s) => (s._proc && (s._proc.status || s._proc.processingStatus)) || s.status;
  if (list.some((s) => st(s) === 'READY')) log('Có nguồn READY: xem trước ở mục G (backend chưa có preview API).');
}
setInterval(() => {
  if (!$('src-poll').checked || !tokens.customer) return;
  for (const s of S.sources) {
    const st = (s._proc && (s._proc.status || s._proc.processingStatus)) || s.status;
    if (!TERMINAL.includes(st) && s._id) pollSource(s);
  }
}, 3000);

// ---------------------------------------------------------------- audio helpers (F + G)
async function sha256Hex(buf) {
  const d = await crypto.subtle.digest('SHA-256', buf);
  return [...new Uint8Array(d)].map((b) => b.toString(16).padStart(2, '0')).join('');
}
async function audioDurationMs(buf) {
  const ctx = new (window.AudioContext || window.webkitAudioContext)();
  try { return Math.round((await ctx.decodeAudioData(buf.slice(0))).duration * 1000); } finally { ctx.close(); }
}
function setupSlider(slider, label, durMs) {
  slider.max = Math.max(0, durMs - 3000); slider.value = 0; slider.disabled = durMs < 3000; label.textContent = '0';
  slider.oninput = () => { label.textContent = slider.value; };
}

// ---------------------------------------------------------------- F. soundwave (PERSO-009/010)
// Backend accepts audio/wav | audio/mpeg | audio/mp4 | audio/aac, <= 10 MiB, 3000..30000 ms.
const SW = { file: null, buf: null, durationMs: 0, sha: '', origin: 'UPLOAD', uploadId: '', uploadUrl: '', uploadHeaders: {} };
let recorder = null;

/** Map browser MIME types to the four types PERSO-009 accepts ('' = unsupported). */
function swContentType(file) {
  const t = (file.type || '').toLowerCase();
  const name = (file.name || '').toLowerCase();
  if (t === 'audio/wav' || t === 'audio/x-wav' || t === 'audio/wave' || name.endsWith('.wav')) return 'audio/wav';
  if (t === 'audio/mpeg' || t === 'audio/mp3' || name.endsWith('.mp3')) return 'audio/mpeg';
  if (t === 'audio/mp4' || t === 'audio/x-m4a' || t === 'audio/m4a' || name.endsWith('.m4a')) return 'audio/mp4';
  if (t === 'audio/aac' || t === 'audio/x-aac' || name.endsWith('.aac')) return 'audio/aac';
  return '';
}

/** Encode a decoded AudioBuffer as mono 16-bit PCM WAV (browser recordings are webm/ogg). */
function encodeWav(audioBuffer) {
  const rate = audioBuffer.sampleRate;
  const n = audioBuffer.length;
  const mono = new Float32Array(n);
  for (let c = 0; c < audioBuffer.numberOfChannels; c++) {
    const ch = audioBuffer.getChannelData(c);
    for (let i = 0; i < n; i++) mono[i] += ch[i] / audioBuffer.numberOfChannels;
  }
  const out = new DataView(new ArrayBuffer(44 + n * 2));
  const str = (o, s) => { for (let i = 0; i < s.length; i++) out.setUint8(o + i, s.charCodeAt(i)); };
  str(0, 'RIFF'); out.setUint32(4, 36 + n * 2, true); str(8, 'WAVE'); str(12, 'fmt ');
  out.setUint32(16, 16, true); out.setUint16(20, 1, true); out.setUint16(22, 1, true);
  out.setUint32(24, rate, true); out.setUint32(28, rate * 2, true); out.setUint16(32, 2, true); out.setUint16(34, 16, true);
  str(36, 'data'); out.setUint32(40, n * 2, true);
  for (let i = 0; i < n; i++) out.setInt16(44 + i * 2, Math.max(-1, Math.min(1, mono[i])) * 0x7fff, true);
  return new Blob([out.buffer], { type: 'audio/wav' });
}

async function swSetFile(file) {
  SW.file = file; SW.buf = await file.arrayBuffer();
  try { SW.durationMs = await audioDurationMs(SW.buf); } catch (e) { SW.durationMs = 0; log(`Không giải mã được audio: ${e.message}`, 'err'); }
  SW.sha = await sha256Hex(SW.buf);
  $('sw-audio').hidden = false; $('sw-audio').src = URL.createObjectURL(file);
  $('sw-meta').textContent = `${file.name || 'recording'} - ${file.type || '?'} - ${file.size} bytes - ${SW.durationMs} ms - sha256 ${SW.sha.slice(0, 16)}...`;
  setupSlider($('sw-start'), $('sw-start-label'), SW.durationMs);
  if (SW.durationMs < 3000) log('Audio ngắn hơn 3000 ms: không thể chọn đoạn 3 s.', 'err');
  swBody();
}
$('sw-start').addEventListener('input', () => swBody());
/** PERSO-009 body (editable). The 3 s segment goes to PERSO-010 complete, not here. */
function swBody() {
  const ct = SW.file ? swContentType(SW.file) : '';
  if (SW.file && !ct) log(`Định dạng ${SW.file.type || SW.file.name} không được hỗ trợ (chỉ WAV/MP3/M4A/AAC).`, 'err');
  $('sw-body').value = pretty({
    consentId: S.consentId || $('sess-consent').value.trim(),
    contentType: ct, sizeBytes: SW.file ? SW.file.size : 0, sha256: SW.sha,
    origin: SW.origin, durationMs: SW.durationMs,
  });
}
$('sw-file').onchange = (e) => { if (e.target.files[0]) { SW.origin = 'UPLOAD'; swSetFile(e.target.files[0]); } };
$('sw-rec').onclick = async () => {
  try {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    const chunks = []; recorder = new MediaRecorder(stream);
    recorder.ondataavailable = (e) => chunks.push(e.data);
    recorder.onstop = async () => {
      stream.getTracks().forEach((t) => t.stop()); clearTimeout(recorder._t);
      $('sw-rec').disabled = false; $('sw-stop').disabled = true; $('sw-rec-state').textContent = 'đã ghi xong, đổi sang WAV';
      const blob = new Blob(chunks, { type: recorder.mimeType || 'audio/webm' });
      const ctx = new (window.AudioContext || window.webkitAudioContext)();
      try {
        const wav = encodeWav(await ctx.decodeAudioData(await blob.arrayBuffer()));
        SW.origin = 'IN_APP_RECORDING';
        swSetFile(new File([wav], 'recording.wav', { type: 'audio/wav' }));
      } catch (e) { log(`Không đổi được bản ghi sang WAV: ${e.message}`, 'err'); } finally { ctx.close(); }
    };
    recorder.start(); $('sw-rec').disabled = true; $('sw-stop').disabled = false; $('sw-rec-state').textContent = 'đang ghi (tối đa 30 s)...';
    recorder._t = setTimeout(() => recorder.state === 'recording' && recorder.stop(), 30000);
  } catch (e) { log(`Mic lỗi: ${e.message}`, 'err'); }
};
$('sw-stop').onclick = () => recorder && recorder.state === 'recording' && recorder.stop();

$('sw-init').onclick = async () => {
  if (!needOrder() || !SW.file) { log('Cần chọn order và file audio.', 'err'); return; }
  let body; try { body = JSON.parse($('sw-body').value); } catch (e) { log(`Body JSON sai: ${e.message}`, 'err'); return; }
  const r = await api('POST', `${API}/orders/${S.orderId}/soundwave/uploads/initiate`, { slot: 'customer', body });
  $('sw-json').textContent = pretty(r.json);
  if (!r.ok) { logFail(r); return; }
  const d = unwrap(r) || {};
  SW.uploadId = d.uploadId || '';
  SW.uploadUrl = d.uploadUrl || ''; SW.uploadHeaders = d.uploadHeaders || {};
  $('sw-state').textContent = `uploadId ${SW.uploadId || '(không thấy trong phản hồi)'} - hết hạn ${d.expiresAt || '?'}`;
  $('sw-put').disabled = !SW.uploadUrl;
  $('sw-json').textContent = pretty({ ...d, uploadUrl: d.uploadUrl ? '<hidden>' : undefined });
};
$('sw-put').onclick = async () => {
  const t0 = performance.now();
  try {
    const res = await fetch(SW.uploadUrl, { method: 'PUT', headers: SW.uploadHeaders, body: SW.file });
    const u = new URL(SW.uploadUrl);
    log(`PUT ${u.host}${u.pathname} -> ${res.status} (${Math.round(performance.now() - t0)} ms)`, res.ok ? 'ok' : 'err');
    if (res.ok) $('sw-complete').disabled = false; else log(`  ${(await res.text()).slice(0, 300)}`, 'err');
  } catch (e) { log(`PUT lỗi (CORS/mạng?): ${e.message}`, 'err'); }
};
$('sw-complete').onclick = async () => {
  const body = { uploadId: SW.uploadId, segmentStartMs: Number($('sw-start').value) };
  const r = await api('POST', `${API}/orders/${S.orderId}/soundwave/uploads/complete`, { slot: 'customer', body });
  $('sw-json').textContent = pretty(r.json);
  if (!r.ok) { logFail(r); return; }
  const d = unwrap(r) || {};
  S.sources = [{ _id: d.sourceId || SW.uploadId, sourceType: 'SOUNDWAVE', status: d.status || 'ACCEPTED', _proc: null }];
  renderSources(S.sources); pollSource(S.sources[0]);
};

// ---------------------------------------------------------------- G. worker
const W = { file: null, kind: '', durationMs: 0, last: null, sourceId: '', prefix: '' };

async function workerCall(path, body) {
  const t0 = performance.now();
  const res = await fetch(`/worker${path}`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
  const text = await res.text(); let json = null; try { json = JSON.parse(text); } catch { /* */ }
  log(`POST /worker${path} -> ${res.status} (${Math.round(performance.now() - t0)} ms)`, res.ok ? 'ok' : 'err');
  if (!res.ok && json) log(`  ${json.code || 'ERROR'}: ${json.message || ''}`, 'err');
  // Gateway envelope: success {statusCode, message, data}; error {statusCode, code, message, path, timestamp}.
  return { ok: res.ok, status: res.status, json, text, data: res.ok && json ? json.data : null, code: json && json.code, message: json && json.message };
}
async function presign(key) {
  const res = await fetch(`/dev/presign?key=${encodeURIComponent(key)}`);
  log(`GET /dev/presign -> ${res.status}`, res.ok ? 'ok' : 'err');
  return res.ok ? (await res.json()).url : null;
}
/** Upload a Blob under personalization/test-ui/ through the server (never directly). Optional explicit key. */
async function uploadRaw(blob, contentType, explicitKey) {
  const key = explicitKey || `${WORKER_TMP}${uuid()}/raw`;
  const res = await fetch(`/dev/upload-raw?key=${encodeURIComponent(key)}&contentType=${encodeURIComponent(contentType)}`, { method: 'POST', body: blob });
  const j = await res.json(); log(`POST /dev/upload-raw -> ${res.status}`, res.ok ? 'ok' : 'err');
  if (!res.ok) throw new Error(pretty(j));
  return j;
}

$('w-health').onclick = async () => {
  const res = await fetch('/worker/health'); const j = await res.json().catch(() => ({}));
  log(`GET /worker/health -> ${res.status}`, res.ok ? 'ok' : 'err');
  $('w-health-out').textContent = res.ok && j.data
    ? `${res.status} ${j.message} ${JSON.stringify(j.data)}`
    : `${res.status} ${j.code || 'ERROR'}: ${j.message || ''}`;
};
$('w-file').onchange = async (e) => {
  const f = e.target.files[0]; if (!f) return;
  W.file = f; W.kind = f.type.startsWith('audio/') ? 'audio' : 'image';
  $('w-audio-opts').hidden = W.kind !== 'audio';
  if (W.kind === 'audio') {
    W.durationMs = await audioDurationMs(await f.arrayBuffer()).catch(() => 0);
    setupSlider($('w-start'), $('w-start-label'), W.durationMs);
  }
  $('w-file-info').textContent = `${f.name} - ${f.type || '?'} - ${f.size} bytes${W.kind === 'audio' ? ` - ${W.durationMs} ms` : ''}`;
};
$('w-run').onclick = () => { if (W.file) runWorker(W.file, W.kind, W.file.type); else log('Chọn file trước.', 'err'); };

/** Upload a blob then run the fingerprint/soundwave processor; also used by section H. */
async function runWorker(blob, kind, contentType) {
  const state = $('w-state'); state.textContent = 'đang upload...';
  try {
    if (kind === 'audio') {
      // Worker accepts audio/wav|mpeg|mp4|aac only; convert anything else (e.g. webm) to mono 16-bit WAV.
      let ct = swContentType({ type: contentType || blob.type, name: blob.name });
      if (!ct) {
        state.textContent = 'đổi sang WAV...';
        const ctx = new (window.AudioContext || window.webkitAudioContext)();
        try {
          const decoded = await ctx.decodeAudioData(await blob.arrayBuffer());
          blob = encodeWav(decoded); ct = 'audio/wav'; W.durationMs = Math.round(decoded.duration * 1000);
          log(`Đã đổi audio sang WAV (${blob.size} bytes, ${W.durationMs} ms).`, 'ok');
        } finally { ctx.close(); }
      }
      contentType = ct;
    }
    const up = await uploadRaw(blob, contentType || 'image/bmp');
    W.sourceId = uuid(); W.prefix = `${WORKER_TMP}${uuid()}/out/`;
    state.textContent = 'đang xử lý...';
    const body = { sourceId: W.sourceId, rawKey: up.key, outputPrefix: W.prefix };
    let path = '/v1/fingerprint/process';
    if (kind === 'audio') {
      path = '/v1/soundwave/process';
      Object.assign(body, { contentType, declaredDurationMs: W.durationMs || 3000, segmentStartMs: Number($('w-start').value), segmentDurationMs: 3000 });
    }
    const r = await workerCall(path, body);
    W.last = r.data; $('w-json').textContent = r.json ? pretty(r.json) : r.text;
    state.textContent = r.ok ? 'xong' : `lỗi ${r.status} ${r.code || ''}: ${r.message || ''}`; state.className = r.ok ? 'ok' : 'err';
    if (r.ok && r.data) await showWorkerResult(r.data);
  } catch (e) { state.textContent = `lỗi: ${e.message}`; state.className = 'err'; log(`Worker panel lỗi: ${e.message}`, 'err'); }
}

async function showWorkerResult(res) {
  const o = res.outputs || {};
  const pngUrl = o.alphaPng && await presign(o.alphaPng.key);
  if (pngUrl) drawPngWithHull(pngUrl, res.artwork);
  if (o.vectorSvg) { const u = await presign(o.vectorSvg.key); if (u) $('w-svg').src = u; }
  $('w-audio-wrap').hidden = !o.audioSegment;
  if (o.audioSegment) { const u = await presign(o.audioSegment.key); if (u) $('w-audio-seg').src = u; }
  $('w-wave').hidden = !res.wavecode;
  if (res.wavecode) showWavecode(res.wavecode);
}
function drawPngWithHull(url, artwork) {
  const img = new Image();
  img.onload = () => {
    const c = $('w-canvas'); const s = Math.min(1, 300 / Math.max(img.width, img.height));
    c.width = Math.round(img.width * s); c.height = Math.round(img.height * s);
    const g = c.getContext('2d'); g.drawImage(img, 0, 0, c.width, c.height);
    const hull = (artwork && artwork.hull) || [];
    if (hull.length) {
      // Hull may be normalised (0..1) or in pixels; scale accordingly.
      const max = Math.max(...hull.flat().map(Math.abs));
      const [kx, ky] = max <= 1.5 ? [c.width, c.height] : [c.width / img.width, c.height / img.height];
      g.strokeStyle = 'red'; g.lineWidth = 2; g.beginPath();
      hull.forEach(([x, y], i) => (i ? g.lineTo(x * kx, y * ky) : g.moveTo(x * kx, y * ky)));
      g.closePath(); g.stroke();
    }
  };
  img.src = url; // cross-origin drawImage is allowed; we never read pixels
}
function barsRow(box, values, max, cls) {
  box.replaceChildren(...values.map((v, i) => el('span', { class: typeof cls === 'function' ? cls(i) : cls, title: String(v), style: `height:${Math.max(2, (v / max) * 50)}px` })));
}
function showWavecode(wc) {
  barsRow($('w-levels'), wc.levels, 7, '');
  barsRow($('w-bars'), wc.bars, Math.max(7, ...wc.bars), (i) => (wc.checkBars.length && i >= wc.bars.length - wc.checkBars.length ? 'chk' : ''));
  $('w-nudge').replaceChildren(...(wc.nudgeCandidates || []).map((c, i) => el('option', { value: i }, `#${i + 1}: ${c.join('')}`)));
  $('w-nudge').hidden = !(wc.nudgeCandidates || []).length;
}
$('w-render').onclick = async () => {
  const cand = W.last && W.last.wavecode && W.last.wavecode.nudgeCandidates[Number($('w-nudge').value)];
  if (!cand) { log('Không có nudge candidate.', 'err'); return; }
  const prefix = `${WORKER_TMP}${uuid()}/render/`;
  const r = await workerCall('/v1/soundwave/render-wavecode', { sourceId: W.sourceId, outputPrefix: prefix, levels: cand });
  $('w-json').textContent = r.json ? pretty(r.json) : r.text;
  if (!r.ok || !r.data) return;
  const png = await presign(r.data.outputs.alphaPng.key); if (png) $('w-render-png').src = png;
  const svg = await presign(r.data.outputs.vectorSvg.key); if (svg) $('w-render-svg').src = svg;
};

// ---------------------------------------------------------------- H. ESP32 over Web Serial (MQTT lives in sim.js)
const SER = { port: null, reader: null, writer: null, open: false, lastAttempt: null, lastBmp: null, ping: null };
const rawLines = [];

function serialLine(line) {
  if (line.startsWith('@MEMORA ')) {
    let f; try { f = JSON.parse(line.slice(8)); } catch { rawLines.push(`(frame JSON lỗi) ${line.slice(0, 120)}`); return; }
    addFrame('in', f);
    if (f.t === 'attempts') onAttempt(f);
    if (f.t === 'hello' || f.t === 'pong') $('ser-state').textContent = `kết nối - ${f.t} ${JSON.stringify(f.p || {})}`;
  } else {
    rawLines.push(line); if (rawLines.length > 300) rawLines.shift();
    $('ser-raw').textContent = rawLines.join('\n'); $('ser-raw').scrollTop = 1e9;
  }
}
function addFrame(dir, f) {
  const shown = JSON.parse(JSON.stringify(f));
  if (shown.p && typeof shown.p.imageBase64 === 'string') shown.p.imageBase64 = `<base64 ${shown.p.imageBase64.length} chars>`;
  const box = $('ser-frames');
  box.append(el('div', { class: 'frame' }, el('b', { class: dir }, dir === 'in' ? '<- ' : '-> '), `${new Date().toLocaleTimeString('en-GB')} ${JSON.stringify(shown)}`));
  while (box.childNodes.length > 200) box.firstChild.remove();
  box.scrollTop = box.scrollHeight;
  log(`FRAME ${dir === 'in' ? '<-' : '->'} ${f.t}`);
}
function onAttempt(f) {
  SER.lastAttempt = f.p || {};
  const b64 = SER.lastAttempt.imageBase64;
  if (!b64) { $('ser-image').hidden = true; return; }
  const bin = atob(b64); const bytes = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
  SER.lastBmp = new Blob([bytes], { type: 'image/bmp' });
  $('ser-bmp').src = URL.createObjectURL(SER.lastBmp); $('ser-image').hidden = false;
  if (SER.lastAttempt.sessionId) $('ser-session').value = SER.lastAttempt.sessionId;
}
$('ser-to-worker').onclick = () => { if (SER.lastBmp) runWorker(SER.lastBmp, 'image', 'image/bmp'); };

$('ser-connect').onclick = async () => {
  if (!('serial' in navigator)) { log('Trình duyệt không hỗ trợ Web Serial (dùng Chrome/Edge).', 'err'); return; }
  try {
    SER.port = await navigator.serial.requestPort();
    await SER.port.open({ baudRate: Number($('ser-baud').value), bufferSize: 262144 });
    SER.open = true; $('ser-connect').disabled = true; $('ser-disconnect').disabled = false;
    $('ser-state').textContent = 'đã mở cổng, chờ hello...'; log(`Serial mở @ ${$('ser-baud').value}`);
    readLoop();
    SER.ping = setInterval(() => $('ser-autoping').checked && sendFrame('ping'), 5000);
    sendFrame('ping'); // device answers hello/pong
  } catch (e) { log(`Serial lỗi: ${e.message}`, 'err'); }
};
async function readLoop() {
  const dec = new TextDecoder(); let buf = '';
  while (SER.open && SER.port.readable) {
    SER.reader = SER.port.readable.getReader();
    try {
      for (;;) {
        const { value, done } = await SER.reader.read(); if (done) break;
        buf += dec.decode(value, { stream: true });
        let i; while ((i = buf.indexOf('\n')) >= 0) { const line = buf.slice(0, i).replace(/\r$/, ''); buf = buf.slice(i + 1); if (line) serialLine(line); }
      }
    } catch (e) { log(`Serial đọc lỗi: ${e.message}`, 'err'); } finally { SER.reader.releaseLock(); }
  }
}
$('ser-disconnect').onclick = async () => {
  SER.open = false; clearInterval(SER.ping);
  try { await SER.reader?.cancel(); await SER.port?.close(); } catch { /* ignore */ }
  $('ser-connect').disabled = false; $('ser-disconnect').disabled = true; $('ser-state').textContent = 'đã ngắt'; log('Serial đã đóng');
};
async function sendFrame(kind, payload) {
  const t = kind === 'start' || kind === 'next' || kind === 'end' ? 'commands' : kind;
  if (!SER.open || !SER.port.writable) { if (kind !== 'ping') log('Chưa kết nối serial.', 'err'); return; }
  const frame = { t, d: $('ser-device').value.trim(), p: payload ?? {} };
  const w = SER.port.writable.getWriter();
  try { await w.write(new TextEncoder().encode(`@MEMORA ${JSON.stringify(frame)}\n`)); } finally { w.releaseLock(); }
  if (kind !== 'ping') addFrame('out', frame);
}
for (const b of document.querySelectorAll('.ser-send')) {
  b.onclick = () => {
    let sessionId = $('ser-session').value.trim(); const attemptNo = Number($('ser-attempt').value);
    if (!sessionId && b.dataset.kind === 'start') { sessionId = uuid(); $('ser-session').value = sessionId; }
    const s = S.session && S.session.sessionId === sessionId ? S.session : null;
    switch (b.dataset.kind) {
      case 'ping': return sendFrame('ping');
      case 'start': return sendFrame('start', { type: 'start', sessionId, expiresAt: (s && s.expiresAt) || new Date(Date.now() + 15 * 60000).toISOString(), maxAttempts: (s && s.maxAttempts) || 3 });
      case 'next': return sendFrame('next', { type: 'next', sessionId, attemptNo });
      case 'end': return sendFrame('end', { type: 'end', sessionId, status: $('ser-end-status').value, ...($('ser-end-status').value === 'COMPLETED' ? { acceptedAttemptNo: attemptNo } : {}) });
      case 'results': {
        const a = SER.lastAttempt;
        if (!a || !a.nonce) { log('Chưa nhận frame attempts nào (cần nonce).', 'err'); return; }
        const rejected = $('ser-res-result').value === 'REJECTED';
        return sendFrame('results', {
          sessionId: a.sessionId || sessionId, nonce: a.nonce, ok: true, attemptNo, result: $('ser-res-result').value,
          ...(rejected ? { reason: 'CAPTURE_FAILED' } : { suggestion: $('ser-res-sug').value }),
          remainingAttempts: Math.max(0, 3 - attemptNo), sessionStatus: 'IN_PROGRESS',
        });
      }
      default: return undefined;
    }
  };
}

// ---------------------------------------------------------------- boot
(async () => {
  try {
    const c = await (await fetch('/dev/config')).json();
    $('cfg').textContent = `gateway ${c.gatewayUrl} - worker ${c.workerUrl} - bucket ${c.bucket || '?'} - secret worker ${c.workerSecretLoaded ? 'đã nạp' : 'THIẾU'} - S3 ${c.s3Configured ? 'ok' : 'THIẾU'}`;
  } catch { $('cfg').textContent = 'không đọc được /dev/config'; }
  swBody();
})();
