"""Command line for the Vintelligence growth engine. Run `python vi.py --help`."""
import argparse
import csv
import json
import sys
import time

from . import compose, config, db, enrich, inbox, mailer, sources


def cmd_find(con, cfg, a):
    t = cfg["targets"]
    pairs = [(a.niche, a.city)] if a.niche and a.city else [(n, c) for c in t["cities"] for n in t["niches"]]
    if bool(a.niche) != bool(a.city):
        sys.exit("Give both --niche and --city, or neither (to use config.toml targets).")
    for niche, city in pairs:
        try:
            found, added = sources.find_osm(con, niche, city, a.limit or t["per_search_limit"])
            print(f"  {niche:<11} {city:<28} listed with website: {found:<4} new leads: {added}")
        except Exception as ex:
            print(f"  {niche:<11} {city:<28} FAILED: {ex}")
        time.sleep(2)


def cmd_import(con, cfg, a):
    total, added = sources.import_csv(con, a.file, a.niche, a.country)
    print(f"Read {total} rows, added {added} new leads.")


def cmd_enrich(con, cfg, a):
    n = enrich.enrich_new(con, cfg, a.limit, retry=getattr(a, "retry", False))
    print(f"Enriched {n} leads.")


def cmd_draft(con, cfg, a):
    enrich.rescore(con, cfg)
    new, follow = compose.draft_all(con, cfg)
    print(f"New first-touch drafts: {new}. Follow-ups queued: {follow}.")


def cmd_review(con, cfg, a):
    rows = con.execute("SELECT m.*, l.name, l.email, l.score, l.country FROM messages m JOIN leads l ON l.id=m.lead_id "
                       "WHERE m.status=? ORDER BY l.score DESC LIMIT ?", (a.status, a.limit)).fetchall()
    for m in rows:
        print(f"\n{'=' * 78}\nmessage #{m['id']}  step {m['step']}  ->  {m['name']} <{m['email']}>  "
              f"[{m['country']}, score {m['score']}]\nSubject: {m['subject']}\n{'-' * 78}\n{m['body']}")
    print(f"\n{len(rows)} message(s) with status '{a.status}'.")


def cmd_approve(con, cfg, a):
    if a.all:
        n = con.execute("UPDATE messages SET status='approved' WHERE status='draft'").rowcount
    else:
        n = sum(con.execute("UPDATE messages SET status='approved' WHERE status='draft' AND id=?", (i,)).rowcount
                for i in a.ids)
    con.commit()
    print(f"Approved {n} message(s).")


def cmd_reject(con, cfg, a):
    for i in a.ids:
        con.execute("UPDATE messages SET status='cancelled' WHERE id=? AND status IN ('draft','approved')", (i,))
    con.commit()
    print(f"Cancelled {len(a.ids)} message(s).")


def cmd_send(con, cfg, a):
    n = mailer.send_approved(con, cfg, live=a.live, limit=a.limit)
    print(f"{'Sent' if a.live else 'Dry run:'} {n} message(s)." + ("" if a.live else "  Add --live to really send."))


def cmd_inbox(con, cfg, a):
    if not inbox.configured():
        print("Inbox check skipped: add IMAP_HOST, IMAP_USER and IMAP_PASS to .env")
        return
    counts = inbox.check(con)
    print("Inbox:", ", ".join(f"{k}={v}" for k, v in counts.items()) or "nothing new from leads")


def cmd_signals(con, cfg, a):
    added, errors = sources.find_intent(con, cfg["intent"]["queries"], cfg["intent"]["lookback_days"])
    for e in errors:
        print("  source problem:", e)
    print(f"{added} new post(s).")
    for r in con.execute("SELECT * FROM intent WHERE status='new' ORDER BY posted_at DESC LIMIT ?", (a.limit,)):
        print(f"\n#{r['id']} [{r['source']}] {r['title']}\n  {r['url']}\n  {r['snippet'][:220]}")


def cmd_done(con, cfg, a):
    for i in a.ids:
        con.execute("UPDATE intent SET status='done' WHERE id=?", (i,))
    con.commit()


def cmd_leads(con, cfg, a):
    q, args = "SELECT * FROM leads WHERE 1=1", []
    for col in ("status", "channel", "niche", "country"):
        if getattr(a, col):
            q += f" AND {col}=?"
            args.append(getattr(a, col).upper() if col == "country" else getattr(a, col))
    rows = con.execute(q + " ORDER BY score DESC, id LIMIT ?", (*args, a.limit)).fetchall()
    for r in rows:
        print(f"{r['id']:>5} {r['score']:>3} {r['status']:<10} {r['channel']:<6} {r['country']:<2} {r['niche']:<10} "
              f"{r['name'][:30]:<30} {r['email'] or r['phone'] or r['domain']}")
    print(f"{len(rows)} lead(s).")


def cmd_show(con, cfg, a):
    r = con.execute("SELECT * FROM leads WHERE id=?", (a.id,)).fetchone()
    if not r:
        sys.exit("No such lead.")
    for k in r.keys():
        print(f"{k:<11} {r[k]}")
    print("\nHistory:")
    for m in con.execute("SELECT * FROM messages WHERE lead_id=? ORDER BY id", (a.id,)):
        print(f"  message #{m['id']} step {m['step']} {m['status']} {m['sent_at'] or ''}  {m['subject']}")
    for e in con.execute("SELECT * FROM events WHERE lead_id=? ORDER BY id", (a.id,)):
        print(f"  {e['at']} {e['kind']}: {e['detail'][:160]}")


def cmd_set(con, cfg, a):
    lead = con.execute("SELECT * FROM leads WHERE id=?", (a.id,)).fetchone()
    if not lead:
        sys.exit("No such lead.")
    fields = {k: getattr(a, k) for k in ("status", "email", "opener", "channel") if getattr(a, k) is not None}
    if a.note:
        fields["notes"] = (lead["notes"] + "\n" if lead["notes"] else "") + f"{db.now()[:10]} {a.note}"
    if not fields:
        sys.exit("Nothing to change.")
    db.update_lead(con, a.id, **fields)
    if "opener" in fields and lead["status"] == "drafted":  # rebuild the pending first email with the new opener
        fresh = con.execute("SELECT * FROM leads WHERE id=?", (a.id,)).fetchone()
        subject, body = compose.build(fresh, 1, cfg)
        con.execute("UPDATE messages SET subject=?, body=? WHERE lead_id=? AND step=1 AND status IN ('draft','approved')",
                    (subject, body, a.id))
    db.log(con, a.id, "manual", json.dumps(fields)[:300])
    con.commit()
    print("Updated.")


def cmd_suppress(con, cfg, a):
    db.suppress(con, a.value, a.reason)
    con.commit()
    print(f"{a.value} will never be contacted.")


def cmd_report(con, cfg, a):
    one = lambda q, *p: con.execute(q, p).fetchone()[0]
    print("PIPELINE")
    counts = dict(con.execute("SELECT status, COUNT(*) FROM leads GROUP BY status").fetchall())
    for s in db.STATUSES:
        if counts.get(s):
            print(f"  {s:<12} {counts[s]}")
    contacted = one("SELECT COUNT(DISTINCT lead_id) FROM messages WHERE status='sent'")
    replied = one("SELECT COUNT(DISTINCT lead_id) FROM events WHERE kind='reply'")
    print("\nOUTREACH")
    print(f"  emails sent today / cap   {mailer.sent_today(con)} / {cfg['sending']['daily_cap']}")
    print(f"  emails sent, last 7 days  {one('SELECT COUNT(*) FROM messages WHERE status=? AND sent_at>=date(?,?)', 'sent', 'now', '-7 days')}")
    print(f"  businesses contacted      {contacted}")
    print(f"  replied                   {replied}" + (f"  ({replied / contacted:.0%})" if contacted else ""))
    print(f"  drafts waiting for you    {one('SELECT COUNT(*) FROM messages WHERE status=?', 'draft')}")
    print(f"  approved, not yet sent    {one('SELECT COUNT(*) FROM messages WHERE status=?', 'approved')}")
    print("\nNEEDS YOU")
    print(f"  replies to answer         {counts.get('replied', 0)}   (python vi.py leads --status replied)")
    print(f"  manual-channel leads      {one('SELECT COUNT(*) FROM leads WHERE channel=? AND status=?', 'manual', 'enriched')}   (python vi.py leads --channel manual)")
    print(f"  intent posts to answer    {one('SELECT COUNT(*) FROM intent WHERE status=?', 'new')}   (python vi.py signals)")
    problems = mailer.preflight(cfg)
    if problems:
        print("\nBEFORE LIVE SENDING\n  - " + "\n  - ".join(problems))


def cmd_export(con, cfg, a):
    cur = con.execute("SELECT * FROM leads ORDER BY score DESC")
    with open(a.file, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow([d[0] for d in cur.description])
        w.writerows(cur)
    print(f"Wrote {a.file}")


def cmd_run(con, cfg, a):
    """The daily routine. Never sends anything new without your approval."""
    print("1/5 inbox");   cmd_inbox(con, cfg, a)
    print("2/5 find");    cmd_find(con, cfg, argparse.Namespace(niche=None, city=None, limit=None))
    print("3/5 enrich");  cmd_enrich(con, cfg, argparse.Namespace(limit=60))
    print("4/5 draft");   cmd_draft(con, cfg, a)
    from . import opps, youtube
    print("extra: project boards"); print(f"  {opps.search(con, cfg)} new project(s)")
    if youtube.connected():
        print("extra: youtube"); youtube.scan(con, cfg); youtube.upload_due(con, cfg)
    mailer.autopilot(con, cfg)
    print("5/5 report");  cmd_report(con, cfg, a)


def cmd_ui(con, cfg, a):
    from . import web
    con.close()
    web.serve(a.port, not a.no_browser)
    sys.exit(0)


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8", errors="replace")
    p = argparse.ArgumentParser(prog="vi.py", description="Vintelligence growth engine")
    sub = p.add_subparsers(dest="cmd", required=True)

    def add(name, fn, help):
        sp = sub.add_parser(name, help=help)
        sp.set_defaults(fn=fn)
        return sp

    sp = add("find", cmd_find, "find businesses from OpenStreetMap (no args = config.toml targets)")
    sp.add_argument("--niche", choices=sorted(sources.NICHES)); sp.add_argument("--city"); sp.add_argument("--limit", type=int)
    sp = add("import", cmd_import, "import leads from a CSV file")
    sp.add_argument("file"); sp.add_argument("--niche", default=""); sp.add_argument("--country", default="")
    sp = add("enrich", cmd_enrich, "visit new leads' websites: email, phone, signals, score")
    sp.add_argument("--limit", type=int, default=50)
    sp.add_argument("--retry", action="store_true", help="also retry sites that could not be reached before")
    add("draft", cmd_draft, "write first emails for qualified leads and queue due follow-ups")
    sp = add("review", cmd_review, "read drafts before approving")
    sp.add_argument("--status", default="draft"); sp.add_argument("--limit", type=int, default=20)
    sp = add("approve", cmd_approve, "approve drafts for sending")
    sp.add_argument("ids", nargs="*", type=int); sp.add_argument("--all", action="store_true")
    sp = add("reject", cmd_reject, "cancel drafts")
    sp.add_argument("ids", nargs="+", type=int)
    sp = add("send", cmd_send, "send approved emails (dry run unless --live)")
    sp.add_argument("--live", action="store_true"); sp.add_argument("--limit", type=int)
    add("inbox", cmd_inbox, "process replies, opt-outs and bounces")
    sp = add("signals", cmd_signals, "find public posts asking for AI voice/automation help")
    sp.add_argument("--limit", type=int, default=15)
    sp = add("done", cmd_done, "mark intent posts as handled")
    sp.add_argument("ids", nargs="+", type=int)
    sp = add("leads", cmd_leads, "list leads")
    for col in ("status", "channel", "niche", "country"):
        sp.add_argument("--" + col)
    sp.add_argument("--limit", type=int, default=40)
    sp = add("show", cmd_show, "everything about one lead")
    sp.add_argument("id", type=int)
    sp = add("set", cmd_set, "update a lead: status, note, custom opener, email, channel")
    sp.add_argument("id", type=int); sp.add_argument("--status", choices=db.STATUSES); sp.add_argument("--note")
    sp.add_argument("--opener"); sp.add_argument("--email"); sp.add_argument("--channel", choices=["email", "manual"])
    sp = add("suppress", cmd_suppress, "never contact an email address or domain again")
    sp.add_argument("value"); sp.add_argument("--reason", default="manual")
    add("report", cmd_report, "pipeline and outreach numbers")
    sp = add("export", cmd_export, "export all leads to CSV")
    sp.add_argument("file")
    add("run", cmd_run, "daily routine: inbox, find, enrich, draft, report (does not send)")

    sp = add("ui", cmd_ui, "open the dashboard in your browser")
    sp.add_argument("--port", type=int, default=8765); sp.add_argument("--no-browser", action="store_true")

    a = p.parse_args(argv)
    con = db.connect()
    try:
        a.fn(con, config.load(), a)
    finally:
        try:
            con.commit()
            con.close()
        except Exception:
            pass  # already closed by the dashboard command
