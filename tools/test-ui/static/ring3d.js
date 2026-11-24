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

  // [colour, extra roughness, label]
  const METALS = {
    SILVER: [0xdcdde0, 0, 'Bạc 925'],
    GOLD_18K: [0xe5b95c, 0, 'Vàng 18K'],
    GOLD_14K: [0xd9b06a, 0.02, 'Vàng 14K'],
    ROSE_GOLD: [0xe6a68c, 0, 'Vàng hồng'],
    WHITE_GOLD: [0xe9e6dd, 0, 'Vàng trắng'],
    PLATINUM: [0xcfd1d4, 0.05, 'Bạch kim'],
    TITANIUM_BLACK: [0x3b3c40, 0.1, 'Titan đen'],
  };
  // [colour, label]
  const GEMS = {
    DIAMOND: [0xffffff, 'Kim cương / CZ trắng'],
    RUBY: [0xc8102e, 'Ruby (đỏ)'],
    SAPPHIRE: [0x1f4fb8, 'Sapphire (xanh dương)'],
    EMERALD: [0x0f8a4b, 'Ngọc lục bảo (xanh lá)'],
    AMETHYST: [0x8e4fc9, 'Thạch anh tím'],
    TOPAZ: [0xf2b632, 'Topaz vàng'],
    PINK: [0xf08ab8, 'Đá hồng'],
    NONE: [0, 'Không gắn đá'],
  };

  async function mount(host, opts) {
    const { model, kind } = opts;
    const status = h('div', { class: 'r3d-status' }, 'Đang tải three.js và mô hình...');
    const stage = h('div', { class: 'r3d-stage' }, status);
    const btn = (label, extra = {}) => h('button', { type: 'button', class: 'secondary', ...extra }, label);
    const finishBtns = [['ox', 'Oxi hóa'], ['raw', 'Khắc mờ'], ['deep', 'Khắc sâu'], ['off', 'Ẩn mã']]
      .map(([v, t]) => btn(t, { 'data-v': v, 'aria-pressed': String(v === 'ox') }));
    const bClose = btn('Cận mặt khắc'), bRing = btn('Toàn nhẫn');
    const bU = btn('Lật ngang', { 'aria-pressed': 'false' }), bV = btn('Lật dọc', { 'aria-pressed': 'false' });
    host.replaceChildren(stage, h('div', { class: 'row' }, ...finishBtns, bClose, bRing, bU, bV));

    let tpl, glbBuf;
    try {
      await loadThree();
      // opts.glbBuffer / opts.template: a model uploaded from the machine (template falls back to `model`'s).
      const [t, g] = await Promise.all([
        opts.template ? Promise.resolve(opts.template)
          : fetch(`/models/${model}/template.json`).then((r) => { if (!r.ok) throw new Error(`template ${r.status}`); return r.json(); }),
        opts.glbBuffer ? Promise.resolve(opts.glbBuffer)
          : fetch(`/models/${model}.glb`).then((r) => { if (!r.ok) throw new Error(`glb ${r.status}`); return r.arrayBuffer(); }),
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
    const key = new THREE.DirectionalLight(0xffffff, 1.6); key.position.set(-1, 2, 1.2); scene.add(key);
    camera.add(new THREE.DirectionalLight(0xffffff, 0.5)); scene.add(camera); // soft head light follows the view
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
      buildEngraveMaps();
    }

    // Engraving look from the mask (white = engraved): relief (bump), matte groove floor (roughness),
    // darker groove (colour). Fingerprint ridges are fine, so they get less blur than the wavecode.
    const FINISH = {
      ox: { floor: 40, rough: 0.6, bump: 14, shade: 0.7, wall: 2 },    // oxidised: dark floor
      raw: { floor: 200, rough: 0.85, bump: 14, shade: 0.55, wall: 2 }, // frosted: metal colour, matte
      deep: { floor: 140, rough: 0.7, bump: 32, shade: 0.9, wall: 3.5 }, // deep cut: strong relief, wide shadowed wall
    };
    let finish = 'ox', baseRough = 0.2;
    const blurPx = kind === 'FINGERPRINT' ? 1.5 : 3;
    const mkCanvasTex = () => {
      const c = document.createElement('canvas'); c.width = c.height = TEX;
      const t = new THREE.CanvasTexture(c); t.flipY = false; t.anisotropy = renderer.capabilities.getMaxAnisotropy();
      return [c, t];
    };
    const [bumpCanvas, bumpTex] = mkCanvasTex();
    const [roughCanvas, roughTex] = mkCanvasTex();
    const [colorCanvas, colorTex] = mkCanvasTex();
    const [alphaCanvas, alphaTex] = mkCanvasTex();
    colorTex.encoding = THREE.sRGBEncoding;
    // paint(canvas, value outside the engraving, value inside, blur): grey levels 0..255
    function paint(c, outside, inside, blur) {
      const g = c.getContext('2d');
      g.filter = 'none'; g.globalCompositeOperation = 'source-over';
      g.fillStyle = `rgb(${outside},${outside},${outside})`; g.fillRect(0, 0, TEX, TEX);
      // tint the white mask to the inside value, then draw it (blurred) over the outside value
      const tmp = document.createElement('canvas'); tmp.width = tmp.height = TEX;
      const t = tmp.getContext('2d');
      t.drawImage(maskCanvas, 0, 0);
      t.globalCompositeOperation = 'multiply'; t.fillStyle = `rgb(${inside},${inside},${inside})`; t.fillRect(0, 0, TEX, TEX);
      t.globalCompositeOperation = 'destination-in'; t.drawImage(maskCanvas, 0, 0); // keep only engraved pixels
      if (inside > outside) { g.filter = blur ? `blur(${blur}px)` : 'none'; g.drawImage(tmp, 0, 0); }
      else {
        // darker inside: lighten-safe path, subtract via 'darken'
        g.filter = blur ? `blur(${blur}px)` : 'none'; g.globalCompositeOperation = 'darken';
        const inv = document.createElement('canvas'); inv.width = inv.height = TEX;
        const ig = inv.getContext('2d');
        ig.fillStyle = `rgb(${outside},${outside},${outside})`; ig.fillRect(0, 0, TEX, TEX);
        ig.drawImage(tmp, 0, 0);
        g.drawImage(inv, 0, 0);
      }
      g.filter = 'none'; g.globalCompositeOperation = 'source-over';
    }
    function buildEngraveMaps() {
      const f = FINISH[finish] || FINISH.ox;
      paint(bumpCanvas, 255, 0, blurPx * f.wall * 0.6);                      // wider slope = wider lit/shaded bevel                                   // high surface, low groove
      paint(roughCanvas, Math.round(f.rough * 255), Math.round(f.rough * 255), 0);
      paint(colorCanvas, f.floor, f.floor, 0);
      {
        const g = colorCanvas.getContext('2d'), sh = Math.max(2, blurPx * 1.5 * f.wall);
        const edge = document.createElement('canvas'); edge.width = edge.height = TEX;
        const e = edge.getContext('2d');
        e.drawImage(maskCanvas, 0, 0);
        e.globalCompositeOperation = 'destination-out'; e.drawImage(maskCanvas, sh, sh); // wall in shadow
        g.globalAlpha = f.shade; g.globalCompositeOperation = 'multiply';
        g.filter = `blur(${blurPx * 0.5}px) invert(1)`; g.drawImage(edge, 0, 0);
        g.filter = 'none'; g.globalAlpha = 1; g.globalCompositeOperation = 'source-over';
      }
      // Only the groove (plus a soft rim for the bevel highlight) is drawn; the rest shows the plate's own metal.
      paint(alphaCanvas, 0, 255, blurPx * 1.5);
      bumpTex.needsUpdate = roughTex.needsUpdate = colorTex.needsUpdate = alphaTex.needsUpdate = true;
      if (typeof mat !== 'undefined') { mat.bumpScale = f.bump; mat.needsUpdate = true; }
    }
    const setImage = (img) => { art = img; drawMask(); };
    const loadImg = (src) => new Promise((ok, fail) => {
      const i = new Image(); i.onload = () => ok(i); i.onerror = () => fail(new Error('ảnh lỗi')); i.src = src;
    });

    // Opaque metal skin over ENGRAVE_SURFACE carrying the engraving maps (same metal as the ring).
    const mat = new THREE.MeshStandardMaterial({
      color: 0xdcdde0, metalness: 1, roughness: 1, map: colorTex, roughnessMap: roughTex,
      bumpMap: bumpTex, bumpScale: FINISH.ox.bump, alphaMap: alphaTex, transparent: true, depthWrite: false,
      polygonOffset: true, polygonOffsetFactor: -2, polygonOffsetUnits: -2,
    });
    let overlay = null, ringBox = null, plateBox = null;
    await new Promise((ok, fail) => new THREE.GLTFLoader().parse(glbBuf, '', (gltf) => {
      const root = gltf.scene; scene.add(root);
      const plate = root.getObjectByName('ENGRAVE_SURFACE');
      if (!plate) { fail(new Error('không thấy ENGRAVE_SURFACE')); return; }
      const pm = [].concat(plate.material)[0];
      if (pm && typeof pm.roughness === 'number') baseRough = pm.roughness;
      overlay = new THREE.Mesh(plate.geometry, mat); overlay.renderOrder = 2; plate.add(overlay);
      buildEngraveMaps();
      setupLooks(root);
      root.updateMatrixWorld(true);
      ringBox = new THREE.Box3().setFromObject(root); plateBox = new THREE.Box3().setFromObject(plate);
      ok();
    }, fail)).catch((e) => { status.textContent = `Lỗi đọc GLB: ${e.message || e}`; throw e; });

    // Ring metal (MAT_SILVER_* materials) and gemstones (GEMSTONE_* meshes) chosen in the viewer.
    function setupLooks(root) {
      const metals = [], gems = [];
      root.traverse((o) => {
        if (!o.isMesh || o === overlay) return;
        if (/^GEMSTONE_/.test(o.name)) { gems.push(o); return; }
        for (const m of [].concat(o.material)) {
          if (m && /^MAT_SILVER/.test(m.name) && !metals.includes(m)) { m.userData.baseRough = m.roughness; metals.push(m); }
        }
      });
      const applyMetal = (key) => {
        const [color, rough] = METALS[key];
        for (const m of metals) { m.color.set(color); m.metalness = 1; m.roughness = Math.min(1, m.userData.baseRough + rough); m.needsUpdate = true; }
        mat.color.set(color); mat.needsUpdate = true; // engraved skin follows the metal
      };
      const applyGem = (key) => {
        for (const g of gems) {
          g.visible = key !== 'NONE';
          if (!g.visible) continue;
          if (!g.userData.gemMat) {
            g.userData.gemMat = new THREE.MeshPhysicalMaterial({ metalness: 0, roughness: 0.03, transmission: 0.55, ior: 2.0, thickness: 0.002, clearcoat: 1, envMapIntensity: 2.2 });
            g.material = g.userData.gemMat;
          }
          g.userData.gemMat.color.set(GEMS[key][0]); g.userData.gemMat.needsUpdate = true;
        }
      };
      const sel = (opts, onChange) => {
        const x = h('select', {}, ...Object.entries(opts).map(([k, v]) => h('option', { value: k }, v[1] || v.label)));
        x.onchange = () => onChange(x.value); return x;
      };
      const metalSel = sel(Object.fromEntries(Object.entries(METALS).map(([k, v]) => [k, { label: v[2] }])), applyMetal);
      const gemSel = sel(GEMS, applyGem);
      host.append(h('div', { class: 'row' }, h('span', { class: 'muted' }, 'Chất liệu'), metalSel,
        h('span', { class: 'muted' }, gems.length ? `Đá (${gems.length} viên)` : 'Đá (model không có)'), gemSel));
      if (!gems.length) gemSel.disabled = true;
      applyMetal(metalSel.value); applyGem(gemSel.value);
    }

    function frame(box, dir, k) {
      const c = box.getCenter(new THREE.Vector3()), s = box.getSize(new THREE.Vector3()).length();
      const ringSize = ringBox.getSize(new THREE.Vector3()).length(); // GLB is in metres (~0.03)
      camera.near = ringSize / 500; camera.far = ringSize * 200; camera.updateProjectionMatrix();
      controls.minDistance = ringSize * 0.05; controls.maxDistance = ringSize * 20;
      camera.position.copy(c).add(dir.clone().normalize().multiplyScalar(s * k));
      controls.target.copy(c); controls.update();
      const x = ringBox.getSize(new THREE.Vector3()).x * (ringSize < 1 ? 1000 : 1);
      status.textContent = `${opts.label || model} - kéo để xoay, cuộn để phóng to - nhẫn ~ ${x.toFixed(1)} mm`;
    }
    const viewClose = () => frame(plateBox, new THREE.Vector3(0, 1, 0.35), 1.15);
    const viewRing = () => frame(ringBox, new THREE.Vector3(0.6, 0.7, 1), 1.35);
    bClose.onclick = viewClose; bRing.onclick = viewRing;
    for (const b of finishBtns) {
      b.onclick = () => {
        for (const o of finishBtns) o.setAttribute('aria-pressed', String(o === b));
        const v = b.dataset.v; overlay.visible = v !== 'off';
        if (v !== 'off') { finish = v; buildEngraveMaps(); }
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
