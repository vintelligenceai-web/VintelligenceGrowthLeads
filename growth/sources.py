"""Where leads come from: OpenStreetMap business listings, CSV files, and public 'intent' posts."""
import csv
import json
import re
import time
import urllib.parse

from . import db, net

# niche -> OpenStreetMap tag filters
NICHES = {
    "dental": ['["amenity"="dentist"]', '["healthcare"="dentist"]'],
    "clinic": ['["amenity"~"^(clinic|doctors)$"]'],
    "restaurant": ['["amenity"="restaurant"]'],
    "realestate": ['["office"="estate_agent"]'],
    "salon": ['["shop"~"^(hairdresser|beauty)$"]'],
    "hotel": ['["tourism"="hotel"]'],
    "vet": ['["amenity"="veterinary"]'],
    "autorepair": ['["shop"="car_repair"]'],
    "legal": ['["office"="lawyer"]'],
    "trades": ['["craft"~"^(plumber|hvac|electrician)$"]'],
}


def geocode(city):
    """City name -> (south, west, north, east, ISO-2 country) via OpenStreetMap Nominatim."""
    q = urllib.parse.urlencode({"q": city, "format": "json", "limit": 1, "addressdetails": 1})
    _, text = net.fetch("https://nominatim.openstreetmap.org/search?" + q)
    hits = json.loads(text)
    if not hits:
        raise ValueError(f"City not found: {city}")
    s, n, w, e = hits[0]["boundingbox"]
    return s, w, n, e, hits[0].get("address", {}).get("country_code", "").upper()


# Public Overpass servers. They are free and often busy, so try each in turn.
OVERPASS = ["https://overpass-api.de/api/interpreter",
            "https://overpass.private.coffee/api/interpreter",
            "https://overpass.kumi.systems/api/interpreter"]


def _overpass(query):
    body, last = urllib.parse.urlencode({"data": query}).encode(), None
    for attempt in range(2):
        for url in OVERPASS:
            try:
                return net.fetch(url, data=body, timeout=90, max_bytes=20_000_000)[1]
            except Exception as ex:
                last = ex
                time.sleep(3)
        time.sleep(15)
    raise RuntimeError(f"all map servers busy or refusing ({last}); try again later or use `import`")


def find_osm(con, niche, city, limit=40):
    """Add businesses of `niche` in `city` that list a website. Returns (found, added)."""
    if niche not in NICHES:
        raise ValueError(f"Unknown niche '{niche}'. Choose from: {', '.join(NICHES)}")
    s, w, n, e, country = geocode(city)
    time.sleep(1.1)  # Nominatim/Overpass fair-use: about one request per second
    parts = "".join(f'nwr{f}["{key}"]({s},{w},{n},{e});'
                    for f in NICHES[niche] for key in ("website", "contact:website"))
    query = f"[out:json][timeout:60];({parts});out tags {int(limit) * 3};"
    text = _overpass(query)
    found = added = 0
    for el in json.loads(text).get("elements", []):
        t = el.get("tags", {})
        site, name = t.get("website") or t.get("contact:website"), t.get("name")
        if not site or not name:
            continue
        found += 1
        if added >= limit:
            continue
        lid = db.add_lead(con, name, site, niche, country, t.get("addr:city") or city.split(",")[0],
                          t.get("phone") or t.get("contact:phone") or "",
                          t.get("email") or t.get("contact:email") or "", "osm")
        if lid:
            added += 1
            wa = re.sub(r"\D", "", t.get("contact:whatsapp", ""))
            if 8 <= len(wa) <= 15:
                con.execute("UPDATE leads SET whatsapp=? WHERE id=?", (wa, lid))
    con.commit()
    return found, added


def import_csv(con, path, niche="", country=""):
    """Import leads from any CSV with a website column (Apollo, Google Maps exports, your own lists)."""
    added = total = 0
    with open(path, newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            r = {(k or "").strip().lower(): (v or "").strip() for k, v in row.items()}
            site = r.get("website") or r.get("url") or r.get("domain") or r.get("company website") or ""
            name = r.get("name") or r.get("company") or r.get("company name") or db.domain_of(site)
            total += 1
            if db.add_lead(con, name, site, r.get("niche") or niche, r.get("country") or country,
                           r.get("city", ""), r.get("phone", ""), r.get("email", ""), "csv"):
                added += 1
    con.commit()
    return total, added


def _strip_html(s):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", s or "")).strip()


def _hn(query, since_ts):
    q = urllib.parse.urlencode({"query": f'"{query}"', "tags": "(story,comment)",
                                "numericFilters": f"created_at_i>{since_ts}", "hitsPerPage": 20})
    _, text = net.fetch("https://hn.algolia.com/api/v1/search_by_date?" + q)
    for h in json.loads(text).get("hits", []):
        yield ("hackernews", f"https://news.ycombinator.com/item?id={h['objectID']}",
               h.get("title") or h.get("story_title") or "",
               _strip_html(h.get("comment_text") or h.get("story_text") or "")[:400], h.get("created_at", ""))


def find_intent(con, queries, days=7):
    """Collect recent public posts where someone is asking for what we sell. For manual, helpful replies."""
    since = int(time.time()) - days * 86400
    added, errors = 0, []
    for q in queries:
        for name, gen in (("hackernews", lambda: _hn(q, since)),):  # Reddit refuses anonymous access
            try:
                for source, url, title, snippet, posted in gen():
                    if not url:
                        continue
                    cur = con.execute("INSERT OR IGNORE INTO intent(source,url,title,snippet,posted_at,found_at)"
                                      " VALUES(?,?,?,?,?,?)", (source, url, title, snippet, posted, db.now()))
                    added += cur.rowcount
            except Exception as ex:  # one blocked source must not stop the others
                errors.append(f"{name} '{q}': {ex}")
            time.sleep(1.5)
    con.commit()
    return added, errors
