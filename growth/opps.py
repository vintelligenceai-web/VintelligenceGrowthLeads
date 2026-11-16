"""Opportunities: public project and job posts asking for work Vintelligence can deliver, with a proposal draft.

Sources are public APIs only. Applying is done by hand on the platform, from your own account.
"""
import datetime
import json
import re
import time
import urllib.parse

from . import db, net

# (pattern, what we truthfully offer, a question to ask the client). Taken from the services on the website.
SERVICES = [
    (r"voice|phone|\bcall|ivr|telephon|twilio|vapi|retell|receptionist",
     "AI voice agents that answer and make phone calls, book appointments and hand over to a person when needed",
     "Roughly how many calls a day should the agent handle, and which phone system or number do you use today?"),
    (r"chat\s?bot|whatsapp|messenger|telegram|instagram|conversational",
     "AI chatbots for website, WhatsApp, Facebook, Instagram and Telegram",
     "Which channels matter most, and where should a conversation go when it needs a human?"),
    (r"automat|workflow|\bn8n\b|zapier|make\.com|integrat|\bcrm\b",
     "workflow automation that connects calls, bookings, CRM and other systems",
     "Which tools need to be connected, and what triggers the process today?"),
    (r"booking|appointment|scheduling|calendar|reservation",
     "appointment booking systems with calendar sync and automatic reminders",
     "Which calendar or booking tool do you use now?"),
    (r"\bpos\b|point of sale|inventory",
     "point-of-sale systems connected to bookings and voice agents",
     "Which POS or payment provider is in use today?"),
    (r"\bllm\b|\bgpt|claude|openai|ai agent|\brag\b|langchain|generative ai",
     "custom AI agents built on large language models",
     "What data or documents should the agent work from?"),
]
STRONG = re.compile(r"voice agent|ai receptionist|ai phone|voice ai|vapi|retell|ai call", re.I)


def match(text):
    """-> (score 0-100, [offers], [questions]) for a post."""
    offers, questions = [], []
    for pattern, offer, question in SERVICES:
        if re.search(pattern, text, re.I):
            offers.append(offer)
            questions.append(question)
    score = min(100, len(offers) * 20 + (30 if STRONG.search(text) else 0))
    return score, offers, questions


def rank(title, body):
    """Relevance 0-100. The title must be about our work; long descriptions mention everything."""
    in_title = sum(1 for pattern, _, _ in SERVICES if re.search(pattern, title, re.I))
    in_body = sum(1 for pattern, _, _ in SERVICES if re.search(pattern, body, re.I))
    if not in_title:
        return 0
    return min(100, in_title * 30 + in_body * 8 + (30 if STRONG.search(title + " " + body) else 0))


def proposal(title, text, cfg):
    """A truthful first draft. No invented experience, numbers or client names."""
    c = cfg["company"]
    _, offers, questions = match(title + " " + text)
    name = c.get("sender_name") or "[YOUR NAME]"
    lines = [
        "Hi,", "",
        f"I read your post “{title.strip()}”. This is the kind of work we do at {c['name']}: "
        + ("; ".join(offers[:3]) if offers else "AI automation for small and mid-sized businesses") + ".", "",
        "How I would approach it:",
        "1. A short call, or a few written questions, to pin down the exact flow and the systems involved.",
        "2. A working first version within days that you can test yourself.",
        "3. Changes from your feedback, then a handover with documentation.", "",
    ]
    if c.get("demo_line"):
        lines += [f"You can hear one of our agents right now by calling {c['demo_line']}.", ""]
    lines += ["A few questions so I can give you a fixed price and timeline:"]
    lines += [f"- {q}" for q in questions[:3]] + ["- What would make this a success for you in the first month?", "",
                                                  f"{name}", f"{c['name']} · {c['website']}"]
    return "\n".join(lines)


def _strip(s):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", s or "")).strip()


def _fetch_json(url):
    for attempt in range(2):
        try:
            return json.loads(net.fetch(url, timeout=30, max_bytes=8_000_000)[1])
        except Exception:
            if attempt:
                raise
            time.sleep(3)


def freelancer(query):
    q = urllib.parse.urlencode({"query": query, "limit": 30, "job_details": "true", "full_description": "true"})
    data = _fetch_json("https://www.freelancer.com/api/projects/0.1/projects/active/?" + q)
    for p in data.get("result", {}).get("projects", []):
        b, cur = p.get("budget") or {}, (p.get("currency") or {}).get("code", "")
        budget = f"{b.get('minimum', 0):.0f}–{b.get('maximum') or b.get('minimum', 0):.0f} {cur}" + \
                 (" per hour" if p.get("type") == "hourly" else "")
        posted = datetime.datetime.fromtimestamp(p.get("time_submitted", 0), datetime.timezone.utc).isoformat(timespec="seconds")
        yield {"source": "freelancer.com", "url": f"https://www.freelancer.com/projects/{p.get('seo_url', '')}",
               "title": p.get("title", ""), "company": f"{(p.get('bid_stats') or {}).get('bid_count', 0)} bids so far",
               "snippet": _strip(p.get("description") or p.get("preview_description"))[:900], "budget": budget,
               "tags": ", ".join(j.get("name", "") for j in p.get("jobs", [])[:6]), "posted_at": posted}


def remotive(query):
    data = _fetch_json("https://remotive.com/api/remote-jobs?" + urllib.parse.urlencode({"search": query, "limit": 30}))
    for j in data.get("jobs", []):
        if j.get("job_type") not in ("contract", "freelance", "part_time"):
            continue  # an agency wants project work, not full-time employment
        yield {"source": "remotive.com", "url": j.get("url", ""), "title": j.get("title", ""),
               "company": j.get("company_name", ""), "snippet": _strip(j.get("description"))[:900],
               "budget": j.get("salary", ""), "tags": ", ".join(j.get("tags", [])[:6]),
               "posted_at": j.get("publication_date", "")}


def hn_freelance(query):
    since = int(time.time()) - 35 * 86400
    q = urllib.parse.urlencode({"query": f'"SEEKING FREELANCER" {query}', "tags": "comment",
                                "numericFilters": f"created_at_i>{since}", "hitsPerPage": 20})
    for h in _fetch_json("https://hn.algolia.com/api/v1/search_by_date?" + q).get("hits", []):
        text = _strip(h.get("comment_text"))
        if "SEEKING FREELANCER" not in text.upper():
            continue
        yield {"source": "hackernews", "url": f"https://news.ycombinator.com/item?id={h['objectID']}",
               "title": text[:90], "company": h.get("author", ""), "snippet": text[:900], "budget": "", "tags": "",
               "posted_at": h.get("created_at", "")}


SOURCES = {"freelancer.com": freelancer, "remotive.com": remotive, "hackernews": hn_freelance}


def search(con, cfg, log=print, min_score=45):
    """Collect matching posts from every source. Returns number of new opportunities."""
    added = 0
    for query in cfg["opportunities"]["queries"]:
        for name, fn in SOURCES.items():
            try:
                for o in fn(query):
                    if not o["url"] or not o["title"]:
                        continue
                    score = rank(o["title"], o["snippet"] + " " + o["tags"])
                    if score < min_score:
                        continue
                    cur = con.execute(
                        "INSERT OR IGNORE INTO opps(source,url,title,company,snippet,budget,tags,posted_at,score,proposal,found_at)"
                        " VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                        (o["source"], o["url"], o["title"], o["company"], o["snippet"], o["budget"], o["tags"],
                         o["posted_at"], score, proposal(o["title"], o["snippet"] + " " + o["tags"], cfg), db.now()))
                    added += cur.rowcount
            except Exception as ex:
                log(f"  {name} “{query}”: could not read ({ex})")
            time.sleep(1.5)
        con.commit()
        log(f"  searched “{query}”")
    return added
