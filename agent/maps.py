"""Self-contained Leaflet HTML maps of one municipality."""

from __future__ import annotations

import datetime as dt
import json
import re
from pathlib import Path

from agent.analysis import NETWORKS, _municipality, _network, _radius
from pipeline.common import ROOT

OUTPUT_DIR = ROOT / "outputs"
NETWORK_COLOURS = {"Instabee": "#e4002b", "PostNord": "#00a0d6", "Bring": "#7ab929", "DHL": "#f2b705",
                   "iBoxen": "#8e44ad", "Paketbox": "#555555", "DB Schenker": "#333333", "Early Bird": "#16a085",
                   "Other": "#999999"}

TEMPLATE = """<!doctype html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__</title>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css">
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<style>html,body,#map{height:100%;margin:0;font-family:system-ui,sans-serif}
.legend{background:#fff;padding:8px 10px;border-radius:6px;line-height:1.5;font-size:13px}
.dot{display:inline-block;width:10px;height:10px;border-radius:50%;margin-right:6px}</style></head>
<body><div id="map"></div><script>
const data = __DATA__;
const map = L.map('map', {preferCanvas: true});
L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png',
  {maxZoom: 18, attribution: '&copy; OpenStreetMap contributors'}).addTo(map);
const maxPop = Math.max(...data.hexes.map(h => h[2]), 1);
data.hexes.forEach(([lat, lon, pop, covered]) => L.circleMarker([lat, lon], {
  radius: 2 + 6 * Math.sqrt(pop / maxPop), stroke: false, fillOpacity: 0.55,
  fillColor: covered ? '#2e7d32' : '#ef6c00'}).bindTooltip(Math.round(pop) + ' people').addTo(map));
data.lockers.forEach(l => L.circleMarker([l.lat, l.lon], {radius: 6, color: '#fff', weight: 1.5,
  fillColor: data.colours[l.network] || '#999', fillOpacity: 1}).bindPopup(l.network + ': ' + (l.name || '')).addTo(map));
data.sites.forEach((s, i) => L.marker([s.lat, s.lon]).bindPopup((i + 1) + '. ' + s.name).bindTooltip(String(i + 1),
  {permanent: true, direction: 'top'}).addTo(map));
map.fitBounds(L.latLngBounds(data.hexes.map(h => [h[0], h[1]])));
const legend = L.control({position: 'bottomright'});
legend.onAdd = () => { const d = L.DomUtil.create('div', 'legend');
  d.innerHTML = '<b>' + data.title + '</b><br>' +
    '<span class="dot" style="background:#2e7d32"></span>within ' + data.radius + ' m of ' + data.network + '<br>' +
    '<span class="dot" style="background:#ef6c00"></span>outside<br>' +
    Object.entries(data.colours).filter(([n]) => data.lockers.some(l => l.network === n))
      .map(([n, c]) => '<span class="dot" style="background:' + c + '"></span>' + n + ' locker').join('<br>') +
    (data.sites.length ? '<br>numbered pins: proposed sites' : '') +
    '<br><small>Lockers from OpenStreetMap (Instabee incomplete)</small>';
  return d; };
legend.addTo(map);
</script></body></html>
"""


def render_map(con, area: str, network: str = "Instabee", radius_m: float = 500, sites: list | None = None) -> Path:
    dist, label, _ = _network(network)
    r = _radius(radius_m)
    a = _municipality(con, area)
    hexes = con.execute(f"""SELECT round(lat, 5), round(lon, 5), round(pop_est, 1), {dist} <= {r}
        FROM hex_features WHERE {a.where()} AND pop_est >= 1""").fetchall()
    lockers = con.execute(f"""SELECT l.network, l.name, l.lat, l.lon FROM lockers l
        JOIN deso d ON ST_Within(l.geom, d.geom) WHERE {a.where('d.kommun_code')}""").df()
    data = {"title": f"{a.name}: {label} lockers, {r:.0f} m", "network": label, "radius": round(r),
            "hexes": [list(h) for h in hexes], "lockers": lockers.to_dict("records"),
            "sites": [{"name": str(s["name"]), "lat": float(s["lat"]), "lon": float(s["lon"])} for s in (sites or [])],
            "colours": NETWORK_COLOURS}
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    slug = re.sub(r"[^a-z0-9]+", "-", a.name.lower()).strip("-") or "area"
    path = OUTPUT_DIR / f"map_{slug}_{dt.datetime.now():%Y%m%d_%H%M%S}.html"
    html = TEMPLATE.replace("__TITLE__", data["title"]).replace("__DATA__", json.dumps(data, ensure_ascii=False, default=str))
    path.write_text(html, encoding="utf-8")
    return path
