"""Visit a lead's own website, pull public contact details and detect what they already automate."""
import json
import re
import time
import urllib.parse
import urllib.robotparser

from . import db, net
from .config import email_policy

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}")
BAD_EMAIL = re.compile(r"\.(png|jpe?g|gif|webp|svg|css|js)$|sentry|wixpress|example\.|yourdomain|@domain\.|"
                       r"@email\.com|godaddy|schema\.org|@2x|^(no-?reply|donotreply)", re.I)
FREE_MAIL = {"gmail.com", "yahoo.com", "outlook.com", "hotmail.com", "aol.com", "icloud.com", "live.com",
             "msn.com", "me.com", "proton.me", "protonmail.com"}
ROLE = ["info", "hello", "contact", "office", "frontdesk", "reception", "appointments", "reservations",
        "booking", "bookings", "admin", "enquiries", "inquiries", "team", "mail", "sales", "support"]

# A click-to-chat link on the business's own site: an open invitation to message them there.
WHATSAPP_RE = re.compile(r"(?:wa\.me/|api\.whatsapp\.com/send/?\?(?:[^\"'\s]*&)?phone=|whatsapp://send/?\?phone=)\+?(\d{8,15})", re.I)

DETECT = {
    "online_booking": r"calendly\.com|opentable\.|resy\.com|acuityscheduling|zocdoc|booksy|square\.site|"
                      r"squareup\.com/appointments|mindbodyonline|vagaro|simplybook|setmore|exploretock|sevenrooms|"
                      r"nexhealth|localmed|fresha\.com|doctolib|book(ing)?\s+(now|online|an appointment|a table)|"
                      r"schedule\s+(online|now|an appointment)|reserve\s+a\s+table|request\s+an?\s+appointment",
    "chat_widget": r"intercom|drift\.com|tawk\.to|crisp\.chat|tidio|zdassets|livechatinc|manychat|podium\.com|"
                   r"birdeye|smartsupp|olark|freshchat",
    "online_ordering": r"toasttab|doordash|ubereats|grubhub|chownow|order\s+online|olo\.com|slicelife",
    "ai_voice": r"slang\.ai|synthflow|goodcall|loman\.ai|vapi\.ai|retellai|bland\.ai|smith\.ai",
}

NICHE_WEIGHT = {"dental": 20, "clinic": 18, "restaurant": 15, "realestate": 15, "salon": 15, "vet": 15,
                "trades": 15, "autorepair": 12, "legal": 12, "hotel": 10}


def decode_cfemail(hexstr):
    """Cloudflare hides emails as data-cfemail="<hex>"; first byte is the XOR key."""
    key = int(hexstr[:2], 16)
    return "".join(chr(int(hexstr[i:i + 2], 16) ^ key) for i in range(2, len(hexstr), 2))


def extract_emails(html):
    found = [decode_cfemail(h) for h in re.findall(r'data-cfemail="([0-9a-fA-F]+)"', html)]
    found += EMAIL_RE.findall(urllib.parse.unquote(html))
    out = []
    for e in found:
        e = e.strip(".").lower()
        if not BAD_EMAIL.search(e) and e not in out:
            out.append(e)
    return out


def pick_email(emails, domain):
    """Best public address: on the business's own domain (or free-mail), role inboxes first."""
    def usable(e):
        d = e.split("@")[1]
        return d == domain or d.endswith("." + domain) or domain.endswith("." + d) or d in FREE_MAIL

    def rank(e):
        local, d = e.split("@")
        role = ROLE.index(local) if local in ROLE else len(ROLE)
        return (d in FREE_MAIL, role)

    ok = sorted((e for e in emails if usable(e)), key=rank)
    return ok[0] if ok else ""


def detect(html):
    sig = {k: bool(re.search(p, html, re.I)) for k, p in DETECT.items()}
    tel = re.search(r'href="tel:([^"]+)"', html, re.I)
    sig["phone"] = urllib.parse.unquote(tel.group(1)).strip() if tel else ""
    wa = WHATSAPP_RE.search(urllib.parse.unquote(html))
    sig["whatsapp"] = wa.group(1) if wa else ""
    return sig


def score(lead, sig, cfg):
    s = NICHE_WEIGHT.get(lead["niche"], 5)
    s += 30 if lead["email"] else 0
    s += 10 if (sig.get("phone") or lead["phone"]) else 0
    s += 25 if not sig.get("online_booking") else 0
    s += 10 if not sig.get("chat_widget") else 0
    s += 5 if email_policy(cfg, lead["country"])[0] else 0
    s -= 40 if sig.get("ai_voice") else 0
    return max(0, min(100, s))


def _allowed(robots, url):
    try:
        return robots.can_fetch(net.UA, url)
    except Exception:
        return True


def crawl(website):
    """Fetch the homepage plus up to two contact/about pages. Returns combined HTML ('' if unreachable)."""
    base = urllib.parse.urlsplit(website)
    robots = urllib.robotparser.RobotFileParser()
    try:
        _, txt = net.fetch(f"{base.scheme}://{base.netloc}/robots.txt", timeout=10, max_bytes=200_000)
        robots.parse(txt.splitlines())
    except Exception:
        robots.parse([])
    if not _allowed(robots, website):
        return ""
    for attempt in range(2):  # one retry: DNS and connection hiccups are common
        try:
            final, html = net.fetch(website)
            break
        except Exception:
            if attempt:
                return ""
            time.sleep(3)
    host = urllib.parse.urlsplit(final).netloc
    extra = []
    for href in re.findall(r'href="([^"#]+)"', html):
        if re.search(r"contact|about|location|team", href, re.I):
            u = urllib.parse.urljoin(final, href)
            if urllib.parse.urlsplit(u).netloc == host and u not in extra and u != final:
                extra.append(u)
    for u in extra[:2]:
        if _allowed(robots, u):
            try:
                html += "\n" + net.fetch(u, timeout=15)[1]
            except Exception:
                pass
    return html


def apply(con, lead, html, cfg):
    """Store what a crawl found for one lead: contact details, signals, score, channel."""
    if not html:
        db.update_lead(con, lead["id"], status="unreachable")
        return
    sig = detect(html)
    email = lead["email"] or pick_email(extract_emails(html), lead["domain"])
    fields = {"email": email, "phone": lead["phone"] or sig["phone"], "signals": sig,
              "whatsapp": lead["whatsapp"] or sig["whatsapp"]}
    merged = {**dict(lead), **fields}
    can_email = bool(email) and email_policy(cfg, lead["country"])[0] and not db.is_suppressed(con, email)
    fields["channel"] = "email" if can_email else "manual"
    fields["score"] = score(merged, sig, cfg)
    if lead["status"] == "new":
        fields["status"] = "enriched"
    db.update_lead(con, lead["id"], **fields)


def enrich_new(con, cfg, limit=50, progress=print, retry=False):
    if retry:  # give sites that failed earlier another chance
        con.execute("UPDATE leads SET status='new' WHERE status='unreachable'")
    rows = con.execute("SELECT * FROM leads WHERE status='new' ORDER BY id LIMIT ?", (limit,)).fetchall()
    for lead in rows:
        apply(con, lead, crawl(lead["website"]), cfg)
        con.commit()
        r = con.execute("SELECT status, email, score, channel FROM leads WHERE id=?", (lead["id"],)).fetchone()
        progress(f"  {lead['domain']:<38} {r['status']:<11} score={r['score']:<3} {r['channel']:<6} {r['email']}")
    return len(rows)


def rescore(con, cfg):
    for lead in con.execute("SELECT * FROM leads WHERE status IN ('enriched','drafted')").fetchall():
        db.update_lead(con, lead["id"], score=score(lead, json.loads(lead["signals"]), cfg))
    con.commit()


def check_whatsapp(con, limit=80, progress=print):
    """Re-read sites of leads enriched before WhatsApp detection existed. Returns (checked, found)."""
    rows = con.execute("SELECT * FROM leads WHERE status IN ('enriched','drafted') AND whatsapp='' "
                       "AND signals NOT LIKE '%\"whatsapp\"%' ORDER BY score DESC LIMIT ?", (limit,)).fetchall()
    found = 0
    for lead in rows:
        html = crawl(lead["website"])
        sig = json.loads(lead["signals"] or "{}")
        wa = WHATSAPP_RE.search(urllib.parse.unquote(html)) if html else None
        sig["whatsapp"] = wa.group(1) if wa else ""
        db.update_lead(con, lead["id"], signals=sig, whatsapp=sig["whatsapp"])
        con.commit()
        found += bool(wa)
        progress(f"  {lead['domain']:<38} {'WhatsApp +' + sig['whatsapp'] if wa else 'no WhatsApp link'}")
    return len(rows), found
