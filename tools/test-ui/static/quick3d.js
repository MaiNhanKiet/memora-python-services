// Quick 3D: model = default or a .glb uploaded from the machine; artwork = the selected item's asset
// (Soundwave: current wavecode SVG; fingerprint: current/selected fingerprint_alpha.png).
(function () {
  const $ = (id) => document.getElementById(id);
  const KINDS = { SW: { model: 'BAR_001_SW', kind: 'WAVECODE' }, FP: { model: 'SIGNET_001_FP', kind: 'FINGERPRINT' } };
  const TAB_KIND = { 'tab-fp': 'FP', 'tab-sw': 'SW' };
  const msg = (t, cls) => { const m = $('q3d-msg'); m.textContent = t; m.className = cls || 'muted'; };
  let viewer = null, kind = 'FP';

  // The panel follows the active tab: tab 1 -> fingerprint item, tab 2 -> Soundwave item, tab 3 -> hidden.
  function follow(tab) {
    const k = TAB_KIND[tab];
    $('q3d-card').hidden = !k;
    if (!k || k === kind) return;
    kind = k;
    if (viewer) { viewer.dispose(); viewer = null; }
    $('q3d').replaceChildren(); msg('');
    $('q3d-kind').textContent = k === 'SW' ? 'Item Soundwave (mặc định BAR_001_SW)' : 'Item vân tay (mặc định SIGNET_001_FP)';
  }
  for (const b of document.querySelectorAll('.tabs button')) b.addEventListener('click', () => follow(b.dataset.tab));
  kind = null;
  follow((document.querySelector('.tabs button.active') || {}).dataset?.tab || 'tab-fp');

  async function assetFor(k) {
    const A = window.SimAssets;
    if (!A) throw new Error('trang mô phỏng chưa sẵn sàng');
    const key = k === 'SW' ? A.swKey() : A.fpKey();
    if (!key) throw new Error(k === 'SW' ? 'item chưa có wavecode (xử lý Soundwave ở tab 2 trước)' : 'item chưa có vân tay đã xử lý (tab 1)');
    const r = await fetch(A.url(key));
    if (!r.ok) throw new Error(`tải asset ${r.status}`);
    return { key, blob: await r.blob() };
  }

  $('q3d-show').onclick = async () => {
    const k = kind, base = KINDS[k];
    const glb = $('q3d-glb').files[0], tplFile = $('q3d-tpl').files[0];
    $('q3d-show').disabled = true;
    try {
      msg('đang lấy asset của item...');
      const asset = await assetFor(k);
      const opts = { ...base, label: glb ? glb.name : base.model };
      if (glb) opts.glbBuffer = await glb.arrayBuffer();
      if (tplFile) opts.template = JSON.parse(await tplFile.text());
      if (viewer) { viewer.dispose(); viewer = null; }
      msg('đang dựng mô hình...');
      viewer = await window.Ring3D.mount($('q3d'), opts);
      if (k === 'SW') await viewer.setSvgText(await asset.blob.text());
      else await viewer.setImageUrl(URL.createObjectURL(asset.blob));
      msg(`đã gắn ${asset.key.split('/').slice(-2).join('/')}${glb ? '' : ' (model mặc định)'}`, 'ok');
    } catch (e) {
      viewer = null;
      msg(`Lỗi: ${e.message}${glb ? ' (GLB phải có mesh ENGRAVE_SURFACE có UV)' : ''}`, 'err');
    } finally { $('q3d-show').disabled = false; }
  };
})();
