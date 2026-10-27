"""Count S1-RTC passes and S2-L2A dates over the Twente network, 2017-2020 (metadata only)."""
import json, sys, urllib.request, collections, datetime as dt
sys.stdout.reconfigure(encoding="utf-8")
URL = "https://planetarycomputer.microsoft.com/api/stac/v1/search"
BBOX = [6.30, 52.06, 6.98, 52.43]          # all stations

def search(coll, start, end):
    body = {"collections": [coll], "bbox": BBOX, "datetime": f"{start}/{end}", "limit": 1000}
    out, nxt = [], None
    while True:
        req = urllib.request.Request(URL, json.dumps(nxt or body).encode(), {"Content-Type": "application/json"})
        d = json.load(urllib.request.urlopen(req, timeout=120))
        out += d["features"]
        link = [l for l in d.get("links", []) if l.get("rel") == "next"]
        if not link: return out
        nxt = link[0].get("body") or {**body, "token": link[0]["href"].split("token=")[-1]}

for year in (2017, 2018, 2019, 2020):
    s1 = search("sentinel-1-rtc", f"{year}-01-01", f"{year}-12-31")
    passes = {(f["properties"]["datetime"][:13], f["properties"]["sat:relative_orbit"]) for f in s1}
    s2 = search("sentinel-2-l2a", f"{year}-01-01", f"{year}-12-31")
    by_date = collections.defaultdict(list)
    for f in s2:
        by_date[f["properties"]["datetime"][:10]].append(f["properties"]["eo:cloud_cover"])
    clear = {d: min(c) for d, c in by_date.items()}
    s1_days = sorted({dt.date.fromisoformat(p[0][:10]) for p in passes})
    def gap(d):
        d = dt.date.fromisoformat(d)
        return min(abs((x - d).days) for x in s1_days)
    lt20 = [d for d, c in clear.items() if c < 20]
    gaps = collections.Counter(gap(d) for d in lt20)
    print(f"{year}: S1 passes {len(passes):3d} (orbits {sorted({p[1] for p in passes})}) | "
          f"S2 dates {len(clear):3d}, tile cloud<20%: {len(lt20):3d}, <50%: {sum(c<50 for c in clear.values()):3d} | "
          f"S1 gap to clear S2 (days): {dict(sorted(gaps.items()))}")
