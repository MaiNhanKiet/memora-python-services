// 3D ring preview (three.js 0.147 UMD, loaded lazily from jsDelivr). Dev tool only.
// Ring3D.mount(host, { model: 'BAR_001_SW', kind: 'WAVECODE' | 'FINGERPRINT' }) -> Promise<viewer>
//   viewer.setSvgText(text)   viewer.setImageUrl(url)   viewer.dispose()
// Models come from GET /models/<NAME>.glb, placement from GET /models/<NAME>/template.json.
(() => {
  const CDN = 'https://cdn.jsdelivr.net/npm/three@0.147.0/';
  const SCRIPTS = ['build/three.min.js', 'examples/js/loaders/GLTFLoader.js',
    'examples/js/controls/OrbitControls.js', 'examples/js/environments/RoomEnvironment.js'];
  const TEX = 4096;
  let loading = null;

  function loadThree() {
    if (window.THREE && THREE.RoomEnvironment) return Promise.resolve();
    if (!loading) {
      loading = SCRIPTS.reduce((p, src) => p.then(() => new Promise((ok, fail) => {
        const s = document.createElement('script'); s.src = CDN + src; s.onload = ok;
        s.onerror = () => fail(new Error(`không tải được ${src} (cần Internet)`));
        document.head.append(s);
      })), Promise.resolve());
      loading.catch(() => { loading = null; });
    }
    return loading;
  }
  const h = (tag, attrs = {}, ...kids) => {
    const n = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs)) n.setAttribute(k, v);
    n.append(...kids); return n;
  };

  /** Placement of the artwork on the engrave surface, from the template JSON (nothing hard-coded). */
  function placement(tpl, kind) {
    const uv = tpl.engrave_surface.uv, [ox, oy] = uv.uv_origin_mm, mm = uv.mm_per_uv_unit;
    if (kind === 'FINGERPRINT') {
      const d = tpl.packages.FINGERPRINT.default;
      const size = Array.isArray(d.size_mm) ? d.size_mm : [d.size_mm, d.size_mm];
      return { centerUv: [(d.center[0] - ox) / mm, (d.center[1] - oy) / mm], sizeMm: size, rotationDeg: d.rotation_deg || 0, mmPerUv: mm };
    }
    const f = tpl.packages.SOUNDWAVE.fixed;
    return { centerUv: f.center_uv, sizeMm: f.size_mm, rotationDeg: f.rotation_deg || 0, mmPerUv: mm };
  }

  async function mount(host, opts) {
    const { model, kind } = opts;
    const status = h('div', { class: 'r3d-status' }, 'Đang tải three.js và mô hình...');
    const stage = h('div', { class: 'r3d-stage' }, status);
    const btn = (label, extra = {}) => h('button', { type: 'button', class: 'secondary', ...extra }, label);
    const finishBtns = [['ox', 'Tô đen (oxi hóa)'], ['raw', 'Khắc mờ'], ['off', 'Ẩn mã']]
      .map(([v, t]) => btn(t, { 'data-v': v, 'aria-pressed': String(v === 'ox') }));
    const bClose = btn('Cận mặt khắc'), bRing = btn('Toàn nhẫn');
    const bU = btn('Lật ngang', { 'aria-pressed': 'false' }), bV = btn('Lật dọc', { 'aria-pressed': 'false' });
    host.replaceChildren(stage, h('div', { class: 'row' }, ...finishBtns, bClose, bRing, bU, bV));

    let tpl, glbBuf;
    try {
      await loadThree();
      const [t, g] = await Promise.all([
        fetch(`/models/${model}/template.json`).then((r) => { if (!r.ok) throw new Error(`template ${r.status}`); return r.json(); }),
        fetch(`/models/${model}.glb`).then((r) => { if (!r.ok) throw new Error(`glb ${r.status}`); return r.arrayBuffer(); }),
      ]);
      tpl = t; glbBuf = g;
    } catch (e) { status.textContent = `Không tải được 3D: ${e.message}`; throw e; }
    const place = placement(tpl, kind);

    const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    renderer.outputEncoding = THREE.sRGBEncoding;
    renderer.toneMapping = THREE.ACESFilmicToneMapping; renderer.toneMappingExposure = 1.05;
    stage.prepend(renderer.domElement);
    const scene = new THREE.Scene();
    scene.environment = new THREE.PMREMGenerator(renderer).fromScene(new THREE.RoomEnvironment(), 0.04).texture;
    const camera = new THREE.PerspectiveCamera(30, 1, 0.001, 100);
    const controls = new THREE.OrbitControls(camera, renderer.domElement); controls.enableDamping = true;
    const resize = () => {
      const w = stage.clientWidth, hh = stage.clientHeight;
      if (w && hh) { renderer.setSize(w, hh, false); camera.aspect = w / hh; camera.updateProjectionMatrix(); }
    };
    const ro = new ResizeObserver(resize); ro.observe(stage);

    const maskCanvas = document.createElement('canvas'); maskCanvas.width = maskCanvas.height = TEX;
    const maskTex = new THREE.CanvasTexture(maskCanvas);
    maskTex.flipY = false; maskTex.anisotropy = renderer.capabilities.getMaxAnisotropy();
    let art = null, flipU = false, flipV = false;
    function drawMask() {
      const g = maskCanvas.getContext('2d');
      g.fillStyle = '#000'; g.fillRect(0, 0, TEX, TEX);
      if (art) {
        const w = (place.sizeMm[0] / place.mmPerUv) * TEX, hh = (place.sizeMm[1] / place.mmPerUv) * TEX;
        const k = Math.min(w / art.width, hh / art.height), dw = art.width * k, dh = art.height * k;
        // Any opaque pixel (fill, stroke, masked shape) becomes white = engraved.
        const tmp = document.createElement('canvas'); tmp.width = Math.ceil(dw); tmp.height = Math.ceil(dh);
        const t = tmp.getContext('2d');
        t.drawImage(art, 0, 0, tmp.width, tmp.height);
        t.globalCompositeOperation = 'source-in'; t.fillStyle = '#fff'; t.fillRect(0, 0, tmp.width, tmp.height);
        g.save();
        g.translate(place.centerUv[0] * TEX, (1 - place.centerUv[1]) * TEX); // glTF image row = 1 - v
        g.rotate(-place.rotationDeg * Math.PI / 180);
        g.scale(flipU ? -1 : 1, flipV ? -1 : 1);
        g.drawImage(tmp, -dw / 2, -dh / 2, dw, dh);
        g.restore();
      }
      maskTex.needsUpdate = true;
    }
    const setImage = (img) => { art = img; drawMask(); };
    const loadImg = (src) => new Promise((ok, fail) => {
      const i = new Image(); i.onload = () => ok(i); i.onerror = () => fail(new Error('ảnh lỗi')); i.src = src;
    });

    const mat = new THREE.MeshStandardMaterial({
      color: 0x24262a, metalness: 0.35, roughness: 0.7, alphaMap: maskTex, transparent: true,
      depthWrite: false, polygonOffset: true, polygonOffsetFactor: -2, polygonOffsetUnits: -2,
    });
    let overlay = null, ringBox = null, plateBox = null;
    await new Promise((ok, fail) => new THREE.GLTFLoader().parse(glbBuf, '', (gltf) => {
      const root = gltf.scene; scene.add(root);
      const plate = root.getObjectByName('ENGRAVE_SURFACE');
      if (!plate) { fail(new Error('không thấy ENGRAVE_SURFACE')); return; }
      overlay = new THREE.Mesh(plate.geometry, mat); overlay.renderOrder = 2; plate.add(overlay);
      root.updateMatrixWorld(true);
      ringBox = new THREE.Box3().setFromObject(root); plateBox = new THREE.Box3().setFromObject(plate);
      ok();
    }, fail)).catch((e) => { status.textContent = `Lỗi đọc GLB: ${e.message || e}`; throw e; });

    function frame(box, dir, k) {
      const c = box.getCenter(new THREE.Vector3()), s = box.getSize(new THREE.Vector3()).length();
      const ringSize = ringBox.getSize(new THREE.Vector3()).length(); // GLB is in metres (~0.03)
      camera.near = ringSize / 500; camera.far = ringSize * 200; camera.updateProjectionMatrix();
      controls.minDistance = ringSize * 0.05; controls.maxDistance = ringSize * 20;
      camera.position.copy(c).add(dir.clone().normalize().multiplyScalar(s * k));
      controls.target.copy(c); controls.update();
      const x = ringBox.getSize(new THREE.Vector3()).x * (ringSize < 1 ? 1000 : 1);
      status.textContent = `${model} - kéo để xoay, cuộn để phóng to - nhẫn ~ ${x.toFixed(1)} mm`;
    }
    const viewClose = () => frame(plateBox, new THREE.Vector3(0, 1, 0.35), 1.15);
    const viewRing = () => frame(ringBox, new THREE.Vector3(0.6, 0.7, 1), 1.35);
    bClose.onclick = viewClose; bRing.onclick = viewRing;
    for (const b of finishBtns) {
      b.onclick = () => {
        for (const o of finishBtns) o.setAttribute('aria-pressed', String(o === b));
        const v = b.dataset.v; overlay.visible = v !== 'off';
        if (v === 'ox') { mat.color.set(0x24262a); mat.metalness = 0.35; mat.roughness = 0.7; }
        if (v === 'raw') { mat.color.set(0x8d9096); mat.metalness = 0.9; mat.roughness = 0.75; }
        mat.needsUpdate = true;
      };
    }
    const toggle = (b, set) => {
      b.onclick = () => { const on = b.getAttribute('aria-pressed') !== 'true'; b.setAttribute('aria-pressed', String(on)); set(on); drawMask(); };
    };
    toggle(bU, (on) => { flipU = on; }); toggle(bV, (on) => { flipV = on; });

    viewClose(); resize();
    renderer.setAnimationLoop(() => { controls.update(); renderer.render(scene, camera); });
    return {
      async setSvgText(text) {
        const px = text.replace(/width="([\d.]+)mm"/, (_, v) => `width="${Math.round(v * 250)}"`)
          .replace(/height="([\d.]+)mm"/, (_, v) => `height="${Math.round(v * 250)}"`);
        setImage(await loadImg(`data:image/svg+xml;charset=utf-8,${encodeURIComponent(px)}`));
      },
      async setImageUrl(url) { setImage(await loadImg(url)); },
      dispose() { renderer.setAnimationLoop(null); ro.disconnect(); renderer.dispose(); },
    };
  }
  window.Ring3D = { mount };
})();
