"""Send approved emails over SMTP, slowly, with every safety check applied per message."""
import os
import random
import smtplib
import time
from email.message import EmailMessage
from email.utils import formataddr, formatdate, make_msgid

from . import db
from .config import email_policy


def preflight(cfg):
    """Problems that must be fixed before any live send."""
    c, s, problems = cfg["company"], cfg["sending"], []
    if not c.get("sender_name"):
        problems.append("config.toml [company] sender_name is empty")
    if not c.get("postal_address"):
        problems.append("config.toml [company] postal_address is empty (legally required in the US)")
    if not s.get("from_email"):
        problems.append("config.toml [sending] from_email is empty")
    elif s["from_email"].lower().endswith(("@gmail.com", "@yahoo.com", "@outlook.com", "@hotmail.com")):
        problems.append("from_email is a free mailbox; cold email must come from your own outreach domain")
    for var in ("SMTP_HOST", "SMTP_USER", "SMTP_PASS"):
        if not os.environ.get(var):
            problems.append(f".env is missing {var}")
    return problems


def blocked_reason(con, cfg, lead):
    """Why this lead must not be emailed right now ('' if it is fine)."""
    if not lead["email"]:
        return "no email address"
    if db.is_suppressed(con, lead["email"]):
        return "on suppression list"
    if lead["status"] not in ("drafted", "contacted"):
        return f"lead status is '{lead['status']}'"
    ok, why = email_policy(cfg, lead["country"])
    return "" if ok else why


def build_mime(cfg, lead, msg, thread_id=""):
    s, c = cfg["sending"], cfg["company"]
    m = EmailMessage()
    m["From"] = formataddr((c["sender_name"], s["from_email"]))
    m["To"] = lead["email"]
    m["Subject"] = msg["subject"]
    m["Date"] = formatdate(localtime=True)
    m["Message-ID"] = make_msgid(domain=s["from_email"].split("@")[-1])
    reply_to = s.get("reply_to") or s["from_email"]
    m["Reply-To"] = reply_to
    m["List-Unsubscribe"] = f"<mailto:{reply_to}?subject=unsubscribe>"
    if thread_id:
        m["In-Reply-To"] = thread_id
        m["References"] = thread_id
    m.set_content(msg["body"])
    return m


def sent_today(con):
    return con.execute("SELECT COUNT(*) FROM messages WHERE status='sent' AND substr(sent_at,1,10)=?",
                       (db.now()[:10],)).fetchone()[0]


def send_approved(con, cfg, live=False, limit=None, out=print):
    """Send approved messages. Without live=True nothing leaves the machine. Returns number sent."""
    s = cfg["sending"]
    if live:
        problems = preflight(cfg)
        if problems:
            out("Cannot send yet:\n  - " + "\n  - ".join(problems))
            return 0
    room = max(0, s["daily_cap"] - sent_today(con))
    if limit is not None:
        room = min(room, limit)
    rows = con.execute("SELECT m.*, l.name AS lead_name FROM messages m JOIN leads l ON l.id=m.lead_id "
                       "WHERE m.status='approved' ORDER BY m.step DESC, m.id").fetchall()
    if not rows:
        out("Nothing approved to send. Use `review` then `approve`.")
        return 0

    smtp = None
    if live and room:
        smtp = smtplib.SMTP(os.environ["SMTP_HOST"], int(os.environ.get("SMTP_PORT", "587")), timeout=30)
        smtp.starttls()
        smtp.login(os.environ["SMTP_USER"], os.environ["SMTP_PASS"])
    sent = 0
    try:
        for msg in rows:
            lead = con.execute("SELECT * FROM leads WHERE id=?", (msg["lead_id"],)).fetchone()
            why = blocked_reason(con, cfg, lead)
            if why:
                con.execute("UPDATE messages SET status='cancelled' WHERE id=?", (msg["id"],))
                db.log(con, lead["id"], "send_blocked", why)
                con.commit()
                out(f"  skip   {lead['email'] or lead['domain']}: {why}")
                continue
            if sent >= room:
                out(f"  Daily cap reached ({s['daily_cap']}). The rest stay approved for tomorrow.")
                break
            if not live:
                out(f"  DRY RUN would send step {msg['step']} to {lead['email']}  |  {msg['subject']}")
                sent += 1
                continue
            first = con.execute("SELECT message_id FROM messages WHERE lead_id=? AND step=1 AND status='sent'",
                                (lead["id"],)).fetchone()
            mime = build_mime(cfg, lead, msg, first["message_id"] if first and msg["step"] > 1 else "")
            try:
                smtp.send_message(mime)
            except smtplib.SMTPRecipientsRefused:
                db.suppress(con, lead["email"], "rejected by server")
                db.update_lead(con, lead["id"], status="bounced")
                con.commit()
                out(f"  bounce {lead['email']}")
                continue
            con.execute("UPDATE messages SET status='sent', sent_at=?, message_id=? WHERE id=?",
                        (db.now(), mime["Message-ID"], msg["id"]))
            db.update_lead(con, lead["id"], status="contacted")
            db.log(con, lead["id"], "sent", f"step {msg['step']}")
            con.commit()
            sent += 1
            out(f"  sent   step {msg['step']} -> {lead['email']}")
            if sent < room:
                time.sleep(random.uniform(s["min_gap_seconds"], s["max_gap_seconds"]))
    finally:
        if smtp:
            smtp.quit()
    return sent


def autopilot(con, cfg, out=print):
    """If the owner switched autopilot on: approve pending first emails and send within the daily cap."""
    if not cfg.get("autopilot", {}).get("enabled"):
        return 0
    problems = preflight(cfg)
    if problems:
        out("Autopilot is on but cannot send yet: " + "; ".join(problems))
        return 0
    n = con.execute("UPDATE messages SET status='approved' WHERE status='draft'").rowcount
    con.commit()
    out(f"Autopilot: approved {n} draft(s), sending within the daily limit ...")
    return send_approved(con, cfg, live=True, out=out)
