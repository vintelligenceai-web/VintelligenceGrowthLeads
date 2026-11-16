"""Turn a lead + template into a short, truthful, personalised email sequence."""
import json
import re

from . import db
from .config import ROOT

TEMPLATES = ROOT / "templates"

# niche -> (who we build for, what a caller wants to do)
NICHE_TEXT = {
    "dental": ("dental practices", "book an appointment"),
    "clinic": ("clinics", "book an appointment"),
    "restaurant": ("restaurants", "reserve a table"),
    "realestate": ("real estate agencies", "arrange a viewing"),
    "salon": ("salons", "book an appointment"),
    "hotel": ("hotels", "make or change a booking"),
    "vet": ("veterinary practices", "book an appointment"),
    "autorepair": ("auto repair shops", "book a service slot"),
    "legal": ("law firms", "book a consultation"),
    "trades": ("home-service businesses", "book a job"),
}


def load_steps(niche):
    """templates/<niche>.txt (or generic.txt) -> {step: (subject, body)}"""
    path = TEMPLATES / f"{niche}.txt"
    if not path.exists():
        path = TEMPLATES / "generic.txt"
    steps = {}
    for chunk in re.split(r"^=== step (\d+) ===\s*$", path.read_text(encoding="utf-8"), flags=re.M)[1:]:
        if chunk.strip().isdigit():
            n = int(chunk)
            continue
        subject, _, body = chunk.strip().partition("\n")
        steps[n] = (subject.replace("Subject:", "").strip(), body.strip())
    return steps


def opener(lead, sig):
    """First line. Only states what was actually observed on the site; a hand-written opener wins."""
    if lead["opener"]:
        return lead["opener"]
    d, action = lead["domain"], NICHE_TEXT.get(lead["niche"], ("", "book"))[1]
    if lead["niche"] == "realestate":
        return (f"I was looking at {d}. In property, the agency that calls a new enquiry back first usually "
                f"wins the viewing, and that is hard to do on evenings and weekends.")
    if not sig.get("online_booking"):
        return (f"I was looking at {d} and couldn't spot a way to {action} online, so I'm guessing most of that "
                f"comes in by phone, and some of those calls land when nobody can pick up.")
    return (f"I was looking at {d}. You already let people {action} online, so I'm guessing the phone is "
            f"what still pulls your team away at the busiest times.")


def render(text, values):
    """Fill {placeholders}. A line whose placeholders are all empty is dropped."""
    out = []
    for line in text.splitlines():
        keys = re.findall(r"\{(\w+)\}", line)
        if keys and not any(values.get(k) for k in keys):
            continue
        out.append(re.sub(r"\{(\w+)\}", lambda m: str(values.get(m.group(1), "")), line))
    return re.sub(r"\n{3,}", "\n\n", "\n".join(out)).strip()


def values_for(lead, cfg):
    c = cfg["company"]
    sig = json.loads(lead["signals"] or "{}")
    kind, action = NICHE_TEXT.get(lead["niche"], ("local businesses", "book"))
    return {
        "name": lead["name"], "domain": lead["domain"], "city": lead["city"], "kind": kind, "action": action,
        "opener": opener(lead, sig), "offer": c.get("offer", ""),
        "demo_sentence": f"You can hear one right now: call {c['demo_line']} and try to {action}." if c.get("demo_line") else "",
        "booking_sentence": f"You can pick a time here: {c['booking_link']}" if c.get("booking_link") else "",
        "sender_name": c.get("sender_name") or "[YOUR NAME]", "sender_title": c.get("sender_title", ""),
        "company": c["name"], "website": c["website"],
        "whatsapp_line": f"WhatsApp: {cfg['whatsapp']['number']}" if cfg.get("whatsapp", {}).get("number") else "",
        "postal_address": c.get("postal_address") or "[POSTAL ADDRESS]",
    }


FOOTER = """
{sender_name}
{sender_title}, {company}
{website}
{whatsapp_line}

{company}, {postal_address}
Not relevant? Reply "no thanks" and I won't email you again."""


def build(lead, step, cfg):
    steps = load_steps(lead["niche"])
    subject, body = steps[step]
    vals = values_for(lead, cfg)
    if step > 1:  # follow-ups go in the same thread
        subject = "Re: " + steps[1][0]
    return render(subject, vals), render(body + "\n" + FOOTER, vals)


def draft_all(con, cfg):
    """Create step-1 drafts for qualified leads and follow-up drafts where one is due. Returns counts."""
    s = cfg["sending"]
    new = follow = 0
    for lead in con.execute("SELECT * FROM leads WHERE status='enriched' AND channel='email' AND score>=? "
                            "ORDER BY score DESC", (s["min_score"],)).fetchall():
        subject, body = build(lead, 1, cfg)
        con.execute("INSERT INTO messages(lead_id,step,subject,body,created_at) VALUES(?,?,?,?,?)",
                    (lead["id"], 1, subject, body, db.now()))
        db.update_lead(con, lead["id"], status="drafted")
        new += 1

    gaps = s["followup_days"]
    for lead in con.execute("SELECT * FROM leads WHERE status='contacted'").fetchall():
        if con.execute("SELECT 1 FROM messages WHERE lead_id=? AND status IN ('draft','approved')",
                       (lead["id"],)).fetchone():
            continue
        last = con.execute("SELECT * FROM messages WHERE lead_id=? AND status='sent' ORDER BY step DESC LIMIT 1",
                           (lead["id"],)).fetchone()
        if not last:
            continue
        if last["step"] > len(gaps):  # sequence finished
            if db.days_since(last["sent_at"]) >= 7:
                db.update_lead(con, lead["id"], status="no_reply")
            continue
        if db.days_since(last["sent_at"]) >= gaps[last["step"] - 1] and last["step"] + 1 in load_steps(lead["niche"]):
            subject, body = build(lead, last["step"] + 1, cfg)
            status = "approved" if s.get("auto_approve_followups") else "draft"
            con.execute("INSERT INTO messages(lead_id,step,subject,body,status,created_at) VALUES(?,?,?,?,?,?)",
                        (lead["id"], last["step"] + 1, subject, body, status, db.now()))
            follow += 1
    con.commit()
    return new, follow


def rebuild_first(con, cfg, lead_id):
    """Re-render the pending first email after the lead's opener or details changed."""
    lead = con.execute("SELECT * FROM leads WHERE id=?", (lead_id,)).fetchone()
    subject, body = build(lead, 1, cfg)
    con.execute("UPDATE messages SET subject=?, body=? WHERE lead_id=? AND step=1 AND status IN ('draft','approved')",
                (subject, body, lead_id))


def call_script(lead, cfg):
    """What to say when phoning a lead that cannot be emailed."""
    v = values_for(lead, cfg)
    return "\n\n".join([
        f"Hi, this is {v['sender_name']} from {v['company']}. Could I speak to the owner or the manager?",
        f"(When connected) Thanks for taking the call, I'll be quick. We build AI phone agents for {v['kind']}. "
        f"It answers the calls your team can't get to, after hours or when the line is busy, and lets the caller "
        f"{v['action']} straight away.",
        f"Can I ask, what happens today when someone calls {v['name']} and nobody can pick up?",
        f"(Listen. Then) Would it be useful if I set up a free demo agent for {v['name']} that you can call "
        f"yourself? It takes me a day and costs you nothing.",
        "(If yes) Great. What is the best email to send the demo number to?",
    ])


def linkedin_note(lead, cfg):
    """Short connection note for the owner or manager. Sent by hand, never automated."""
    v = values_for(lead, cfg)
    return (f"Hi, I came across {v['name']} while looking at {v['kind']} in {v['city'] or 'your area'}. "
            f"I build AI phone agents that answer missed and after-hours calls. Happy to connect.")


def reply_suggestions(lead, cfg):
    """Starting points for answering a reply. Edit before sending from your own mailbox."""
    v = values_for(lead, cfg)
    link = cfg["company"].get("booking_link")
    book = f" You can pick a time here: {link}" if link else " What day and time suits you this week?"
    sign = f"\n\n{v['sender_name']}\n{v['company']}"
    return [
        ("They are interested",
         f"Thanks for getting back to me. The easiest next step is a 15-minute call so I can ask a few questions "
         f"about how calls work at {v['name']} today.{book}\n\nAfter that I will set up a demo agent using your "
         f"real opening hours, so you can call it and judge for yourself." + sign),
        ("They ask about price",
         f"Good question. It depends on how many calls you get and which systems the agent has to connect to, so I "
         f"would rather give you an exact figure than a guess. It is a one-time setup fee plus a monthly plan, and "
         f"the demo is free either way.{book}" + sign),
        ("They ask how it works",
         f"Happy to explain. Your phone line forwards to the agent only when you choose: after hours, when the line "
         f"is busy, or after a few rings. The agent answers in a natural voice, lets the caller {v['action']}, and "
         f"sends you a summary. Anything unusual is passed to your staff.\n\nThe quickest way to see it is to call "
         f"a demo I set up for {v['name']}.{book}" + sign),
        ("Not now / maybe later",
         "Understood, and thanks for letting me know. I will leave it there. If missed calls become a priority "
         "later, just reply to this email." + sign),
    ]


def whatsapp_message(lead, cfg, step=1):
    """Short, personal, says who is writing and how to stop it. Sent by hand from your own WhatsApp."""
    v = values_for(lead, cfg)
    if step > 1:
        return (f"Hi, {v['sender_name']} from {v['company']} again. Just checking my message reached the right person "
                f"at {v['name']}. If AI call answering is not of interest, tell me and I will not message again.")
    demo = f" You can try one by calling {cfg['company']['demo_line']}." if cfg["company"].get("demo_line") else ""
    return (f"Hi {v['name']} team, this is {v['sender_name']} from {v['company']} ({v['website']}). "
            f"We build AI phone agents for {v['kind']}: they answer the calls you miss, after hours or when the line "
            f"is busy, and let the caller {v['action']}.{demo} "
            f"Would it help if I set up a free demo for {v['name']} that you can call yourself? "
            f"If this is not for you, reply STOP and I will not message again.")


def whatsapp_quick_replies(cfg):
    """Ready answers for the questions prospects ask in chat."""
    c = cfg["company"]
    book = f" You can pick a time here: {c['booking_link']}" if c.get("booking_link") else " What time suits you this week?"
    return [
        ("Who are you?", f"We are {c['name']}, an AI automation company. We build voice agents, chatbots and booking systems "
                         f"for businesses. More here: {c['website']}"),
        ("How does it work?", "Your phone line forwards to the agent only when you choose: after hours, when busy, or after a "
                              "few rings. It answers in a natural voice, takes the booking or the message, and sends you a "
                              "summary. Anything unusual goes to your staff."),
        ("How much is it?", "It depends on call volume and what the agent connects to, so I would rather give you an exact "
                            "figure than a guess. It is a one-time setup fee plus a monthly plan, and the demo is free." + book),
        ("We miss calls after hours", "That is the most common reason people come to us. The agent picks up every call you "
                                      "cannot, at any hour, and books straight into your calendar. I can set up a demo with "
                                      "your real opening hours so you can test it."),
        ("We get too many WhatsApp messages", "We also build WhatsApp chatbots that answer common questions, take bookings "
                                              "and orders, and pass a chat to your team when it needs a person."),
        ("Book a call", "Great. A 15-minute call is enough for me to understand how calls work for you today." + book),
        ("They said no", "No problem at all, thank you for telling me. I will not message again. Wishing you a good week."),
    ]
