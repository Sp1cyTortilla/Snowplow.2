// Active snow plow trucks on the map.
// Loaded after the main page script; uses its global `map` (Leaflet).
// Polls /api/trucks and draws one arrow per truck, pointing the way it's heading.
(() => {
  const POLL_MS = 30 * 1000;
  const STALE_MIN = 30;   // trucks that haven't reported in this long are drawn faded

  const truckLayer = L.layerGroup().addTo(map);
  const trucks = new Map();   // key "STATE:id" -> L.marker

  const style = document.createElement('style');
  style.textContent = `
    .truck { width: 22px; height: 22px; }
    .truck svg { width: 22px; height: 22px; display: block; transition: transform .4s; }
    .truck.stale { opacity: .45; }
  `;
  document.head.appendChild(style);

  // Orange plow arrow; a plain circle when the feed gives no heading.
  function iconFor(t) {
    const hasHeading = t.heading != null;
    const shape = hasHeading
      ? '<path d="M11 2 L19 19 L11 15 L3 19 Z" fill="#f97316" stroke="#7c2d12" stroke-width="1.5" stroke-linejoin="round"/>'
      : '<circle cx="11" cy="11" r="7" fill="#f97316" stroke="#7c2d12" stroke-width="1.5"/>';
    const rot = hasHeading ? `style="transform: rotate(${t.heading}deg)"` : '';
    return L.divIcon({
      className: 'truck' + (isStale(t) ? ' stale' : ''),
      html: `<svg viewBox="0 0 22 22" ${rot} aria-hidden="true">${shape}</svg>`,
      iconSize: [22, 22], iconAnchor: [11, 11],
    });
  }

  function parseTime(v) {
    if (v == null) return null;
    if (typeof v === 'number') return new Date(v < 1e12 ? v * 1000 : v);   // seconds or ms
    const d = new Date(v);
    return isNaN(d) ? null : d;
  }

  function isStale(t) {
    const d = parseTime(t.updated);
    return d ? (Date.now() - d) / 60000 > STALE_MIN : false;
  }

  function label(t, state) {
    const d = parseTime(t.updated);
    const parts = [`Plow ${t.id}`, state];
    if (t.speed != null) parts.push(`${Math.round(t.speed)} mph`);
    if (d) parts.push(`seen ${d.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' })}`);
    return parts.join(' · ');
  }

  async function refresh() {
    let states;
    try {
      const res = await fetch('/api/trucks');
      if (!res.ok) return;
      states = await res.json();
    } catch (_) { return; }   // offline: keep what's drawn

    const seen = new Set();
    for (const s of states) {
      for (const t of s.trucks || []) {
        const key = `${s.state}:${t.id}`;
        seen.add(key);
        let m = trucks.get(key);
        if (!m) {
          m = L.marker([t.lat, t.lon], { icon: iconFor(t), keyboard: false, zIndexOffset: 1000 })
            .addTo(truckLayer);
          trucks.set(key, m);
        } else {
          m.setLatLng([t.lat, t.lon]);
          m.setIcon(iconFor(t));
        }
        m.bindTooltip(label(t, s.state));
      }
    }
    // remove trucks that are no longer in the feed
    for (const [key, m] of trucks) {
      if (!seen.has(key)) { truckLayer.removeLayer(m); trucks.delete(key); }
    }
  }

  refresh();
  setInterval(refresh, POLL_MS);
  window.plowTrucks = { refresh, layer: truckLayer };   // handy from the console
})();
