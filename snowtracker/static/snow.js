// Snowfall color layer + plow "eraser".
// Loaded after the main page script; uses its globals `map`, `SCALES`, `COLORS`.
//
// 1. Each grid cell's 24h snowfall becomes one pixel of a small image, colored on
//    the same scale as the color key. The browser smooths it when stretching it
//    over the map, which gives a soft gradient between cells.
// 2. Plowed stretches (from /api/plowed) are cut out of that image, then redrawn
//    with the color of the snow that has fallen since the plow passed, so they
//    fill back in as it keeps snowing.
(() => {
  const METRIC = 'snowfall_24h_in';
  const OPACITY = 0.6;          // how strongly the snow colors cover the base map
  const ROAD_METERS = 12;       // plowed width when zoomed in
  const MIN_ROAD_PX = 3;        // ...and never thinner than this
  const GRID_REFRESH_MS = 5 * 60 * 1000;
  const PLOWED_REFRESH_MS = 60 * 1000;

  // ---- colors: continuous gradient through the color key's stops ----
  const hex = h => [1, 3, 5].map(i => parseInt(h.slice(i, i + 2), 16));
  const STOPS = SCALES[METRIC].map((v, i) => [v, hex(COLORS[i])]);

  function rgbFor(v) {               // null = no snow (transparent)
    if (v == null || v < STOPS[0][0]) return null;
    for (let i = 0; i < STOPS.length - 1; i++) {
      const [v0, c0] = STOPS[i], [v1, c1] = STOPS[i + 1];
      if (v < v1) {
        const f = (v - v0) / (v1 - v0);
        return c0.map((c, k) => Math.round(c + (c1[k] - c) * f));
      }
    }
    return STOPS[STOPS.length - 1][1];
  }

  // ---- state ----
  let grid = null;      // { img: canvas, south, north, west, east }
  let plowed = [];      // [lat1, lon1, lat2, lon2, since_in, base_in, plowed_epoch]

  function buildGrid(data) {
    const g = data.grid_deg;
    const cells = data.cells.filter(c => c[2] != null);
    if (!cells.length) return null;
    const idx = c => [Math.round(c[0] / g - 0.5), Math.round(c[1] / g - 0.5)];
    let iMin = Infinity, iMax = -Infinity, jMin = Infinity, jMax = -Infinity;
    for (const c of cells) {
      const [i, j] = idx(c);
      iMin = Math.min(iMin, i); iMax = Math.max(iMax, i);
      jMin = Math.min(jMin, j); jMax = Math.max(jMax, j);
    }
    const w = jMax - jMin + 1, h = iMax - iMin + 1;
    // values[y][x], row 0 = northernmost; null = no data
    let values = Array.from({ length: h }, () => new Array(w).fill(null));
    for (const c of cells) {
      const [i, j] = idx(c);
      values[iMax - i][j - jMin] = c[2];
    }
    // Only cells containing a zip code have data, which leaves holes inland.
    // Fill holes inward from their edges, a ring per pass; a cell needs 4+ neighbours
    // with data, which closes inland gaps but doesn't spread along open lake shores.
    for (let pass = 0; pass < 5; pass++) {
      const next = values.map(r => r.slice());
      for (let y = 0; y < h; y++) for (let x = 0; x < w; x++) {
        if (values[y][x] != null) continue;
        let sum = 0, n = 0;
        for (let dy = -1; dy <= 1; dy++) for (let dx = -1; dx <= 1; dx++) {
          const v = values[y + dy]?.[x + dx];
          if ((dx || dy) && v != null) { sum += v; n++; }
        }
        if (n >= 4) next[y][x] = sum / n;
      }
      values = next;
    }

    const img = document.createElement('canvas');
    img.width = w; img.height = h;
    const ctx = img.getContext('2d');
    const px = ctx.createImageData(w, h);
    for (let y = 0; y < h; y++) for (let x = 0; x < w; x++) {
      const rgb = rgbFor(values[y][x]);
      if (!rgb) continue;
      const o = (y * w + x) * 4;
      px.data[o] = rgb[0]; px.data[o + 1] = rgb[1]; px.data[o + 2] = rgb[2]; px.data[o + 3] = 255;
    }
    ctx.putImageData(px, 0, 0);
    return { img, south: iMin * g, north: (iMax + 1) * g, west: jMin * g, east: (jMax + 1) * g };
  }

  // ---- the Leaflet layer ----
  map.createPane('snow');
  map.getPane('snow').style.zIndex = 350;          // under the zip dots and trucks
  map.getPane('snow').style.pointerEvents = 'none';

  const SnowLayer = L.Layer.extend({
    onAdd(m) {
      this._canvas = L.DomUtil.create('canvas', 'leaflet-zoom-hide');
      this._canvas.style.opacity = OPACITY;
      m.getPane('snow').appendChild(this._canvas);
      m.on('moveend zoomend resize viewreset', this._reset, this);
      this._reset();
    },
    onRemove(m) {
      L.DomUtil.remove(this._canvas);
      m.off('moveend zoomend resize viewreset', this._reset, this);
    },
    _reset() {
      const size = map.getSize();
      const pad = size.multiplyBy(0.5);              // extra edge so panning doesn't show gaps
      const full = size.add(pad.multiplyBy(2));
      const topLeft = map.containerPointToLayerPoint(pad.multiplyBy(-1));
      L.DomUtil.setPosition(this._canvas, topLeft);
      const dpr = window.devicePixelRatio || 1;
      this._canvas.width = full.x * dpr; this._canvas.height = full.y * dpr;
      this._canvas.style.width = full.x + 'px'; this._canvas.style.height = full.y + 'px';
      this._pad = pad; this._dpr = dpr;
      this.redraw();
    },
    redraw() {
      if (!this._canvas) return;
      const ctx = this._canvas.getContext('2d');
      const dpr = this._dpr, pad = this._pad;
      ctx.setTransform(dpr, 0, 0, dpr, pad.x * dpr, pad.y * dpr);   // container coords
      ctx.clearRect(-pad.x, -pad.y, this._canvas.width / dpr, this._canvas.height / dpr);
      if (!grid) return;

      // 1. snow gradient
      const nw = map.latLngToContainerPoint([grid.north, grid.west]);
      const se = map.latLngToContainerPoint([grid.south, grid.east]);
      ctx.imageSmoothingEnabled = true;
      ctx.imageSmoothingQuality = 'high';
      ctx.drawImage(grid.img, nw.x, nw.y, se.x - nw.x, se.y - nw.y);
      if (!plowed.length) return;

      // 2. erase plowed stretches, then 3. paint back what has fallen since
      const z = map.getZoom();
      const metersPerPx = lat => 40075016.686 * Math.cos(lat * Math.PI / 180) / (256 * 2 ** z);
      ctx.lineCap = 'round';
      const segs = plowed.map(s => ({
        a: map.latLngToContainerPoint([s[0], s[1]]),
        b: map.latLngToContainerPoint([s[2], s[3]]),
        width: Math.max(MIN_ROAD_PX, ROAD_METERS / metersPerPx(s[0])),
        rgb: rgbFor(s[4]),
      }));
      ctx.globalCompositeOperation = 'destination-out';
      for (const s of segs) stroke(ctx, s, 'rgba(0,0,0,1)');
      ctx.globalCompositeOperation = 'source-over';
      for (const s of segs) if (s.rgb) stroke(ctx, s, `rgb(${s.rgb.join(',')})`);
    },
  });

  function stroke(ctx, s, color) {
    ctx.strokeStyle = color;
    ctx.lineWidth = s.width;
    ctx.beginPath(); ctx.moveTo(s.a.x, s.a.y); ctx.lineTo(s.b.x, s.b.y); ctx.stroke();
  }

  const layer = new SnowLayer().addTo(map);

  async function refreshGrid() {
    try {
      const res = await fetch('/api/snowgrid');
      if (res.ok) { grid = buildGrid(await res.json()); layer.redraw(); }
    } catch (_) { /* keep what's drawn */ }
  }
  async function refreshPlowed() {
    try {
      const res = await fetch('/api/plowed');
      if (res.ok) { plowed = await res.json(); layer.redraw(); }
    } catch (_) { /* keep what's drawn */ }
  }

  refreshGrid(); refreshPlowed();
  setInterval(refreshGrid, GRID_REFRESH_MS);
  setInterval(refreshPlowed, PLOWED_REFRESH_MS);
  // the main page calls this when new snow data arrives
  window.snowLayer = { refresh: () => { refreshGrid(); refreshPlowed(); }, layer };
})();
