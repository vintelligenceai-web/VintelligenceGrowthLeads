import datetime
import json
import sqlite3
import urllib.parse

from .config import ROOT

DB_PATH = ROOT / "data" / "growth.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS leads(
  id INTEGER PRIMARY KEY, name TEXT, niche TEXT, country TEXT, city TEXT,
  website TEXT, domain TEXT UNIQUE, phone TEXT DEFAULT '', email TEXT DEFAULT '',
  source TEXT, signals TEXT DEFAULT '{}', score INTEGER DEFAULT 0,
  status TEXT DEFAULT 'new', channel TEXT DEFAULT '', opener TEXT DEFAULT '',
  notes TEXT DEFAULT '', created_at TEXT, updated_at TEXT);
CREATE TABLE IF NOT EXISTS messages(
  id INTEGER PRIMARY KEY, lead_id INTEGER REFERENCES leads(id), step INTEGER,
  subject TEXT, body TEXT, status TEXT DEFAULT 'draft', message_id TEXT DEFAULT '',
  created_at TEXT, sent_at TEXT);
CREATE TABLE IF NOT EXISTS suppression(value TEXT PRIMARY KEY, reason TEXT, at TEXT);
CREATE TABLE IF NOT EXISTS intent(
  id INTEGER PRIMARY KEY, source TEXT, url TEXT UNIQUE, title TEXT, snippet TEXT,
  posted_at TEXT, status TEXT DEFAULT 'new', found_at TEXT);
CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY, lead_id INTEGER, kind TEXT, detail TEXT, at TEXT);
CREATE TABLE IF NOT EXISTS seen_mail(message_id TEXT PRIMARY KEY);
CREATE TABLE IF NOT EXISTS kv(key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS opps(
  id INTEGER PRIMARY KEY, source TEXT, url TEXT UNIQUE, title TEXT, company TEXT DEFAULT '',
  snippet TEXT DEFAULT '', budget TEXT DEFAULT '', tags TEXT DEFAULT '', posted_at TEXT DEFAULT '',
  score INTEGER DEFAULT 0, status TEXT DEFAULT 'new', proposal TEXT DEFAULT '', found_at TEXT);
CREATE TABLE IF NOT EXISTS videos(
  id INTEGER PRIMARY KEY, title TEXT, description TEXT DEFAULT '', tags TEXT DEFAULT '',
  script TEXT DEFAULT '', file TEXT DEFAULT '', publish_at TEXT DEFAULT '', status TEXT DEFAULT 'idea',
  youtube_id TEXT DEFAULT '', error TEXT DEFAULT '', created_at TEXT, uploaded_at TEXT);
"""

# Lead lifecycle. Anything from 'replied' onward is moved by a human with `vi.py set`.
STATUSES = ["new", "enriched", "unreachable", "drafted", "contacted", "no_reply", "replied",
            "meeting", "proposal", "won", "lost", "bounced", "suppressed"]

# Hosts that are never a business's own website.
NOT_A_SITE = ("facebook.com", "instagram.com", "yelp.com", "linktr.ee", "google.com", "twitter.com",
              "x.com", "tripadvisor.com", "opentable.com", "doordash.com", "ubereats.com",
              "grubhub.com", "linkedin.com", "youtube.com", "tiktok.com", "zocdoc.com")


def connect(path=None):
    path = path or DB_PATH
    if path != ":memory:":
        DB_PATH.parent.mkdir(exist_ok=True)
    con = sqlite3.connect(path, timeout=30)
    con.row_factory = sqlite3.Row
    con.executescript(SCHEMA)
    have = {r[1] for r in con.execute("PRAGMA table_info(leads)")}
    for col, ddl in (("whatsapp", "TEXT DEFAULT ''"), ("wa_step", "INTEGER DEFAULT 0"), ("wa_at", "TEXT DEFAULT ''")):
        if col not in have:
            con.execute(f"ALTER TABLE leads ADD COLUMN {col} {ddl}")
    return con


def now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


def days_since(iso):
    then = datetime.datetime.fromisoformat(iso)
    return (datetime.datetime.now(datetime.timezone.utc) - then).total_seconds() / 86400


def log(con, lead_id, kind, detail=""):
    con.execute("INSERT INTO events(lead_id, kind, detail, at) VALUES(?,?,?,?)", (lead_id, kind, detail, now()))


def domain_of(url):
    url = (url or "").strip()
    if not url:
        return ""
    if "://" not in url:
        url = "https://" + url
    host = (urllib.parse.urlsplit(url).hostname or "").lower()
    return host[4:] if host.startswith("www.") else host


def normalize_url(url):
    url = (url or "").strip()
    return url if "://" in url else ("https://" + url if url else "")


def add_lead(con, name, website, niche="", country="", city="", phone="", email="", source=""):
    """Insert a lead keyed by domain. Returns the new id, or None if skipped or already known."""
    dom = domain_of(website)
    if not dom or "." not in dom or any(dom == h or dom.endswith("." + h) for h in NOT_A_SITE):
        return None
    if is_suppressed(con, email or "x@" + dom):
        return None
    cur = con.execute(
        "INSERT OR IGNORE INTO leads(name,niche,country,city,website,domain,phone,email,source,created_at,updated_at)"
        " VALUES(?,?,?,?,?,?,?,?,?,?,?)",
        (name.strip(), niche, country.upper(), city, normalize_url(website), dom, phone or "",
         (email or "").lower(), source, now(), now()))
    return cur.lastrowid if cur.rowcount else None


def update_lead(con, lead_id, **fields):
    if "signals" in fields and not isinstance(fields["signals"], str):
        fields["signals"] = json.dumps(fields["signals"])
    fields["updated_at"] = now()
    sets = ", ".join(f"{k}=?" for k in fields)
    con.execute(f"UPDATE leads SET {sets} WHERE id=?", (*fields.values(), lead_id))


def suppress(con, value, reason):
    """Never contact this email address or domain again."""
    value = value.strip().lower()
    con.execute("INSERT OR IGNORE INTO suppression(value, reason, at) VALUES(?,?,?)", (value, reason, now()))
    if "@" in value:
        con.execute("UPDATE leads SET status='suppressed', updated_at=? WHERE lower(email)=?", (now(), value))
    else:
        con.execute("UPDATE leads SET status='suppressed', updated_at=? WHERE domain=?", (now(), value))
    con.execute("UPDATE messages SET status='cancelled' WHERE status IN ('draft','approved') AND lead_id IN "
                "(SELECT id FROM leads WHERE status='suppressed')")


def is_suppressed(con, email):
    email = (email or "").lower()
    dom = email.split("@")[-1]
    return con.execute("SELECT 1 FROM suppression WHERE value IN (?,?)", (email, dom)).fetchone() is not None
