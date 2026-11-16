"""Read the outreach mailbox: stop sequences on replies, honour opt-outs, record bounces."""
import datetime
import email
import email.policy
import imaplib
import os
import re

from . import db
from .enrich import EMAIL_RE

OPT_OUT = re.compile(r"\b(unsubscribe|no thanks|not interested|remove me|stop (emailing|contacting|sending)|"
                     r"take me off|do not (contact|email)|don't (contact|email))\b", re.I)
BOUNCE_FROM = re.compile(r"mailer-daemon|postmaster|mail delivery", re.I)
AUTO = re.compile(r"out of (the )?office|automatic reply|auto.?reply|autoreply|on (annual )?leave", re.I)


def classify(from_addr, subject, body, auto_submitted=""):
    """-> 'bounce' | 'optout' | 'auto' | 'reply'"""
    if BOUNCE_FROM.search(from_addr) or re.search(r"undeliver|delivery (status|failure|has failed)", subject, re.I):
        return "bounce"
    fresh = "\n".join(l for l in body.splitlines() if not l.lstrip().startswith(">"))
    fresh = re.split(r"\nOn .{5,80} wrote:|\n-{2,}\s*Original Message", fresh)[0]  # drop the quoted original
    if subject.strip().lower() == "unsubscribe" or OPT_OUT.search(fresh[:600]):
        return "optout"
    if (auto_submitted and auto_submitted.lower() != "no") or AUTO.search(subject):
        return "auto"
    return "reply"


def _body_text(msg):
    part = msg.get_body(preferencelist=("plain", "html"))
    try:
        text = part.get_content() if part else ""
    except Exception:
        text = ""
    return re.sub(r"<[^>]+>", " ", text)


def handle(con, from_addr, subject, body, auto_submitted=""):
    """Apply one incoming message to the CRM. Returns (kind, lead_name or '')."""
    kind = classify(from_addr, subject, body, auto_submitted)
    if kind == "bounce":
        hit = ""
        for addr in set(a.lower() for a in EMAIL_RE.findall(body)):
            lead = con.execute("SELECT * FROM leads WHERE lower(email)=?", (addr,)).fetchone()
            if lead:
                db.suppress(con, addr, "bounced")
                db.update_lead(con, lead["id"], status="bounced")
                db.log(con, lead["id"], "bounce", subject[:120])
                hit = lead["name"]
        return kind, hit
    m = EMAIL_RE.search(from_addr)
    sender = m.group(0).lower() if m else ""
    lead = con.execute("SELECT * FROM leads WHERE lower(email)=? OR domain=? ORDER BY (lower(email)=?) DESC",
                       (sender, sender.split("@")[-1], sender)).fetchone() if sender else None
    if not lead or kind == "auto":
        return kind, lead["name"] if lead else ""
    if kind == "optout":
        db.suppress(con, sender, "asked to stop")
        db.suppress(con, lead["email"], "asked to stop")
        db.log(con, lead["id"], "optout", body[:200])
    else:
        con.execute("UPDATE messages SET status='cancelled' WHERE lead_id=? AND status IN ('draft','approved')",
                    (lead["id"],))
        db.update_lead(con, lead["id"], status="replied")
        db.log(con, lead["id"], "reply", body[:500])
    return kind, lead["name"]


def configured():
    return all(os.environ.get(v) for v in ("IMAP_HOST", "IMAP_USER", "IMAP_PASS"))


def check(con, days=14, out=print):
    """Scan recent mail without marking anything as read. Returns counts per kind."""
    box = imaplib.IMAP4_SSL(os.environ["IMAP_HOST"], int(os.environ.get("IMAP_PORT", "993")))
    box.login(os.environ["IMAP_USER"], os.environ["IMAP_PASS"])
    counts = {}
    try:
        box.select("INBOX", readonly=True)
        since = (datetime.date.today() - datetime.timedelta(days=days)).strftime("%d-%b-%Y")
        _, data = box.search(None, "SINCE", since)
        for num in data[0].split():
            _, parts = box.fetch(num, "(BODY.PEEK[])")
            msg = email.message_from_bytes(parts[0][1], policy=email.policy.default)
            mid = msg.get("Message-ID") or f"{msg.get('Date')}|{msg.get('From')}"
            if con.execute("INSERT OR IGNORE INTO seen_mail VALUES(?)", (mid,)).rowcount == 0:
                continue
            kind, who = handle(con, str(msg.get("From", "")), str(msg.get("Subject", "")), _body_text(msg),
                               str(msg.get("Auto-Submitted", "")))
            con.commit()
            if who:
                counts[kind] = counts.get(kind, 0) + 1
                out(f"  {kind:<7} {who}")
    finally:
        box.logout()
    return counts
