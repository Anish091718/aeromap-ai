(() => {
  const canvas = document.getElementById('view3d');
  const ctx = canvas.getContext('2d');
  const DEFAULT = {yaw: -0.6, elev: 0.9, zoom: 1};
  let yaw = DEFAULT.yaw, elev = DEFAULT.elev, zoom = DEFAULT.zoom;
  let pts = [];            // local metres [x east, y north, z up]
  let origin = null, since = 0, run = null, status = '-', distance = 0;

  function resize() {
    const r = canvas.getBoundingClientRect(), dpr = window.devicePixelRatio || 1;
    canvas.width = r.width * dpr; canvas.height = r.height * dpr;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    draw();
  }
  window.addEventListener('resize', resize);

  function toLocal(p) {   // p = [seq, lat, lon, height]
    if (!origin) origin = {lat: p[1], lon: p[2]};
    return [
      (p[2] - origin.lon) * 111320 * Math.cos(origin.lat * Math.PI / 180),
      (p[1] - origin.lat) * 111320,
      p[3]
    ];
  }

  function project(p, scale, w, h) {
    const cy = Math.cos(yaw), sy = Math.sin(yaw);
    const x = p[0] * cy - p[1] * sy, y = p[0] * sy + p[1] * cy;
    const v = y * Math.sin(elev) + p[2] * Math.cos(elev);
    return [w / 2 + x * scale, h * 0.55 - v * scale];
  }

  function draw() {
    const w = canvas.getBoundingClientRect().width, h = canvas.getBoundingClientRect().height;
    ctx.clearRect(0, 0, w, h);

    let ext = 30;
    pts.forEach(p => { ext = Math.max(ext, Math.abs(p[0]), Math.abs(p[1]), p[2]); });
    ext = Math.ceil(ext / 10) * 10;
    const scale = 0.42 * Math.min(w, h) / ext * zoom;

    // ground grid, every 10 m
    ctx.strokeStyle = 'rgba(255,255,255,0.12)'; ctx.lineWidth = 1;
    for (let g = -ext; g <= ext; g += 10) {
      let a = project([g, -ext, 0], scale, w, h), b = project([g, ext, 0], scale, w, h);
      ctx.beginPath(); ctx.moveTo(a[0], a[1]); ctx.lineTo(b[0], b[1]); ctx.stroke();
      a = project([-ext, g, 0], scale, w, h); b = project([ext, g, 0], scale, w, h);
      ctx.beginPath(); ctx.moveTo(a[0], a[1]); ctx.lineTo(b[0], b[1]); ctx.stroke();
    }

    // north marker
    const n0 = project([0, 0, 0], scale, w, h), n1 = project([0, ext * 0.25, 0], scale, w, h);
    ctx.strokeStyle = '#7fd1a0'; ctx.beginPath(); ctx.moveTo(n0[0], n0[1]); ctx.lineTo(n1[0], n1[1]); ctx.stroke();
    ctx.fillStyle = '#7fd1a0'; ctx.font = '12px sans-serif'; ctx.fillText('N', n1[0] + 4, n1[1]);

    if (!pts.length) {
      ctx.fillStyle = '#9fb3ab'; ctx.font = '14px sans-serif';
      ctx.fillText('No flight data yet. Start the simulator or connect a drone on the Live page.', 20, 30);
      return;
    }

    // path on the ground (shadow) and in the air
    ctx.lineWidth = 1; ctx.strokeStyle = 'rgba(255,255,255,0.25)'; ctx.beginPath();
    pts.forEach((p, i) => { const s = project([p[0], p[1], 0], scale, w, h); i ? ctx.lineTo(s[0], s[1]) : ctx.moveTo(s[0], s[1]); });
    ctx.stroke();

    ctx.lineWidth = 2.5; ctx.strokeStyle = '#e4570f'; ctx.beginPath();
    pts.forEach((p, i) => { const s = project(p, scale, w, h); i ? ctx.lineTo(s[0], s[1]) : ctx.moveTo(s[0], s[1]); });
    ctx.stroke();

    // current position with drop line
    const last = pts[pts.length - 1];
    const top = project(last, scale, w, h), base = project([last[0], last[1], 0], scale, w, h);
    ctx.strokeStyle = '#ffb48a'; ctx.setLineDash([4, 4]); ctx.beginPath();
    ctx.moveTo(top[0], top[1]); ctx.lineTo(base[0], base[1]); ctx.stroke(); ctx.setLineDash([]);
    ctx.fillStyle = '#e4570f'; ctx.beginPath(); ctx.arc(top[0], top[1], 6, 0, Math.PI * 2); ctx.fill();
    ctx.strokeStyle = '#fff'; ctx.lineWidth = 2; ctx.stroke();
  }

  async function poll() {
    try {
      const snap = await (await fetch('/api/drone/telemetry?since=' + since)).json();
      if (snap.run !== run) {
        run = snap.run; since = 0; pts = []; origin = null;
        if (snap.seq > 0) snap.points = (await (await fetch('/api/drone/telemetry?since=0')).json()).points;
      }
      snap.points.forEach(p => pts.push(toLocal(p)));
      if (snap.points.length) since = snap.points[snap.points.length - 1][0];
      status = snap.status; distance = snap.distance;
      const t = snap.telemetry;
      document.getElementById('st').textContent = status;
      document.getElementById('pts').textContent = pts.length;
      document.getElementById('h').textContent = t.rel_alt !== undefined ? t.rel_alt.toFixed(1) : '-';
      document.getElementById('dist').textContent = distance ? distance.toFixed(0) : '-';
      draw();
    } catch (e) { /* server unreachable; try again next tick */ }
  }

  // mouse / touch rotation
  let drag = null;
  canvas.addEventListener('pointerdown', e => { drag = {x: e.clientX, y: e.clientY}; canvas.setPointerCapture(e.pointerId); canvas.style.cursor = 'grabbing'; });
  canvas.addEventListener('pointerup', () => { drag = null; canvas.style.cursor = 'grab'; });
  canvas.addEventListener('pointermove', e => {
    if (!drag) return;
    yaw += (e.clientX - drag.x) * 0.01;
    elev = Math.min(1.5, Math.max(0.05, elev + (e.clientY - drag.y) * 0.01));
    drag = {x: e.clientX, y: e.clientY};
    draw();
  });
  canvas.addEventListener('wheel', e => {
    e.preventDefault();
    zoom = Math.min(8, Math.max(0.3, zoom * (e.deltaY < 0 ? 1.1 : 0.9)));
    draw();
  }, {passive: false});
  document.getElementById('reset-view').addEventListener('click', () => { yaw = DEFAULT.yaw; elev = DEFAULT.elev; zoom = DEFAULT.zoom; draw(); });

  resize();
  poll();
  setInterval(poll, 1000);
})();
