(() => {
  const cfg = window.LIVE_CFG || {};
  const BLANK = 'data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7';
  const $ = id => document.getElementById(id);

  // ---------------------------------------------------------------- map
  const map = L.map('map', {maxZoom: 22}).setView([22.5, 80], 5);
  const sat = L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',
    {maxZoom: 22, maxNativeZoom: 19, attribution: 'Imagery &copy; Esri'}).addTo(map);
  const streets = L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png',
    {maxZoom: 22, maxNativeZoom: 19, attribution: '&copy; OpenStreetMap contributors'});
  const overlays = {};
  if (cfg.tiles_url) {
    overlays['Orthomosaic'] = L.tileLayer(cfg.tiles_url, {
      minZoom: cfg.min_zoom, maxNativeZoom: cfg.max_zoom, maxZoom: 22, errorTileUrl: BLANK
    }).addTo(map);
    map.fitBounds(cfg.bounds);
  }
  L.control.layers({'Satellite': sat, 'Streets': streets}, overlays).addTo(map);
  L.control.scale({imperial: false}).addTo(map);

  const trail = L.polyline([], {color: '#e4570f', weight: 3, opacity: .9}).addTo(map);
  const iconHtml = '<div class="drone-icon" id="drone-icon"><svg viewBox="0 0 34 34">' +
    '<path d="M17 3 L28 29 L17 24 L6 29 Z" fill="#e4570f" stroke="#fff" stroke-width="2" stroke-linejoin="round"/></svg></div>';
  const marker = L.marker([0, 0], {
    icon: L.divIcon({className: '', html: iconHtml, iconSize: [34, 34], iconAnchor: [17, 17]}),
    zIndexOffset: 1000
  });
  let markerOnMap = false;
  let firstFix = true;

  // ---------------------------------------------------------------- state
  let run = null, since = 0, gpsWatch = null, lastPos = null;

  const statusLabels = {
    disconnected: 'Disconnected', connecting: 'Connecting', waiting: 'Waiting',
    live: 'Live', stale: 'No signal', error: 'Error'
  };

  function fmt(v, d) { return (v === undefined || v === null) ? '-' : Number(v).toFixed(d); }

  function setStatus(status, message) {
    const el = $('status');
    el.className = 'badge ' + status;
    el.textContent = statusLabels[status] || status;
    $('message').textContent = message || '';
  }

  function showTelemetry(t, snap) {
    const set = (f, text) => { const el = document.querySelector('[data-f="' + f + '"]'); if (el) el.textContent = text; };
    set('lat', fmt(t.lat, 6));
    set('lon', fmt(t.lon, 6));
    set('rel_alt', fmt(t.rel_alt, 1));
    set('alt', fmt(t.alt, 1));
    set('speed', t.speed === undefined ? '-' : t.speed.toFixed(1) + ' m/s (' + (t.speed * 3.6).toFixed(0) + ' km/h)');
    set('heading', t.heading === undefined ? '-' : Math.round(t.heading) + '\u00b0');
    let batt = t.battery === undefined ? '-' : Math.round(t.battery) + '%';
    if (t.voltage !== undefined) batt += ' / ' + t.voltage.toFixed(1) + ' V';
    set('battery', batt);
    set('sats', t.sats === undefined ? '-' : String(t.sats));
    set('mode', (t.mode || '-') + (t.armed === true ? ' (armed)' : ''));
    $('distance').textContent = snap.distance ? snap.distance.toFixed(0) + ' m' : '-';
  }

  async function poll() {
    try {
      const res = await fetch('/api/drone/telemetry?since=' + since);
      if (res.status === 401 || res.redirected) { location.reload(); return; }
      const snap = await res.json();

      if (snap.run !== run) {          // a new connection started: reset the path
        run = snap.run; since = 0; firstFix = true;
        trail.setLatLngs([]);
        if (snap.seq > 0) { const again = await (await fetch('/api/drone/telemetry?since=0')).json(); Object.assign(snap, {points: again.points}); }
      }

      setStatus(snap.status, snap.message);

      snap.points.forEach(p => trail.addLatLng([p[1], p[2]]));
      if (snap.points.length) since = snap.points[snap.points.length - 1][0];

      const t = snap.telemetry;
      showTelemetry(t, snap);

      if (t.lat !== undefined && t.lon !== undefined) {
        const pos = [t.lat, t.lon];
        marker.setLatLng(pos);
        if (!markerOnMap) { marker.addTo(map); markerOnMap = true; }
        const icon = $('drone-icon');
        if (icon) {
          icon.style.transform = 'rotate(' + (t.heading || 0) + 'deg)';
          icon.classList.toggle('stale', snap.status === 'stale');
        }
        if (firstFix) { map.setView(pos, Math.max(map.getZoom(), 17)); firstFix = false; }
        else if ($('follow').checked && snap.status === 'live') map.panTo(pos, {animate: true});
      }
    } catch (err) {
      setStatus('error', 'Lost connection to the server.');
    }
  }

  // ---------------------------------------------------------------- browser GPS
  function startBrowserGps() {
    if (!navigator.geolocation) { setStatus('error', 'This browser has no geolocation support.'); return; }
    gpsWatch = navigator.geolocation.watchPosition(pos => {
      const c = pos.coords;
      const body = {lat: c.latitude, lon: c.longitude};
      if (c.altitude !== null) body.alt = c.altitude;
      if (c.heading !== null && !isNaN(c.heading)) body.heading = c.heading;
      if (c.speed !== null) body.speed = Math.max(0, c.speed);
      fetch('/api/drone/push', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)});
    }, err => {
      setStatus('error', 'Location error: ' + err.message + '. Phones need HTTPS (or localhost) for GPS.');
    }, {enableHighAccuracy: true, maximumAge: 1000, timeout: 15000});
  }

  function stopBrowserGps() {
    if (gpsWatch !== null) { navigator.geolocation.clearWatch(gpsWatch); gpsWatch = null; }
  }

  // ---------------------------------------------------------------- controls
  function updatePanels() {
    ['simulator', 'mavlink', 'browser', 'http'].forEach(s =>
      $('panel-' + s).classList.toggle('hidden', $('source').value !== s));
  }
  $('source').addEventListener('change', updatePanels);
  updatePanels();

  $('project-select').addEventListener('change', e => {
    location.href = e.target.value ? '/live?project=' + e.target.value : '/live';
  });

  $('btn-connect').addEventListener('click', async () => {
    const source = $('source').value;
    stopBrowserGps();
    const c = map.getCenter();
    const res = await fetch('/api/drone/connect', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({source, connection: $('connection').value, lat: c.lat, lon: c.lng})
    });
    const out = await res.json();
    if (!out.ok) { setStatus('error', out.message); return; }
    if (source === 'browser') startBrowserGps();
    if (source === 'simulator' && map.getZoom() < 16) map.setZoom(17);
    poll();
  });

  $('btn-disconnect').addEventListener('click', async () => {
    stopBrowserGps();
    await fetch('/api/drone/disconnect', {method: 'POST'});
    poll();
  });

  $('btn-clear').addEventListener('click', () => trail.setLatLngs([]));

  poll();
  setInterval(poll, 1000);
})();
