"""Control centre: `python vi.py ui`. Runs only on this computer (127.0.0.1).

Everything the command line can do, with buttons. Emails still need a human: a draft must be
approved, and live sending needs the "Send now" button plus a ticked confirmation.
"""
import html
import json
import re
import secrets
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import compose, config, db, enrich, inbox, mailer, market, opps, sources, youtube
from .config import ROOT

TOKEN = secrets.token_urlsafe(16)  # stops other web pages from posting actions to this one
e = lambda v: html.escape(str(v if v is not None else ""))
MOVE_TO = ["contacted", "replied", "meeting", "proposal", "won", "lost", "no_reply"]
BOARD = ["contacted", "replied", "meeting", "proposal", "won"]
NICHE_LABEL = {k: v[0].capitalize() for k, v in compose.NICHE_TEXT.items()}

# ---------------------------------------------------------------- background jobs
JOB = {"id": 0, "name": "", "lines": [], "running": False}
_lock = threading.Lock()


def start_job(name, fn):
    """Run fn(con, cfg, log) in the background. One job at a time."""
    with _lock:
        if JOB["running"]:
            return False
        JOB.update(id=JOB["id"] + 1, name=name, lines=[], running=True)

    def log(*parts):
        JOB["lines"].append(" ".join(str(p) for p in parts))

    def work():
        con = db.connect()
        try:
            fn(con, config.load(), log)
            log("Finished.")
        except Exception as ex:
            log(f"Stopped with a problem: {ex}")
        finally:
            con.commit()
            con.close()
            JOB["running"] = False

    threading.Thread(target=work, daemon=True).start()
    return True


def job_find(pairs, limit, then_enrich=True):
    def run(con, cfg, log):
        for niche, city in pairs:
            log(f"Searching {NICHE_LABEL.get(niche, niche)} in {city} ...")
            try:
                found, added = sources.find_osm(con, niche, city, limit)
                log(f"  {found} listed with a website, {added} new")
            except Exception as ex:
                log(f"  could not search: {ex}")
            time.sleep(2)
        if then_enrich:
            job_enrich(80)(con, cfg, log)
    return run


def job_enrich(limit=80, retry=False):
    def run(con, cfg, log):
        log("Reading websites (email, phone, what they already automate) ...")
        n = enrich.enrich_new(con, cfg, limit, progress=log, retry=retry)
        enrich.rescore(con, cfg)
        new, follow = compose.draft_all(con, cfg)
        log(f"Read {n} websites. New drafts: {new}. Follow-ups queued: {follow}.")
    return run


def job_inbox(con, cfg, log):
    if not inbox.configured():
        log("Inbox is not connected yet. Add the mailbox in Settings.")
        return
    counts = inbox.check(con, out=log)
    log("Inbox: " + (", ".join(f"{k}={v}" for k, v in counts.items()) or "nothing new from leads"))


def job_signals(con, cfg, log):
    added, errors = sources.find_intent(con, cfg["intent"]["queries"], cfg["intent"]["lookback_days"])
    for err in errors:
        log("source problem:", err)
    log(f"{added} new post(s) found.")


def job_opps(con, cfg, log):
    log("Searching project boards ...")
    log(f"{opps.search(con, cfg, log)} new project(s) worth a bid.")


def job_youtube(con, cfg, log):
    log(f"{youtube.scan(con, cfg)} new video file(s) queued.")
    log(f"{youtube.upload_due(con, cfg, log)} video(s) uploaded.")


def job_daily(con, cfg, log):
    job_inbox(con, cfg, log)
    t = cfg["targets"]
    job_find([(n, c) for c in t["cities"] for n in t["niches"]], t["per_search_limit"])(con, cfg, log)
    job_signals(con, cfg, log)
    job_opps(con, cfg, log)
    if youtube.connected():
        job_youtube(con, cfg, log)
    mailer.autopilot(con, cfg, log)


def job_send(live):
    def run(con, cfg, log):
        n = mailer.send_approved(con, cfg, live=live, out=log)
        log(f"{'Sent' if live else 'Test run, nothing left this computer. Would send'} {n} email(s).")
    return run


# ---------------------------------------------------------------- page pieces
CSS = """
:root{--bg:#0b0d14;--side:#0f1220;--card:#151929;--card2:#1b2033;--ink:#eef0f7;--mute:#8b93a9;--line:#252b40;--field:#0e111c;
--acc:#7c5cff;--acc2:#22d3ee;--ok:#34d399;--warn:#fbbf24;--bad:#fb7185;--onok:#04130c;--shadow:0 12px 40px rgba(0,0,0,.55);--glow:rgba(124,92,255,.35);color-scheme:dark}
:root[data-theme=light]{--bg:#f4f6fb;--side:#ffffff;--card:#ffffff;--card2:#eef1f8;--ink:#141a2b;--mute:#5d6883;--line:#dfe4ef;--field:#ffffff;
--acc:#6246ea;--acc2:#0891b2;--ok:#0f9d6b;--warn:#b45309;--bad:#d92d50;--onok:#ffffff;--shadow:0 12px 34px rgba(30,41,80,.14);--glow:rgba(98,70,234,.22);color-scheme:light}
*{box-sizing:border-box}
@view-transition{navigation:auto}
::view-transition-old(root){animation:.18s ease both vt-out}::view-transition-new(root){animation:.28s ease both vt-in}
@keyframes vt-out{to{opacity:0}}@keyframes vt-in{from{opacity:0}}
@keyframes rise{from{opacity:0;transform:translateY(14px)}to{opacity:1;transform:none}}
@keyframes grow{from{transform:scaleX(0)}to{transform:scaleX(1)}}
@keyframes pulse{0%{box-shadow:0 0 0 0 var(--glow)}70%{box-shadow:0 0 0 12px transparent}100%{box-shadow:0 0 0 0 transparent}}
@keyframes drift{0%{transform:translate(0,0) scale(1)}50%{transform:translate(4%,6%) scale(1.12)}100%{transform:translate(0,0) scale(1)}}
body::before{content:"";position:fixed;inset:-20% -10% auto auto;width:60vw;height:60vw;z-index:-1;pointer-events:none;filter:blur(90px);opacity:.5;
background:radial-gradient(circle at 30% 30%,var(--glow),transparent 60%),radial-gradient(circle at 70% 60%,rgba(34,211,238,.16),transparent 55%);animation:drift 22s ease-in-out infinite}
main>*{animation:rise .45s cubic-bezier(.2,.7,.2,1) both}main>*:nth-child(3){animation-delay:.05s}main>*:nth-child(4){animation-delay:.1s}main>*:nth-child(5){animation-delay:.15s}main>*:nth-child(n+6){animation-delay:.2s}
.grid>*{animation:rise .5s cubic-bezier(.2,.7,.2,1) both}.grid>*:nth-child(2){animation-delay:.06s}.grid>*:nth-child(3){animation-delay:.12s}.grid>*:nth-child(4){animation-delay:.18s}.grid>*:nth-child(n+5){animation-delay:.24s}
.bar i{transform-origin:left;animation:grow .9s cubic-bezier(.2,.7,.2,1) both .15s}
.card,button,.btn,aside a,input,select,textarea,.tile{transition:border-color .18s,background-color .18s,transform .18s,box-shadow .18s,color .18s}
.card:hover{box-shadow:var(--shadow)}.tile:hover{transform:translateY(-2px);border-color:var(--acc)}
button:active,.btn:active{transform:scale(.97)}button.go:hover{box-shadow:0 6px 22px var(--glow)}
.kpi.act{animation:rise .5s both,pulse 2.4s ease-out 1s 2}
.theme{margin:18px 8px 0;width:calc(100% - 16px);display:flex;justify-content:center;gap:8px;align-items:center}
@media(prefers-reduced-motion:reduce){*,::view-transition-old(root),::view-transition-new(root){animation:none!important;transition:none!important}}
body{margin:0;font:15px/1.55 Inter,system-ui,"Segoe UI",sans-serif;background:var(--bg);color:var(--ink)}
a{color:var(--acc2);text-decoration:none}a:hover{text-decoration:underline}
.app{display:grid;grid-template-columns:232px 1fr;min-height:100vh}
aside{background:var(--side);border-right:1px solid var(--line);padding:20px 14px;position:sticky;top:0;height:100vh;overflow:auto}
.logo{font-weight:800;font-size:18px;letter-spacing:.3px;margin:2px 8px 22px;background:linear-gradient(90deg,var(--acc),var(--acc2));-webkit-background-clip:text;background-clip:text;color:transparent}
.logo small{display:block;font-weight:500;font-size:11px;letter-spacing:1.5px;color:var(--mute);-webkit-text-fill-color:var(--mute)}
aside a{display:flex;justify-content:space-between;align-items:center;padding:9px 12px;border-radius:9px;color:var(--mute);margin-bottom:2px;font-weight:500}
aside a:hover{background:var(--card);color:var(--ink);text-decoration:none}
aside a.on{background:linear-gradient(90deg,var(--glow),rgba(34,211,238,.10));color:var(--ink);font-weight:700}
aside .sep{font-size:11px;letter-spacing:1.4px;color:#5c6480;margin:18px 12px 6px;text-transform:uppercase}
.badge{background:var(--acc);color:#fff;border-radius:99px;font-size:11px;padding:1px 8px;font-weight:700}
.badge.hot{background:var(--bad)}
main{padding:28px 34px 120px;max-width:1280px;width:100%}
h1{font-size:24px;margin:0 0 4px}h2{font-size:15px;margin:28px 0 12px;color:var(--mute);text-transform:uppercase;letter-spacing:1.2px;font-weight:600}
.sub{color:var(--mute);margin:0 0 22px}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(190px,1fr));gap:14px}
.two{display:grid;grid-template-columns:1fr 1fr;gap:16px}@media(max-width:1000px){.two{grid-template-columns:1fr}.app{grid-template-columns:1fr}aside{position:static;height:auto}}
.card{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:18px 20px}
a.card{display:block;color:inherit;transition:.15s}a.card:hover{border-color:var(--acc);transform:translateY(-2px);text-decoration:none}
.kpi .n{font-size:32px;font-weight:750;line-height:1.1}.kpi .l{color:var(--mute);font-size:13px;margin-top:4px}
.kpi.act{border-color:rgba(124,92,255,.55);background:linear-gradient(135deg,rgba(124,92,255,.16),var(--card) 60%)}
table{width:100%;border-collapse:collapse}th,td{text-align:left;padding:10px 12px;border-bottom:1px solid var(--line);font-size:14px;vertical-align:top}
th{color:var(--mute);font-weight:600;font-size:11px;text-transform:uppercase;letter-spacing:.8px}tr:last-child td{border-bottom:0}
tbody tr:hover{background:var(--card2)}
.pill{display:inline-block;padding:2px 10px;border-radius:99px;font-size:12px;font-weight:600;background:var(--card2);border:1px solid var(--line);white-space:nowrap}
.pill.replied,.pill.meeting,.pill.proposal{color:var(--warn);border-color:rgba(251,191,36,.4)}.pill.won,.pill.yes{color:var(--ok);border-color:rgba(52,211,153,.4)}
.pill.lost,.pill.bounced,.pill.suppressed,.pill.no{color:var(--bad);border-color:rgba(251,113,133,.4)}.pill.contacted,.pill.drafted{color:var(--acc2);border-color:rgba(34,211,238,.35)}
.score{display:inline-block;min-width:38px;text-align:center;padding:2px 8px;border-radius:8px;font-weight:700;background:var(--card2)}
.score.hi{background:rgba(52,211,153,.16);color:var(--ok)}.score.mid{background:rgba(251,191,36,.14);color:var(--warn)}
form.in{display:inline}label{display:block;font-size:12px;color:var(--mute);margin:12px 0 4px;font-weight:600}
input,select,textarea{font:inherit;width:100%;padding:9px 12px;border-radius:9px;border:1px solid var(--line);background:var(--field);color:var(--ink)}
input:focus,select:focus,textarea:focus{outline:2px solid var(--acc);border-color:transparent}
input[type=checkbox]{width:auto;margin-right:6px}textarea{min-height:120px;resize:vertical;line-height:1.5}
button,.btn{font:inherit;font-weight:600;padding:8px 16px;border-radius:9px;border:1px solid var(--line);background:var(--card2);color:var(--ink);cursor:pointer;display:inline-block}
button:hover,.btn:hover{border-color:var(--acc);text-decoration:none}
button.go{background:linear-gradient(90deg,var(--acc),#5b8cff);border:0;color:#fff}button.ok{background:var(--ok);border:0;color:var(--onok)}
button.no{color:var(--bad)}button.sm{padding:4px 10px;font-size:13px}button:disabled{opacity:.45;cursor:not-allowed}
.row{display:flex;gap:10px;align-items:center;flex-wrap:wrap}.row.end{justify-content:space-between}.mute{color:var(--mute)}.small{font-size:13px}
.bar{height:10px;border-radius:99px;background:var(--card2);overflow:hidden}.bar i{display:block;height:100%;background:linear-gradient(90deg,var(--acc),var(--acc2))}
.fun{display:grid;grid-template-columns:130px 1fr 50px;gap:10px;align-items:center;margin:9px 0;font-size:14px}
.fun .bar{height:22px;border-radius:7px}
.check{display:flex;gap:12px;align-items:flex-start;padding:10px 0;border-bottom:1px solid var(--line)}.check:last-child{border:0}
.dot{flex:none;width:22px;height:22px;border-radius:50%;border:2px solid var(--line);display:grid;place-items:center;font-size:12px;margin-top:1px}
.dot.done{background:var(--ok);border-color:var(--ok);color:var(--onok)}
.note{border-left:3px solid var(--warn);background:rgba(251,191,36,.07);padding:12px 16px;border-radius:8px;margin:14px 0}
.note.bad{border-color:var(--bad);background:rgba(251,113,133,.07)}.note.good{border-color:var(--ok);background:rgba(52,211,153,.07)}
pre.mail{white-space:pre-wrap;font:14px/1.6 inherit;margin:0;background:var(--field);border:1px solid var(--line);border-radius:10px;padding:14px 16px}
.board{display:grid;grid-template-columns:repeat(5,minmax(200px,1fr));gap:12px;overflow-x:auto}.col{background:var(--side);border:1px solid var(--line);border-radius:14px;padding:12px;min-height:220px}
.col h3{margin:2px 4px 10px;font-size:13px;text-transform:uppercase;letter-spacing:1px;color:var(--mute);display:flex;justify-content:space-between}
.tile{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:10px 12px;margin-bottom:8px;font-size:14px}
.chips span{display:inline-block;margin:0 6px 6px 0}
#job{position:fixed;right:22px;bottom:22px;width:440px;max-width:calc(100vw - 44px);background:var(--field);border:1px solid var(--acc);border-radius:14px;box-shadow:var(--shadow);display:none;z-index:9;animation:rise .3s both}
#job header{display:flex;justify-content:space-between;padding:10px 14px;border-bottom:1px solid var(--line);font-weight:600}
#job pre{margin:0;padding:10px 14px;max-height:230px;overflow:auto;font:12px/1.5 ui-monospace,Consolas,monospace;color:var(--mute);white-space:pre-wrap}
.spin{display:inline-block;width:12px;height:12px;border:2px solid var(--acc2);border-top-color:transparent;border-radius:50%;animation:s 1s linear infinite;margin-right:8px}@keyframes s{to{transform:rotate(360deg)}}
.md table{background:var(--card);border-radius:10px;margin:12px 0}.md code,code{background:var(--card2);padding:1px 6px;border-radius:5px;font-size:13px}.md li{margin:4px 0}.md h1{margin-top:8px}.md h2{margin-top:34px}
.tabs a{display:inline-block;padding:6px 14px;border-radius:99px;border:1px solid var(--line);margin:0 6px 8px 0;color:var(--mute);font-size:13px;font-weight:600}.tabs a.on{background:var(--acc);border-color:var(--acc);color:#fff}
"""

JS = """
const jobBox=document.getElementById('job'),jobLog=jobBox.querySelector('pre'),jobName=document.getElementById('jobname');
let wasRunning=false,seen=Number(sessionStorage.getItem('job')||0);
async function poll(){try{const j=await (await fetch('/job')).json();
 if(j.running||(j.id>seen&&j.id)){jobBox.style.display='block';jobName.innerHTML=(j.running?'<span class="spin"></span>':'')+j.name+(j.running?'':' - done');
  jobLog.textContent=j.lines.join('\\n');jobLog.scrollTop=jobLog.scrollHeight}
 if(wasRunning&&!j.running){const a=document.activeElement.tagName;if(a!=='TEXTAREA'&&a!=='INPUT')setTimeout(()=>location.reload(),1200)}
 wasRunning=j.running;window.jobId=j.id}catch(x){}}
function closeJob(){seen=window.jobId||0;sessionStorage.setItem('job',seen);jobBox.style.display='none'}
poll();setInterval(poll,1500);
document.addEventListener('click',ev=>{const c=ev.target.closest('[data-copy]');if(c){navigator.clipboard.writeText(document.getElementById(c.dataset.copy).innerText);c.textContent='Copied';setTimeout(()=>c.textContent='Copy',1200)}});
document.addEventListener('click',ev=>{const b=ev.target.closest('[data-copyfield]');if(b){navigator.clipboard.writeText(b.closest('form').querySelector('textarea').value);const t=b.textContent;b.textContent='Copied';setTimeout(()=>b.textContent=t,1200)}});
document.addEventListener('click',ev=>{const b=ev.target.closest('[data-wa]');if(b)window.open('https://wa.me/'+b.dataset.wa+'?text='+encodeURIComponent(b.closest('form').querySelector('textarea').value),'_blank','noopener')});
document.addEventListener('submit',ev=>{const m=ev.target.dataset.confirm;if(m&&!confirm(m))ev.preventDefault()});
function setTheme(t){document.documentElement.dataset.theme=t;localStorage.setItem('theme',t);const b=document.getElementById('theme');if(b)b.textContent=t==='light'?'🌙  Dark mode':'☀️  Light mode'}
setTheme(document.documentElement.dataset.theme||'dark');
document.getElementById('theme').addEventListener('click',()=>setTheme(document.documentElement.dataset.theme==='light'?'dark':'light'));
if(!matchMedia('(prefers-reduced-motion: reduce)').matches)document.querySelectorAll('.kpi .n').forEach(el=>{const m=el.textContent.match(/^\\d+$/);if(!m)return;const end=+m[0];if(end<2)return;const t0=performance.now();
 (function step(t){const k=Math.min(1,(t-t0)/700);el.textContent=Math.round(end*(1-Math.pow(1-k,3)));if(k<1)requestAnimationFrame(step)})(t0)});
const q=document.getElementById('q');if(q)q.addEventListener('input',()=>{const t=q.value.toLowerCase();document.querySelectorAll('tbody tr').forEach(r=>r.style.display=r.innerText.toLowerCase().includes(t)?'':'none')});
"""


def form(action, label, cls="", back="/", confirm="", **fields):
    hidden = "".join(f'<input type="hidden" name="{e(k)}" value="{e(v)}">'
                     for k, v in {"token": TOKEN, "action": action, "back": back, **fields}.items())
    c = f' data-confirm="{e(confirm)}"' if confirm else ""
    return f'<form class="in" method="post" action="/act"{c}>{hidden}<button class="{cls}">{e(label)}</button></form>'


def open_form(action, back, confirm=""):
    c = f' data-confirm="{e(confirm)}"' if confirm else ""
    return (f'<form method="post" action="/act"{c}><input type="hidden" name="token" value="{TOKEN}">'
            f'<input type="hidden" name="action" value="{e(action)}"><input type="hidden" name="back" value="{e(back)}">')


def field(name, label, value="", kind="text", hint="", rows=0):
    hint = f' <span class="mute" style="font-weight:400">{e(hint)}</span>' if hint else ""
    if rows:
        return f'<label>{e(label)}{hint}</label><textarea name="{e(name)}" style="min-height:{rows * 24}px">{e(value)}</textarea>'
    return f'<label>{e(label)}{hint}</label><input type="{kind}" name="{e(name)}" value="{e(value)}">'


def ext(url, label="website"):
    if not str(url).startswith(("http://", "https://")):
        return e(url)
    return f'<a href="{e(url)}" target="_blank" rel="noopener noreferrer">{e(label)} ↗</a>'


def score_tag(n):
    return f'<span class="score {"hi" if n >= 70 else "mid" if n >= 50 else ""}">{n}</span>'


def pill(status):
    return f'<span class="pill {e(status)}">{e(status.replace("_", " "))}</span>'


def one(con, q, *p):
    return con.execute(q, p).fetchone()[0]


def page(con, path, title, sub, body, flash=""):
    drafts_n = one(con, "SELECT COUNT(*) FROM messages WHERE status='draft'")
    ready_n = one(con, "SELECT COUNT(*) FROM messages WHERE status='approved'")
    replied_n = one(con, "SELECT COUNT(*) FROM leads WHERE status='replied'")
    manual_n = one(con, "SELECT COUNT(*) FROM leads WHERE channel='manual' AND status='enriched'")
    posts_n = one(con, "SELECT COUNT(*) FROM intent WHERE status='new'")
    opps_n = one(con, "SELECT COUNT(*) FROM opps WHERE status='new'")
    wa_n = one(con, "SELECT COUNT(*) FROM leads WHERE whatsapp<>'' AND wa_step=0 AND status IN ('enriched','drafted')")
    vids_n = one(con, "SELECT COUNT(*) FROM videos WHERE status='ready'")

    def link(href, label, n=0, hot=False):
        b = f'<span class="badge {"hot" if hot else ""}">{n}</span>' if n else ""
        return f'<a class="{"on" if href == path else ""}" href="{href}">{label}{b}</a>'

    nav = (link("/", "Home") + '<div class="sep">Get clients</div>' + link("/find", "Find leads")
           + link("/drafts", "Review emails", drafts_n) + link("/send", "Send", ready_n)
           + link("/whatsapp", "WhatsApp", wa_n) + link("/calls", "Call &amp; LinkedIn list", manual_n) + link("/market", "Marketplaces") + link("/opps", "Projects to bid on", opps_n)
           + link("/posts", "People asking", posts_n)
           + '<div class="sep">Manage</div>' + link("/pipeline", "Pipeline", replied_n, True) + link("/leads", "All leads")
           + '<div class="sep">Content</div>' + link("/youtube", "YouTube studio", vids_n)
           + '<div class="sep">Setup</div>' + link("/templates", "Email templates") + link("/settings", "Settings")
           + link("/playbook", "Playbook"))
    flash = f'<div class="note good">{e(flash)}</div>' if flash else ""
    return (f'<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
            f'<title>{e(title)} · Vintelligence</title><script>document.documentElement.dataset.theme=new URLSearchParams(location.search).get("theme")||localStorage.getItem("theme")||(matchMedia("(prefers-color-scheme: light)").matches?"light":"dark")</script><style>{CSS}</style><div class="app"><aside>'
            f'<div class="logo">VINTELLIGENCE<small>GROWTH ENGINE</small></div>{nav}<button class="theme" id="theme" type="button"></button></aside>'
            f'<main><h1>{e(title)}</h1><p class="sub">{sub}</p>{flash}{body}</main></div>'
            f'<div id="job"><header><span id="jobname"></span><a href="#" onclick="closeJob();return false">close</a></header>'
            f'<pre></pre></div><script>{JS}</script></html>')


# ---------------------------------------------------------------- pages
def home(con, cfg, q):
    c, s = cfg["company"], cfg["sending"]
    counts = dict(con.execute("SELECT status, COUNT(*) FROM leads GROUP BY status").fetchall())
    total = sum(counts.values())
    sent_leads = one(con, "SELECT COUNT(DISTINCT lead_id) FROM messages WHERE status='sent'")
    replies = one(con, "SELECT COUNT(DISTINCT lead_id) FROM events WHERE kind='reply'")
    drafts_n = one(con, "SELECT COUNT(*) FROM messages WHERE status='draft'")
    ready_n = one(con, "SELECT COUNT(*) FROM messages WHERE status='approved'")
    manual_n = one(con, "SELECT COUNT(*) FROM leads WHERE channel='manual' AND status='enriched'")
    posts_n = one(con, "SELECT COUNT(*) FROM intent WHERE status='new'")
    mail_ok = not [p for p in mailer.preflight(cfg) if ".env" in p or "from_email" in p]

    steps = [
        (bool(c.get("sender_name") and c.get("postal_address")), "Add your name and postal address", "/settings"),
        (mail_ok, "Connect an outreach mailbox on its own domain", "/settings#mailbox"),
        (bool(c.get("booking_link")), "Add a link where prospects can book a call", "/settings"),
        (bool(c.get("demo_line")), "Add a demo phone number prospects can call", "/settings"),
        (total > 0, "Find your first leads", "/find"),
        (ready_n > 0 or sent_leads > 0, "Review and approve your first emails", "/drafts"),
        (sent_leads > 0, "Send your first emails", "/send"),
        (counts.get("won", 0) > 0, "Win your first client", "/pipeline"),
    ]
    done = sum(1 for d, _, _ in steps if d)
    checklist = "".join(
        f'<div class="check"><span class="dot {"done" if d else ""}">{"✓" if d else ""}</span>'
        f'<div>{"<span class=mute>" + e(t) + "</span>" if d else f"<a href={h!r}>{e(t)}</a>"}</div></div>'
        for d, t, h in steps)

    def kpi(n, label, href, act=False):
        return f'<a class="card kpi {"act" if act and n else ""}" href="{href}"><div class="n">{e(n)}</div><div class="l">{e(label)}</div></a>'

    todo = (kpi(drafts_n, "emails waiting for your review", "/drafts", True)
            + kpi(ready_n, "approved and ready to send", "/send", True)
            + kpi(counts.get("replied", 0), "replies to answer", "/pipeline", True)
            + kpi(one(con, "SELECT COUNT(*) FROM leads WHERE whatsapp<>'' AND wa_step=0 AND status IN ('enriched','drafted')"),
                  "businesses to WhatsApp", "/whatsapp", True)
            + kpi(manual_n, "businesses to call or message", "/calls", True)
            + kpi(one(con, "SELECT COUNT(*) FROM opps WHERE status='new'"), "projects to bid on", "/opps", True)
            + kpi(posts_n, "people asking online", "/posts", True))

    with_email = one(con, "SELECT COUNT(*) FROM leads WHERE email<>''")
    stages = [("Found", total), ("Website read", total - counts.get("new", 0) - counts.get("unreachable", 0)),
              ("Email found", with_email), ("Contacted", sent_leads), ("Replied", replies),
              ("Meeting", sum(counts.get(x, 0) for x in ("meeting", "proposal", "won"))), ("Won", counts.get("won", 0))]
    top = max(total, 1)
    funnel = "".join(f'<div class="fun"><span class="mute">{n}</span><div class="bar"><i style="width:{max(v / top * 100, 1 if v else 0):.1f}%"></i></div><b>{v}</b></div>'
                     for n, v in stages)

    days = dict(con.execute("SELECT substr(sent_at,1,10), COUNT(*) FROM messages WHERE status='sent' "
                            "AND sent_at>=date('now','-13 days') GROUP BY 1").fetchall())
    by_niche = con.execute("SELECT niche, COUNT(*), SUM(email<>''), ROUND(AVG(score)) FROM leads GROUP BY niche ORDER BY 2 DESC").fetchall()
    niche_rows = "".join(f"<tr><td>{e(NICHE_LABEL.get(n, n or 'other'))}</td><td>{t}</td><td>{m or 0}</td><td>{int(a or 0)}</td></tr>"
                         for n, t, m, a in by_niche) or "<tr><td colspan=4 class=mute>No leads yet</td></tr>"
    rate = f"{replies / sent_leads:.0%}" if sent_leads else "–"

    body = (f'<div class="row end" style="margin-bottom:16px"><div class="row">'
            f'{form("job_daily", "▶  Run daily routine", "go")}'
            f'<span class="mute small">checks inbox, finds leads, reads their sites, writes drafts, searches project boards</span></div>'
            f'<a class="btn" href="/settings#autopilot">Autopilot: {"ON" if cfg["autopilot"]["enabled"] else "off"}</a></div>'
            f'<h2>Needs you today</h2><div class="grid">{todo}</div>'
            f'<div class="two" style="margin-top:22px"><div class="card"><div class="row end"><b>Getting to your first client</b>'
            f'<span class="mute small">{done} of {len(steps)} done</span></div>'
            f'<div class="bar" style="margin:10px 0 6px"><i style="width:{done / len(steps) * 100:.0f}%"></i></div>{checklist}</div>'
            f'<div class="card"><b>Funnel</b><div style="margin-top:12px">{funnel}</div>'
            f'<div class="row" style="margin-top:16px;gap:26px"><div><div class="mute small">Reply rate</div><b style="font-size:20px">{rate}</b></div>'
            f'<div><div class="mute small">Sent today</div><b style="font-size:20px">{mailer.sent_today(con)} / {s["daily_cap"]}</b></div>'
            f'<div><div class="mute small">Sent, last 14 days</div><b style="font-size:20px">{sum(days.values())}</b></div></div></div></div>'
            f'<h2>Leads by niche</h2><div class="card"><table><thead><tr><th>Niche</th><th>Leads</th><th>With email</th><th>Average score</th></tr></thead>'
            f'<tbody>{niche_rows}</tbody></table></div>')
    return "Home", "What to do today, and how the numbers look.", body


def find(con, cfg, q):
    t = cfg["targets"]
    opts = "".join(f'<option value="{k}">{e(v)}</option>' for k, v in NICHE_LABEL.items())
    targets = ", ".join(NICHE_LABEL.get(n, n) for n in t["niches"]) + " in " + "; ".join(t["cities"])
    waiting = one(con, "SELECT COUNT(*) FROM leads WHERE status='new'")
    failed = one(con, "SELECT COUNT(*) FROM leads WHERE status='unreachable'")
    body = (f'<div class="two"><div class="card"><b>Search a city</b>'
            f'<p class="mute small">Finds businesses with a website from OpenStreetMap, reads each site, scores it and writes a draft email for the good ones.</p>'
            f'{open_form("job_find", "/find")}<label>Type of business</label><select name="niche">{opts}</select>'
            f'{field("city", "City", "", hint="e.g. Chicago, Illinois, USA   or   Leeds, UK")}'
            f'{field("limit", "How many", "40", "number")}'
            f'<div style="margin-top:16px"><button class="go">Find leads</button></div></form></div>'
            f'<div class="card"><b>Your saved targets</b><p class="mute small">{e(targets)}</p>'
            f'{form("job_daily", "Search all saved targets", "go", "/find")} <a class="btn" href="/settings#targets">Change targets</a>'
            f'<h2 style="margin-top:26px">Housekeeping</h2><div class="row">'
            f'{form("job_enrich", f"Read {waiting} waiting websites", "", "/find")}'
            f'{form("job_enrich", f"Retry {failed} unreachable sites", "", "/find", retry="1")}'
            f'{form("job_inbox", "Check inbox", "", "/find")}{form("job_signals", "Search posts", "", "/find")}</div></div></div>'
            f'<div class="two" style="margin-top:16px"><div class="card"><b>Add one business by hand</b>{open_form("lead_add", "/find")}'
            f'{field("name", "Business name")}{field("website", "Website")}'
            f'<div class="two">{field("email", "Email", hint="optional")}{field("phone", "Phone", hint="optional")}</div>'
            f'<div class="two"><div><label>Type</label><select name="niche">{opts}</select></div>{field("country", "Country code", "US", hint="US, GB, DE ...")}</div>'
            f'{field("city", "City", hint="optional")}<div style="margin-top:16px"><button>Add lead</button></div></form></div>'
            f'<div class="card"><b>Paste a list (CSV)</b><p class="mute small">First line must be column names. It needs a <code>website</code> column; '
            f'<code>name, email, phone, niche, country, city</code> are used if present. Works with exports from Apollo, Google Maps tools or your own sheet.</p>'
            f'{open_form("import_csv", "/find")}<textarea name="csv" placeholder="name,website,country&#10;Bright Dental,brightdental.com,US"></textarea>'
            f'<div style="margin-top:12px"><button>Import</button></div></form></div></div>')
    return "Find leads", "Fill the top of your funnel.", body


def drafts(con, cfg, q):
    rows = con.execute("SELECT m.*, l.name, l.email, l.score, l.country, l.website, l.opener, l.niche FROM messages m "
                       "JOIN leads l ON l.id=m.lead_id WHERE m.status='draft' ORDER BY l.score DESC, m.id LIMIT 60").fetchall()
    if not rows:
        return "Review emails", "Nothing waiting.", ('<div class="card">No drafts to review. <a href="/find">Find more leads</a> '
                                                      'and drafts will appear here.</div>')
    c = cfg["company"]
    warn = ""
    if not (c.get("sender_name") and c.get("postal_address")):
        warn = ('<div class="note">These drafts still show <b>[YOUR NAME]</b> or <b>[POSTAL ADDRESS]</b>. '
                'Fill them in under <a href="/settings">Settings</a>, then press “Refresh drafts with my details”. '
                f'{form("rebuild_all", "Refresh drafts with my details", "sm", "/drafts")}</div>')
    out = warn + (f'<div class="row end" style="margin-bottom:14px"><span class="mute">Read each one. Edit anything. '
                  f'A specific first line about their business gets far more replies.</span>'
                  f'{form("approve_all", f"Approve all {len(rows)}", "ok", "/drafts", confirm="Approve every draft on this page?")}</div>')
    for m in rows:
        out += (f'<div class="card" style="margin-bottom:16px" id="m{m["id"]}"><div class="row end"><div>{score_tag(m["score"])} '
                f'<a href="/lead?id={m["lead_id"]}"><b>{e(m["name"])}</b></a> <span class="mute">&lt;{e(m["email"])}&gt; · '
                f'{e(NICHE_LABEL.get(m["niche"], m["niche"]))} · {e(m["country"])} · email {m["step"]} of 3</span></div>{ext(m["website"], "open their website")}</div>'
                f'{open_form("save_draft", "/drafts#m" + str(m["id"]))}<input type="hidden" name="id" value="{m["id"]}">'
                f'{field("subject", "Subject", m["subject"])}{field("body", "Email", m["body"], rows=13)}'
                f'<div class="row" style="margin-top:12px"><button name="then" value="approve" class="ok">Save &amp; approve</button>'
                f'<button name="then" value="">Save changes</button><button name="then" value="reject" class="no">Reject</button></div></form>'
                + (f'<details style="margin-top:12px"><summary class="mute small" style="cursor:pointer">Write a custom first line and rebuild this email</summary>'
                   f'{open_form("lead_opener", "/drafts#m" + str(m["id"]))}<input type="hidden" name="id" value="{m["lead_id"]}">'
                   f'{field("opener", "First line", m["opener"], hint="one or two true sentences about something you saw on their site")}'
                   f'<div style="margin-top:10px"><button class="sm">Rebuild email</button></div></form></details>' if m["step"] == 1 else "")
                + '</div>')
    return "Review emails", f"{len(rows)} draft(s) waiting. Nothing is sent until you approve it and press Send.", out


def send(con, cfg, q):
    rows = con.execute("SELECT m.*, l.name, l.email, l.country FROM messages m JOIN leads l ON l.id=m.lead_id "
                       "WHERE m.status='approved' ORDER BY m.step DESC, m.id").fetchall()
    problems = mailer.preflight(cfg)
    cap, today = cfg["sending"]["daily_cap"], mailer.sent_today(con)
    out = ""
    if problems:
        out += ('<div class="note bad"><b>Live sending is locked until setup is finished:</b><ul style="margin:6px 0 0">'
                + "".join(f"<li>{e(p)}</li>" for p in problems) + '</ul><a href="/settings">Open Settings</a></div>')
    out += (f'<div class="grid"><div class="card kpi"><div class="n">{len(rows)}</div><div class="l">approved emails in the queue</div></div>'
            f'<div class="card kpi"><div class="n">{today} / {cap}</div><div class="l">sent today / daily limit</div></div>'
            f'<div class="card kpi"><div class="n">{max(0, min(len(rows), cap - today))}</div><div class="l">will go out if you send now</div></div></div>')
    out += (f'<div class="card" style="margin-top:16px"><div class="row">{form("job_send_dry", "Test run (sends nothing)", "", "/send")}'
            f'{open_form("job_send_live", "/send", "Send these emails to real businesses now?")}'
            f'<label style="display:inline;margin:0 10px"><input type="checkbox" name="sure" value="yes" required>I have read these emails and want to send them</label>'
            f'<button class="go" {"disabled" if problems or not rows else ""}>Send now</button></form></div>'
            f'<p class="mute small" style="margin-bottom:0">Emails go out one at a time with a pause of '
            f'{cfg["sending"]["min_gap_seconds"]}–{cfg["sending"]["max_gap_seconds"]} seconds between them, so a batch takes a while. '
            f'Keep this window open. People who opted out, bounced, or are in a consent-required country are skipped automatically.</p></div>')
    if rows:
        out += '<h2>Queue</h2><div class="card"><table><thead><tr><th>To</th><th>Email</th><th>Subject</th><th></th></tr></thead><tbody>'
        for m in rows:
            out += (f'<tr><td><a href="/lead?id={m["lead_id"]}">{e(m["name"])}</a><br><span class="mute small">{e(m["email"])} · {e(m["country"])}</span></td>'
                    f'<td>{m["step"]} of 3</td><td>{e(m["subject"])}</td><td>{form("unapprove", "Back to drafts", "sm", "/send", id=m["id"])}</td></tr>')
        out += "</tbody></table></div>"
    sent = con.execute("SELECT m.*, l.name, l.email FROM messages m JOIN leads l ON l.id=m.lead_id WHERE m.status='sent' "
                       "ORDER BY m.sent_at DESC LIMIT 25").fetchall()
    if sent:
        out += '<h2>Recently sent</h2><div class="card"><table><tbody>' + "".join(
            f'<tr><td class="mute">{e(m["sent_at"][:16].replace("T", " "))}</td><td><a href="/lead?id={m["lead_id"]}">{e(m["name"])}</a></td>'
            f'<td>email {m["step"]}</td><td>{e(m["subject"])}</td></tr>' for m in sent) + "</tbody></table></div>"
    return "Send", "Approved emails leave from your outreach mailbox, slowly, within the daily limit.", out


def calls(con, cfg, q):
    rows = con.execute("SELECT * FROM leads WHERE channel='manual' AND status='enriched' ORDER BY score DESC LIMIT 80").fetchall()
    if not rows:
        return "Call & LinkedIn list", "Nothing here yet.", '<div class="card">No manual leads right now.</div>'
    out = ('<div class="note">These businesses either show no public email or are in a country where cold email needs consent. '
           'Phone them or message the owner on LinkedIn by hand. Open a lead for a ready phone script.</div>'
           '<div class="card"><table><thead><tr><th>Score</th><th>Business</th><th>Phone</th><th>Why manual</th><th></th></tr></thead><tbody>')
    for r in rows:
        why = "no public email found" if not r["email"] else config.email_policy(cfg, r["country"])[1]
        out += (f'<tr><td>{score_tag(r["score"])}</td><td><a href="/lead?id={r["id"]}"><b>{e(r["name"])}</b></a><br>'
                f'<span class="mute small">{e(NICHE_LABEL.get(r["niche"], r["niche"]))} · {e(r["city"])}, {e(r["country"])}</span></td>'
                f'<td>{e(r["phone"]) or "<span class=mute>see website</span>"}</td><td class="mute small">{e(why)}</td>'
                f'<td class="row">{form("lead_status", "Contacted", "sm", "/calls", id=r["id"], value="contacted")}'
                f'{form("lead_status", "Not a fit", "sm no", "/calls", id=r["id"], value="lost")}</td></tr>')
    return "Call & LinkedIn list", f"{len(rows)} businesses to reach by hand.", out + "</tbody></table></div>"


def pipeline(con, cfg, q):
    out = '<div class="board">'
    for i, st in enumerate(BOARD):
        rows = con.execute("SELECT * FROM leads WHERE status=? ORDER BY updated_at DESC LIMIT 40", (st,)).fetchall()
        out += f'<div class="col"><h3>{st}<span>{len(rows)}</span></h3>'
        for r in rows:
            nxt = BOARD[i + 1] if i + 1 < len(BOARD) else None
            out += (f'<div class="tile"><a href="/lead?id={r["id"]}"><b>{e(r["name"])}</b></a><div class="mute small">'
                    f'{e(NICHE_LABEL.get(r["niche"], r["niche"]))} · {e(r["city"])}</div><div class="row" style="margin-top:8px">'
                    + (form("lead_status", f"→ {nxt}", "sm", "/pipeline", id=r["id"], value=nxt) if nxt else "")
                    + (form("lead_status", "lost", "sm no", "/pipeline", id=r["id"], value="lost") if st != "won" else "")
                    + "</div></div>")
        out += ('<div class="mute small" style="padding:6px">empty</div>' if not rows else "") + "</div>"
    out += "</div>"
    lost = one(con, "SELECT COUNT(*) FROM leads WHERE status IN ('lost','no_reply')")
    out += f'<p class="mute small" style="margin-top:16px">{lost} lead(s) closed as lost or no reply. <a href="/leads?status=lost">See them</a></p>'
    return "Pipeline", "Every conversation, from first email to signed client. Open a card to answer a reply.", out


def leads(con, cfg, q):
    where, args = "1=1", []
    for col in ("status", "channel", "niche", "country"):
        if q.get(col):
            where += f" AND {col}=?"
            args.append(q[col][0])
    rows = con.execute(f"SELECT * FROM leads WHERE {where} ORDER BY score DESC, id LIMIT 500", args).fetchall()
    cur = q.get("status", [""])[0]
    tabs = '<div class="tabs"><a class="{}" href="/leads">all</a>'.format("" if cur else "on") + "".join(
        f'<a class="{"on" if s == cur else ""}" href="/leads?status={s}">{s.replace("_", " ")}</a>' for s in db.STATUSES) + "</div>"
    out = tabs + '<div class="row end" style="margin:6px 0 14px"><input id="q" placeholder="Type to filter by name, city, email ..." style="max-width:380px">' \
                 '<a class="btn" href="/export">Download as spreadsheet (CSV)</a></div>'
    if not rows:
        return "All leads", "", out + '<div class="card mute">No leads match.</div>'
    out += ('<div class="card"><table><thead><tr><th>Score</th><th>Business</th><th>Type</th><th>Where</th><th>Contact</th>'
            '<th>Channel</th><th>Status</th></tr></thead><tbody>')
    for r in rows:
        contact = "<br>".join(e(x) for x in (r["email"], r["phone"]) if x) or "<span class='mute'>none found</span>"
        out += (f'<tr><td>{score_tag(r["score"])}</td><td><a href="/lead?id={r["id"]}"><b>{e(r["name"])}</b></a><br>'
                f'<span class="mute small">{e(r["domain"])}</span></td><td>{e(NICHE_LABEL.get(r["niche"], r["niche"]))}</td>'
                f'<td>{e(r["city"])}, {e(r["country"])}</td><td class="small">{contact}</td><td>{e(r["channel"])}</td><td>{pill(r["status"])}</td></tr>')
    return "All leads", f"{len(rows)} shown, best fit first.", out + "</tbody></table></div>"


def lead(con, cfg, q):
    lid = int(q.get("id", ["0"])[0])
    r = con.execute("SELECT * FROM leads WHERE id=?", (lid,)).fetchone()
    if not r:
        return "Lead", "", '<div class="card">No such lead.</div>'
    back = f"/lead?id={lid}"
    sig = json.loads(r["signals"] or "{}")
    names = {"online_booking": "online booking", "chat_widget": "chat widget", "online_ordering": "online ordering", "ai_voice": "AI voice product"}
    chips = "".join(f'<span class="pill {"yes" if not sig.get(k) and k != "ai_voice" else "no" if sig.get(k) else ""}">'
                    f'{"has" if sig.get(k) else "no"} {v}</span>' for k, v in names.items()) if sig else '<span class="mute">website not read yet</span>'
    allowed, why = config.email_policy(cfg, r["country"])
    li = "https://www.linkedin.com/search/results/all/?" + urllib.parse.urlencode({"keywords": f'{r["name"]} owner'})
    goog = "https://www.google.com/search?" + urllib.parse.urlencode({"q": f'{r["name"]} {r["city"]} owner OR manager'})
    opts = "".join(f'<option {"selected" if s == r["status"] else ""}>{s}</option>' for s in MOVE_TO)

    left = (f'<div class="card"><div class="row end"><div>{score_tag(r["score"])} {pill(r["status"])} <span class="pill">{e(r["channel"] or "not set")}</span></div>'
            f'<div class="row">{ext(r["website"], "website")} {ext(li, "find owner on LinkedIn")} {ext(goog, "Google")}</div></div>'
            f'<p style="margin:14px 0 6px"><b>{e(NICHE_LABEL.get(r["niche"], r["niche"]))}</b> · {e(r["city"])}, {e(r["country"])} · found via {e(r["source"])}</p>'
            f'<div class="chips" style="margin:10px 0">{chips}</div>'
            f'<p class="small mute" style="margin:0">Email rule for {e(r["country"] or "unknown")}: {"allowed" if allowed else "not allowed"} ({e(why)})</p>'
            f'{open_form("lead_edit", back)}<input type="hidden" name="id" value="{lid}">'
            f'<div class="two">{field("email", "Email", r["email"])}{field("phone", "Phone", r["phone"])}</div>'
            f'{field("whatsapp", "WhatsApp number", r["whatsapp"], hint="digits with country code; only if they publish it")}'
            f'<div class="two"><div><label>Reach by</label><select name="channel"><option {"selected" if r["channel"] == "email" else ""}>email</option>'
            f'<option {"selected" if r["channel"] != "email" else ""}>manual</option></select></div>'
            f'<div><label>Status</label><select name="status"><option value="">keep: {e(r["status"])}</option>{opts}</select></div></div>'
            f'{field("note", "Add a note", "", hint="call outcome, what they said, next step")}'
            f'<div class="row" style="margin-top:14px"><button class="go">Save</button></div></form>'
            + (f'<h2>Notes</h2><pre class="mail">{e(r["notes"])}</pre>' if r["notes"] else "")
            + f'<div style="margin-top:18px">{form("lead_suppress", "Never contact this business again", "sm no", "/leads", confirm="Block this business permanently?", id=lid)}</div></div>')

    right = ""
    if r["status"] == "replied":
        last = con.execute("SELECT detail FROM events WHERE lead_id=? AND kind='reply' ORDER BY id DESC LIMIT 1", (lid,)).fetchone()
        right += '<div class="card" style="margin-bottom:16px"><b>They replied</b>' + (f'<pre class="mail" style="margin:10px 0">{e(last[0])}</pre>' if last else "")
        right += '<p class="mute small">Suggested answers. Copy one, adjust it, and send it from your own mailbox. Then move the lead to “meeting”.</p>'
        for i, (title, text) in enumerate(compose.reply_suggestions(r, cfg)):
            right += (f'<details {"open" if i == 0 else ""} style="margin-bottom:8px"><summary style="cursor:pointer"><b>{e(title)}</b></summary>'
                      f'<pre class="mail" id="rep{i}" style="margin:8px 0">{e(text)}</pre><button class="sm" data-copy="rep{i}">Copy</button></details>')
        right += "</div>"
    right += (f'<div class="card" style="margin-bottom:16px"><div class="row end"><b>Phone script</b><button class="sm" data-copy="script">Copy</button></div>'
              f'<pre class="mail" id="script" style="margin-top:10px">{e(compose.call_script(r, cfg))}</pre>'
              f'<div class="row end" style="margin-top:16px"><b>LinkedIn connection note</b><button class="sm" data-copy="lnk">Copy</button></div>'
              f'<pre class="mail" id="lnk" style="margin-top:10px">{e(compose.linkedin_note(r, cfg))}</pre>'
              f'<p class="mute small" style="margin-bottom:0">Send LinkedIn messages by hand. Automation tools get accounts restricted.</p></div>')
    msgs = con.execute("SELECT * FROM messages WHERE lead_id=? ORDER BY id", (lid,)).fetchall()
    events = con.execute("SELECT * FROM events WHERE lead_id=? ORDER BY id DESC LIMIT 30", (lid,)).fetchall()
    hist = "".join(f'<details style="margin-bottom:8px"><summary style="cursor:pointer">Email {m["step"]} · {pill(m["status"])} '
                   f'<span class="mute small">{e((m["sent_at"] or m["created_at"])[:10])}</span> · {e(m["subject"])}</summary>'
                   f'<pre class="mail" style="margin-top:8px">{e(m["body"])}</pre></details>' for m in msgs)
    hist += "".join(f'<div class="small mute">{e(v["at"][:16].replace("T", " "))} · {e(v["kind"])} · {e(v["detail"][:140])}</div>' for v in events)
    right += f'<div class="card"><b>History</b><div style="margin-top:10px">{hist or "<span class=mute>Nothing yet.</span>"}</div></div>'
    return r["name"], f'<a href="/leads">← all leads</a> · {e(r["domain"])}', f'<div class="two">{left}<div>{right}</div></div>'


def posts(con, cfg, q):
    rows = con.execute("SELECT * FROM intent WHERE status='new' ORDER BY posted_at DESC LIMIT 100").fetchall()
    out = f'<div class="row" style="margin-bottom:16px">{form("job_signals", "Search again", "go", "/posts")}<a class="btn" href="/settings#intent">Change search phrases</a></div>'
    if not rows:
        return "People asking", "", out + '<div class="card mute">Nothing new right now.</div>'
    for r in rows:
        out += (f'<div class="card" style="margin-bottom:12px"><div class="row end"><b>{e(r["title"]) or "(comment)"}</b>'
                f'<span class="mute small">{e(r["source"])} · {e(r["posted_at"][:10])}</span></div><p class="small">{e(r["snippet"])}</p>'
                f'<div class="row">{ext(r["url"], "open post")}{form("post_done", "Done / not relevant", "sm", "/posts", id=r["id"])}</div></div>')
    return "People asking", "Public posts that mention what you sell. Reply by hand, be useful first, pitch second.", out


def templates(con, cfg, q):
    files = sorted(p.stem for p in compose.TEMPLATES.glob("*.txt"))
    cur = q.get("name", ["generic"])[0]
    cur = cur if cur in files else "generic"
    tabs = '<div class="tabs">' + "".join(f'<a class="{"on" if f == cur else ""}" href="/templates?name={f}">{e(NICHE_LABEL.get(f, f))}</a>' for f in files) + "</div>"
    missing = [k for k in NICHE_LABEL if k not in files]
    new = ""
    if missing:
        new = (f'{open_form("new_template", "/templates")}<div class="row"><select name="name" style="width:auto">'
               + "".join(f'<option value="{k}">{e(NICHE_LABEL[k])}</option>' for k in missing)
               + '</select><button>Create a dedicated template for this niche</button></div></form>')
    text = (compose.TEMPLATES / f"{cur}.txt").read_text(encoding="utf-8")
    words = ("{name} business name · {opener} personal first line · {kind} e.g. “dental practices” · {action} e.g. “book an appointment” · "
             "{offer} your offer from Settings · {demo_sentence} and {booking_sentence} appear only when you have set a demo line / booking link")
    body = (tabs + f'<div class="card">{open_form("save_template", "/templates?name=" + cur)}<input type="hidden" name="name" value="{e(cur)}">'
            f'<p class="mute small" style="margin-top:0">Three emails: first contact, a follow-up after {cfg["sending"]["followup_days"][0]} days, and a last one. '
            f'Keep the <code>=== step N ===</code> and <code>Subject:</code> lines. The signature and opt-out line are added automatically.<br>{e(words)}</p>'
            f'<textarea name="text" style="min-height:520px;font:13px/1.6 ui-monospace,Consolas,monospace">{e(text)}</textarea>'
            f'<div class="row" style="margin-top:12px"><button class="go">Save template</button>'
            f'<span class="mute small">Niches without their own template use “generic”. Saving does not change drafts already written.</span></div></form></div>'
            f'<div style="margin-top:16px">{new}</div>')
    return "Email templates", "The wording of every email the engine writes.", body


def settings(con, cfg, q):
    import os
    c, s, t, p, it = cfg["company"], cfg["sending"], cfg["targets"], cfg["policy"], cfg["intent"]
    niche_boxes = "".join(f'<label style="display:inline-block;margin:4px 14px 4px 0;font-weight:400;color:var(--ink)">'
                          f'<input type="checkbox" name="targets.niches" value="{k}" {"checked" if k in t["niches"] else ""}>{e(v)}</label>'
                          for k, v in NICHE_LABEL.items())
    env = lambda k: os.environ.get(k, "")
    body = (f'<div class="two"><div class="card">{open_form("save_settings", "/settings")}<b>You and your company</b>'
            f'{field("company.sender_name", "Your name", c.get("sender_name"), hint="shown as the sender")}'
            f'{field("company.sender_title", "Your title", c.get("sender_title"))}'
            f'{field("company.postal_address", "Postal address", c.get("postal_address"), hint="required by US law in every email")}'
            f'{field("company.booking_link", "Booking link", c.get("booking_link"), hint="Calendly / Cal.com, optional")}'
            f'{field("company.demo_line", "Demo phone number", c.get("demo_line"), hint="a live agent prospects can call, optional")}'
            f'{field("company.offer", "Your offer", c.get("offer"), rows=3)}'
            f'<div style="margin-top:16px"><button class="go">Save</button></div></form></div>'
            f'<div class="card" id="mailbox">{open_form("save_mailbox", "/settings#mailbox")}<b>Outreach mailbox</b>'
            f'<p class="mute small">Use a mailbox on a separate outreach domain, never Gmail or your main domain. See the Playbook, section 3. '
            f'Passwords are stored only in the <code>.env</code> file on this computer.</p>'
            f'{field("sending.from_email", "Send from (email address)", s.get("from_email"))}'
            f'<div class="two">{field("SMTP_HOST", "Outgoing server (SMTP)", env("SMTP_HOST"), hint="e.g. smtp.gmail.com")}{field("SMTP_PORT", "Port", env("SMTP_PORT") or "587")}</div>'
            f'<div class="two">{field("SMTP_USER", "Username", env("SMTP_USER"))}{field("SMTP_PASS", "Password", "", "password", "blank = keep" if env("SMTP_PASS") else "app password")}</div>'
            f'<div class="two">{field("IMAP_HOST", "Incoming server (IMAP)", env("IMAP_HOST"), hint="for replies")}{field("IMAP_PORT", "Port", env("IMAP_PORT") or "993")}</div>'
            f'<div class="two">{field("IMAP_USER", "Username", env("IMAP_USER"))}{field("IMAP_PASS", "Password", "", "password", "blank = keep" if env("IMAP_PASS") else "app password")}</div>'
            f'<div style="margin-top:16px"><button class="go">Save mailbox</button></div></form></div></div>'
            f'<div class="two" style="margin-top:16px"><div class="card" id="targets">{open_form("save_settings", "/settings#targets")}<b>Who to target</b>'
            f'<label>Types of business</label><input type="hidden" name="targets.niches" value="">{niche_boxes}'
            f'{field("targets.cities", "Cities", chr(10).join(t["cities"]), hint="one per line, e.g. Austin, Texas, USA", rows=5)}'
            f'{field("targets.per_search_limit", "Leads per search", t["per_search_limit"], "number")}'
            f'<div style="margin-top:16px"><button class="go">Save</button></div></form></div>'
            f'<div class="card">{open_form("save_settings", "/settings")}<b>Sending rules</b>'
            f'<div class="two">{field("sending.daily_cap", "Emails per day", s["daily_cap"], "number", "start at 5–10 on a new domain")}'
            f'{field("sending.min_score", "Minimum score to email", s["min_score"], "number")}</div>'
            f'{field("sending.followup_days", "Follow-up gaps in days", ", ".join(map(str, s["followup_days"])), hint="e.g. 3, 7")}'
            f'<label style="font-weight:400;color:var(--ink)"><input type="hidden" name="sending.auto_approve_followups" value="false">'
            f'<input type="checkbox" name="sending.auto_approve_followups" value="true" {"checked" if s.get("auto_approve_followups") else ""}>'
            f'Follow-ups are approved automatically (first emails always need you)</label>'
            f'{field("policy.email_ok", "Countries where cold email is allowed", ", ".join(p["email_ok"]))}'
            f'{field("policy.email_b2b", "Countries where business-to-business email is allowed", ", ".join(p["email_b2b"]))}'
            f'<p class="mute small">Every other country is never emailed and goes to the Call &amp; LinkedIn list. Not legal advice; see the Playbook, section 4.</p>'
            f'<div style="margin-top:10px"><button class="go">Save</button></div></form></div></div>'
f'<div class="card" style="margin-top:16px;border-color:var(--acc)" id="autopilot">{open_form("save_settings", "/settings#autopilot")}<b>Autopilot</b>'
            f'<p class="mute small">Off: the daily routine prepares everything and waits for you to approve and send. '
            f'On: it also approves first emails and sends them by itself, up to your daily limit, without you reading them first. '
            f'Country rules, the opt-out list and the setup checks still apply. Turn it on only after you have read a few batches and trust the templates.</p>'
            f'<label style="font-weight:400;color:var(--ink)"><input type="hidden" name="autopilot.enabled" value="false">'
            f'<input type="checkbox" name="autopilot.enabled" value="true" {"checked" if cfg["autopilot"]["enabled"] else ""}>'
            f'Send approved-quality emails automatically every day</label><div style="margin-top:12px"><button class="go">Save</button></div></form></div>'
            f'<div class="two" style="margin-top:16px"><div class="card" id="opps">{open_form("save_settings", "/settings#opps")}<b>Project board search phrases</b>'
            f'{field("opportunities.queries", "Phrases", chr(10).join(cfg["opportunities"]["queries"]), hint="one per line", rows=6)}'
            f'<div style="margin-top:12px"><button class="go">Save</button></div></form></div>'
            f'<div class="card" id="youtube">{open_form("save_settings", "/settings#youtube")}<b>YouTube uploads</b>'
            f'<div class="two">{field("youtube.per_day", "Videos per day", cfg["youtube"]["per_day"], "number", "1 to 5")}'
            f'<div><label>Visibility</label><select name="youtube.privacy">'
            + "".join(f'<option {"selected" if o == cfg["youtube"]["privacy"] else ""}>{o}</option>' for o in ("private", "unlisted", "public"))
            + f'</select></div></div>{field("youtube.default_tags", "Default tags", ", ".join(cfg["youtube"]["default_tags"]))}'
            f'{field("youtube.default_description", "Default description", cfg["youtube"]["default_description"], rows=3)}'
            f'<div style="margin-top:12px"><button class="go">Save</button></div></form></div></div>'
f'<div class="card" style="margin-top:16px" id="whatsapp">{open_form("save_settings", "/settings#whatsapp")}<b>WhatsApp</b>'
            f'<div class="two">{field("whatsapp.number", "Your WhatsApp Business number", cfg.get("whatsapp", {}).get("number", ""), hint="with country code, e.g. +923001234567")}'
            f'<div class="two">{field("whatsapp.daily_limit", "First messages per day", cfg.get("whatsapp", {}).get("daily_limit", 15), "number")}'
            f'{field("whatsapp.followup_after", "Follow up after (days)", cfg.get("whatsapp", {}).get("followup_after", 3), "number")}</div></div>'
            f'{field("policy.whatsapp_ok", "Countries where you message businesses on WhatsApp", ", ".join(cfg["policy"].get("whatsapp_ok", [])), hint="two-letter codes")}'
            f'<p class="mute small">Only businesses that publish a WhatsApp contact on their own website are listed. Rules on unsolicited messages differ by country; check before adding one.</p>'
            f'<div style="margin-top:10px"><button class="go">Save</button></div></form></div>'
            f'<div class="card" style="margin-top:16px" id="intent">{open_form("save_settings", "/settings#intent")}<b>“People asking” search phrases</b>'
            f'{field("intent.queries", "Phrases", chr(10).join(it["queries"]), hint="one per line", rows=5)}'
            f'<div style="margin-top:12px"><button class="go">Save</button></div></form></div>')
    return "Settings", "Everything the engine needs to know about you.", body


def md(text):
    """Small Markdown renderer for the playbook: headings, tables, lists, bold, code."""
    def inline(s):
        s = e(s)
        s = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", s)
        s = re.sub(r"`(.+?)`", r"<code>\1</code>", s)
        return re.sub(r"\[(.+?)\]\((.+?)\)", r"\1", s)
    out, mode, para = [], "", []

    def close():
        nonlocal mode, para
        if para:
            out.append("<p>" + " ".join(para) + "</p>")
            para = []
        if mode:
            out.append({"ul": "</ul>", "ol": "</ol>", "table": "</tbody></table>"}[mode])
            mode = ""
    for line in text.splitlines():
        st = line.strip()
        h = re.match(r"(#+)\s+(.*)", st)
        if h:
            close()
            out.append(f"<h{len(h.group(1))}>{inline(h.group(2))}</h{len(h.group(1))}>")
        elif st.startswith("|"):
            cells = [c.strip() for c in st.strip("|").split("|")]
            if all(re.fullmatch(r":?-+:?", c) for c in cells):
                continue
            if mode != "table":
                close()
                mode = "table"
                out.append("<table><thead><tr>" + "".join(f"<th>{inline(c)}</th>" for c in cells) + "</tr></thead><tbody>")
            else:
                out.append("<tr>" + "".join(f"<td>{inline(c)}</td>" for c in cells) + "</tr>")
        elif re.match(r"(-|\d+\.)\s+", st):
            kind = "ul" if st.startswith("-") else "ol"
            if mode != kind:
                close()
                mode = kind
                out.append(f"<{kind}>")
            out.append("<li>" + inline(re.sub(r"^(-|\d+\.)\s+", "", st)) + "</li>")
        elif not st:
            close()
        elif mode in ("ul", "ol") and line.startswith(" "):
            out[-1] = out[-1][:-5] + " " + inline(st) + "</li>"
        else:
            if mode:
                close()
            para.append(inline(st))
    close()
    return "\n".join(out)


def playbook(con, cfg, q):
    text = (ROOT / "PLAYBOOK.md").read_text(encoding="utf-8")
    return "Playbook", "How to turn this engine into paying clients.", f'<div class="card md">{md(text.split(chr(10), 1)[1])}</div>'


def opps_page(con, cfg, q):
    tab = q.get("tab", ["new"])[0]
    tab = tab if tab in OPP_TABS else "new"
    rows = con.execute("SELECT * FROM opps WHERE status=? ORDER BY score DESC, posted_at DESC LIMIT 60", (tab,)).fetchall()
    counts = dict(con.execute("SELECT status, COUNT(*) FROM opps GROUP BY status").fetchall())
    out = (f'<div class="row end" style="margin-bottom:14px"><div class="tabs" style="margin:0">'
           + "".join(f'<a class="{"on" if t == tab else ""}" href="/opps?tab={t}">{t} ({counts.get(t, 0)})</a>' for t in OPP_TABS)
           + f'</div><div class="row"><a class="btn" href="/market#paste">Paste a job from Upwork</a>{form("job_opps", "Search project boards now", "go", "/opps")}'
             f'<a class="btn" href="/settings#opps">Change search phrases</a></div></div>'
             '<div class="note">These are live requests from people who want this work done now, so they are the fastest route to first income. '
             'Bid from your own account on the platform: open the post, paste the proposal, adjust it, and add your price. '
             'Reply within hours; early bids win.</div>')
    if not rows:
        return "Projects to bid on", "", out + '<div class="card mute">Nothing here. Press “Search project boards now”.</div>'
    for r in rows:
        back = f"/opps?tab={tab}#o{r['id']}"
        out += (f'<div class="card" style="margin-bottom:16px" id="o{r["id"]}"><div class="row end"><div>{score_tag(r["score"])} <b>{e(r["title"])}</b></div>'
                f'{ext(r["url"], "open post") if r["url"].startswith("http") else ""}</div><div class="mute small" style="margin:6px 0">{e(r["source"])} · {e(r["posted_at"][:10])}'
                f'{" · budget " + e(r["budget"]) if r["budget"] else ""}{" · " + e(r["company"]) if r["company"] else ""}</div>'
                f'<div class="chips">{"".join(f"<span class=pill>{e(t.strip())}</span>" for t in r["tags"].split(",") if t.strip())}</div>'
                f'<p class="small">{e(r["snippet"][:520])}{"…" if len(r["snippet"]) > 520 else ""}</p>'
                f'{open_form("opp_save", back)}<input type="hidden" name="id" value="{r["id"]}">'
                f'{field("proposal", "Your proposal (edit freely)", r["proposal"], rows=11)}'
                f'<div class="row" style="margin-top:12px"><button type="button" class="go" data-copyfield="1">Copy proposal</button>'
                f'<button name="then" value="applied" class="ok">I applied</button>'
                + ('<button name="then" value="replied">Client replied</button><button name="then" value="won" class="ok">Won 🎉</button>' if tab in ("applied", "replied") else "")
                + f'<button name="then" value="saved">Save for later</button>'
                f'<button name="then" value="dismissed" class="no">Not for us</button></div></form></div>')
    return "Projects to bid on", f"{len(rows)} open request(s) that match what Vintelligence sells, best match first.", out


def youtube_page(con, cfg, q):
    youtube.ensure_folders()
    youtube.seed(con)
    y = cfg["youtube"]
    ok = lambda b: f'<span class="dot {"done" if b else ""}">{"✓" if b else ""}</span>'
    steps = [
        (youtube.has_secret(), 'Create a Google Cloud project, switch on “YouTube Data API v3”, create an OAuth client of type “Desktop app”, '
                               'download its JSON file and save it as <code>data/client_secret.json</code> in this project.'),
        (youtube.connected(), "Connect your channel (button below). Google opens in the browser; choose the channel's account."),
        (bool(list(youtube.READY.glob("*"))), "Put finished videos into the folder <code>videos/ready</code>. The file name becomes the title."),
    ]
    setup = "".join(f'<div class="check">{ok(d)}<div>{t}</div></div>' for d, t in steps)
    out = ('<div class="note"><b>Read this first.</b> YouTube pays only after a channel joins the Partner Programme: 1,000 subscribers plus either '
           '4,000 watch hours in 12 months or 10 million Shorts views in 90 days. It does not pay for mass-produced or repetitive AI videos, and '
           'realistic AI video must be labelled (this tool labels every upload). Plan for months, not days, and for original stories people finish watching. '
           'Use fictional characters only, never the face or voice of a real person. New Google API projects can only upload as <b>private</b> until Google '
           'audits the project, so at first you publish each video with one click in YouTube Studio.</div>'
           f'<div class="two"><div class="card"><b>Setup</b>{setup}<div class="row" style="margin-top:14px">'
           f'{form("job_yt_connect", "Connect YouTube channel", "go", "/youtube") if youtube.has_secret() and not youtube.connected() else ""}'
           f'{form("job_youtube", "Scan folder and upload due videos", "go" if youtube.connected() else "", "/youtube")}'
           f'<a class="btn" href="/settings#youtube">Upload settings</a></div>'
           f'<p class="mute small" style="margin-bottom:0">Uploads {y["per_day"]} per day as <b>{e(y["privacy"])}</b>. The daily routine uploads automatically once the channel is connected.</p></div>'
           f'<div class="card"><b>Make a video from clips</b><p class="mute small">Generate the scenes of a story in an AI video tool, save the clips into '
           f'<code>videos/stories/&lt;story-name&gt;/</code> named 01, 02, 03 …, optionally add <code>narration.mp3</code> and <code>music.mp3</code>, '
           f'then join them here into one finished file.</p>')
    folders = youtube.story_folders()
    if folders:
        out += (f'{open_form("job_assemble", "/youtube")}<label>Story folder</label><select name="name">'
                + "".join(f'<option>{e(f)}</option>' for f in folders)
                + '</select><label style="font-weight:400;color:var(--ink)"><input type="checkbox" name="vertical" value="1" checked>Vertical (Shorts, 9:16)</label>'
                  '<div style="margin-top:12px"><button class="go">Join clips into a video</button></div></form>')
    else:
        out += '<p class="mute small">No story folders with clips yet.</p>'
    out += "</div></div>"

    queue = con.execute("SELECT * FROM videos WHERE status IN ('ready','failed','uploaded') ORDER BY status='uploaded', publish_at DESC LIMIT 40").fetchall()
    out += '<h2>Upload queue</h2>'
    if queue:
        out += '<div class="card"><table><thead><tr><th>Video</th><th>Scheduled (UTC)</th><th>Status</th><th></th></tr></thead><tbody>'
        for v in queue:
            link_ = ext("https://youtu.be/" + v["youtube_id"], "watch") if v["youtube_id"] else ""
            out += (f'<tr><td><details><summary style="cursor:pointer"><b>{e(v["title"])}</b> <span class="mute small">{e(v["file"])}</span></summary>'
                    f'{open_form("video_save", "/youtube")}<input type="hidden" name="id" value="{v["id"]}">{field("title", "Title", v["title"])}'
                    f'{field("description", "Description", v["description"] or y["default_description"], rows=3)}'
                    f'{field("tags", "Tags", v["tags"] or ", ".join(y["default_tags"]), hint="comma separated")}'
                    f'<div style="margin-top:10px"><button class="sm">Save</button></div></form></details>'
                    f'{"<div class=small style=color:var(--bad)>" + e(v["error"]) + "</div>" if v["error"] else ""}</td>'
                    f'<td class="small">{e(v["publish_at"][:16].replace("T", " "))}</td><td>{pill("won" if v["status"] == "uploaded" else v["status"])} {link_}</td>'
                    f'<td>{form("video_delete", "Remove", "sm no", "/youtube", "Remove from the queue? The file stays on disk.", id=v["id"]) if v["status"] != "uploaded" else ""}</td></tr>')
        out += "</tbody></table></div>"
    else:
        out += '<div class="card mute">No videos queued. Put files in <code>videos/ready</code> and press “Scan folder”.</div>'

    ideas = con.execute("SELECT * FROM videos WHERE status='idea' ORDER BY id DESC").fetchall()
    out += '<h2>Story scripts</h2><div class="two"><div>'
    for v in ideas:
        out += (f'<details class="card" style="margin-bottom:12px"><summary style="cursor:pointer"><b>{e(v["title"])}</b></summary>'
                f'<pre class="mail" id="s{v["id"]}" style="margin:12px 0">{e(v["script"])}</pre><div class="row"><button class="sm" data-copy="s{v["id"]}">Copy</button>'
                f'{form("video_delete", "Delete", "sm no", "/youtube", "Delete this script?", id=v["id"])}</div></details>')
    out += (f'</div><div class="card">{open_form("idea_add", "/youtube")}<b>Add a story script</b>'
            f'<p class="mute small">Ask Claude in this project for new scripts (“write 10 new story scripts for the YouTube studio”) or paste your own. '
            f'Name the finished file like the title and it is matched automatically.</p>'
            f'{field("title", "Title")}{field("script", "Script with scenes", "", rows=8)}<div style="margin-top:12px"><button>Add</button></div></form></div></div>')
    return "YouTube studio", "Scripts, clip assembly and scheduled uploads for your channel.", out


def whatsapp_page(con, cfg, q):
    w = cfg.get("whatsapp", {})
    limit, gap = int(w.get("daily_limit", 15)), int(w.get("followup_after", 3))
    allowed = [c.upper() for c in cfg["policy"].get("whatsapp_ok", [])]
    today = one(con, "SELECT COUNT(*) FROM events WHERE kind='whatsapp' AND substr(at,1,10)=?", db.now()[:10])
    rows = con.execute("SELECT * FROM leads WHERE whatsapp<>'' AND status IN ('enriched','drafted','contacted') "
                       "ORDER BY score DESC").fetchall()
    first = [r for r in rows if r["wa_step"] == 0 and r["status"] != "contacted" and r["country"] in allowed]
    follow = [r for r in rows if r["wa_step"] == 1 and db.days_since(r["wa_at"]) >= gap]
    waiting = [r for r in rows if r["wa_step"] in (1, 2) and r not in follow]
    other = sum(1 for r in rows if r["wa_step"] == 0 and r["country"] not in allowed)
    unchecked = one(con, "SELECT COUNT(*) FROM leads WHERE status IN ('enriched','drafted') AND whatsapp='' "
                         "AND signals NOT LIKE '%\"whatsapp\"%'")
    left = max(0, limit - today)
    out = ('<div class="note"><b>How this works, and why.</b> WhatsApp blocks numbers that send automatic or bulk messages to strangers, '
           'so nothing here is sent by a robot. The app finds businesses that publish a WhatsApp contact on their own website, writes the '
           'message, and opens WhatsApp with the text ready. You press send, from your own WhatsApp Business number, a few per day. '
           'Anyone who says stop is never contacted again on any channel.</div>'
           f'<div class="grid"><div class="card kpi"><div class="n">{len(first)}</div><div class="l">businesses to message</div></div>'
           f'<div class="card kpi"><div class="n">{len(follow)}</div><div class="l">follow-ups due</div></div>'
           f'<div class="card kpi"><div class="n">{len(waiting)}</div><div class="l">waiting for an answer</div></div>'
           f'<div class="card kpi"><div class="n">{today} / {limit}</div><div class="l">sent today / your daily limit</div></div></div>'
           f'<div class="row" style="margin:16px 0">'
           + (form("job_wa_scan", f"Check {unchecked} more websites for a WhatsApp number", "go", "/whatsapp") if unchecked else "")
           + '<a class="btn" href="/settings#whatsapp">WhatsApp settings</a>'
           + (f'<span class="mute small">{other} more have WhatsApp but are in countries not on your allowed list.</span>' if other else "")
           + '</div>')
    if not w.get("number"):
        out += '<div class="note bad">Add your own WhatsApp Business number in <a href="/settings#whatsapp">Settings</a> so prospects can reach you back and it appears in your emails.</div>'
    if not left and (first or follow):
        out += '<div class="note bad">Daily limit reached. Continue tomorrow; this protects your number from being blocked.</div>'

    def cards(items, step, title):
        html_ = f"<h2>{title}</h2>" if items else ""
        for r in items[:40]:
            html_ += (f'<div class="card" style="margin-bottom:14px" id="w{r["id"]}"><div class="row end"><div>{score_tag(r["score"])} '
                      f'<a href="/lead?id={r["id"]}"><b>{e(r["name"])}</b></a> <span class="mute">+{e(r["whatsapp"])} · '
                      f'{e(NICHE_LABEL.get(r["niche"], r["niche"]))} · {e(r["city"])}, {e(r["country"])}</span></div>{ext(r["website"], "their website")}</div>'
                      f'{open_form("wa_sent", "/whatsapp")}<input type="hidden" name="id" value="{r["id"]}"><input type="hidden" name="step" value="{step}">'
                      f'<textarea name="text" style="min-height:120px;margin-top:10px">{e(compose.whatsapp_message(r, cfg, step))}</textarea>'
                      f'<div class="row" style="margin-top:12px"><button type="button" class="go" data-wa="{e(r["whatsapp"])}" {"disabled" if not left else ""}>1. Open in WhatsApp</button>'
                      f'<button class="ok">2. I sent it</button><button name="skip" value="1" class="no">Skip this business</button></div></form></div>')
        return html_

    out += cards(follow, 2, "Follow-ups due") + cards(first, 1, "First messages")
    if not first and not follow:
        out += ('<div class="card mute">No one to message right now. Businesses in the US rarely publish WhatsApp; it is common in the UK, '
                'the Gulf, South Africa, Singapore and Pakistan. Add such cities under <a href="/settings#targets">Settings → Who to target</a>, '
                'find leads, then come back.</div>')
    if waiting:
        out += '<h2>Waiting for an answer</h2><div class="card"><table><tbody>' + "".join(
            f'<tr><td><a href="/lead?id={r["id"]}"><b>{e(r["name"])}</b></a><br><span class="mute small">+{e(r["whatsapp"])} · message {r["wa_step"]} sent {e(r["wa_at"][:10])}</span></td>'
            f'<td class="row">{form("lead_status", "They replied", "sm ok", "/whatsapp", id=r["id"], value="replied")}'
            f'{form("lead_suppress", "They said stop", "sm no", "/whatsapp", "Block this business on every channel?", id=r["id"])}</td></tr>'
            for r in waiting) + "</tbody></table></div>"
    out += '<h2>Quick answers for the chat</h2><div class="two">'
    for i, (title, text) in enumerate(compose.whatsapp_quick_replies(cfg)):
        out += (f'<div class="card"><div class="row end"><b>{e(title)}</b><button class="sm" data-copy="qr{i}">Copy</button></div>'
                f'<p class="small" id="qr{i}" style="margin-bottom:0">{e(text)}</p></div>')
    return "WhatsApp", "Message businesses that invite contact on WhatsApp. Written for you, sent by you.", out + "</div>"


OPP_TABS = ("new", "saved", "applied", "replied", "won")


def market_page(con, cfg, q):
    done = {r[0][9:] for r in con.execute("SELECT key FROM kv WHERE key LIKE 'platform:%'")}
    stats = dict(con.execute("SELECT status, COUNT(*) FROM opps GROUP BY status").fetchall())
    applied = sum(stats.get(s, 0) for s in ("applied", "replied", "won"))
    out = ('<div class="note"><b>The honest rules of these platforms.</b> Upwork and Fiverr close accounts that use bots or scraping, so the app '
           'never logs in or bids for you. It prepares everything, and you paste and press send. What wins: bidding within the first hour or two, '
           'a first line about the client&#39;s problem, and a short demo video. Expect to send 20 to 40 proposals before the first order; '
           'the first reviews make every later bid easier.</div>'
           f'<div class="grid"><div class="card kpi"><div class="n">{len(done)} / {len(market.PLATFORMS)}</div><div class="l">platforms set up</div></div>'
           f'<a class="card kpi" href="/opps?tab=applied"><div class="n">{applied}</div><div class="l">proposals sent</div></a>'
           f'<a class="card kpi" href="/opps?tab=replied"><div class="n">{stats.get("replied", 0)}</div><div class="l">clients replied</div></a>'
           f'<a class="card kpi" href="/opps?tab=won"><div class="n">{stats.get("won", 0)}</div><div class="l">orders won</div></a></div>')

    src = "".join(f'<option value="{k}">{e(n)}</option>' for k, n, *_ in market.PLATFORMS) + '<option value="other">Other</option>'
    out += (f'<h2 id="paste">Write a proposal for any job</h2><div class="card">{open_form("paste_job", "/opps")}'
            f'<p class="mute small" style="margin-top:0">Copy a job post from Upwork, PeoplePerHour, LinkedIn or anywhere else and paste it here. '
            f'The app writes a proposal and saves the job in “Projects to bid on” so you can track it. For a sharper proposal, ask Claude in this '
            f'project: “improve the proposals in Projects to bid on”.</p>'
            f'<div class="two"><div><label>Platform</label><select name="source">{src}</select></div>{field("budget", "Budget", "", hint="optional")}</div>'
            f'{field("title", "Job title", "", hint="optional, first line is used if empty")}{field("url", "Link to the job", "", hint="optional")}'
            f'{field("text", "Job post", "", rows=7)}<div style="margin-top:12px"><button class="go">Write proposal</button></div></form></div>')

    out += '<h2>Platforms to be on</h2>'
    for key, name, how, signup, _, steps in market.PLATFORMS:
        links = "".join(f'<a class="pill" style="margin:0 6px 6px 0" href="{e(u)}" target="_blank" rel="noopener noreferrer">{e(s)} ↗</a>'
                        for s, u in market.search_links(key))
        out += (f'<div class="card" style="margin-bottom:14px"><div class="row end"><div class="row"><span class="dot {"done" if key in done else ""}">'
                f'{"✓" if key in done else ""}</span><b>{e(name)}</b></div><div class="row">{ext(signup, "sign up")}'
                f'{form("platform_toggle", "Mark as not done" if key in done else "Mark as set up", "sm" if key in done else "sm ok", "/market", key=key)}</div></div>'
                f'<p class="mute small" style="margin:8px 0">{e(how)}</p><ol class="small" style="margin:0 0 10px;padding-left:20px">'
                + "".join(f"<li>{e(s)}</li>" for s in steps) + "</ol>"
                + (f'<div class="small mute" style="margin-bottom:6px">Open a live search (newest first):</div><div>{links}</div>' if links else "") + "</div>")

    out += '<h2>Profile texts (copy and paste)</h2><div class="two">'
    for i, (title, text) in enumerate(market.profile_kit(cfg)):
        out += (f'<div class="card"><div class="row end"><b>{e(title)}</b><button class="sm" data-copy="pk{i}">Copy</button></div>'
                f'<pre class="mail" id="pk{i}" style="margin-top:10px">{e(text)}</pre></div>')
    out += ('</div><h2>Ready-made Fiverr gigs</h2><p class="mute small">Four gigs you can publish today. Prices are starting suggestions for a seller with '
            'no reviews; raise them after your first five-star orders. The same texts work as PeoplePerHour offers and Contra services.</p>')
    for i, g in enumerate(market.GIGS):
        packs = "".join(f"<tr><td><b>{e(n)}</b></td><td>{e(p)}</td><td>{e(d)}</td></tr>" for n, p, d in g["packages"])
        faq = "".join(f"<p class='small'><b>{e(a)}</b><br>{e(b)}</p>" for a, b in g["faq"])
        block = lambda label, key, text: (f'<div class="row end" style="margin-top:14px"><span class="mute small">{label}</span>'
                                          f'<button class="sm" data-copy="g{i}{key}">Copy</button></div><pre class="mail" id="g{i}{key}">{e(text)}</pre>')
        out += (f'<details class="card" style="margin-bottom:12px" {"open" if i == 0 else ""}><summary style="cursor:pointer"><b>{e(g["title"])}</b></summary>'
                f'<p class="mute small">Category: {e(g["category"])}</p>{block("Gig title", "t", g["title"])}{block("Description", "d", g["description"])}'
                f'{block("Search tags", "g", g["tags"])}<div class="mute small" style="margin-top:14px">Packages</div><table><tbody>{packs}</tbody></table>'
                f'<div class="mute small" style="margin-top:14px">FAQ</div>{faq}{block("Questions to ask the buyer (requirements)", "r", g["requirements"])}</details>')
    return "Marketplaces", "Upwork, Fiverr and five more places where clients are already looking for this work.", out


PAGES = {"/market": market_page, "/whatsapp": whatsapp_page, "/opps": opps_page, "/youtube": youtube_page, "/": home, "/find": find, "/drafts": drafts, "/send": send, "/calls": calls, "/pipeline": pipeline,
         "/leads": leads, "/lead": lead, "/posts": posts, "/templates": templates, "/settings": settings, "/playbook": playbook}


# ---------------------------------------------------------------- actions
def split_list(v):
    return [x.strip() for x in re.split(r"[,\n]", v) if x.strip()]


def act(con, f, multi):
    """Apply one button press. Returns a short message for the user."""
    a, i = f.get("action", ""), f.get("id")
    cfg = config.load()
    busy = "Another task is still running. Wait for it to finish."
    if a == "job_daily":
        return "Daily routine started." if start_job("Daily routine", job_daily) else busy
    if a == "job_find":
        niche, city = f.get("niche", ""), f.get("city", "").strip()
        if niche not in sources.NICHES or not city:
            return "Choose a type of business and enter a city."
        limit = max(1, min(int(f.get("limit") or 40), 200))
        return f"Searching {city}." if start_job(f"Find {NICHE_LABEL[niche]} in {city}", job_find([(niche, city)], limit)) else busy
    if a == "job_enrich":
        return "Reading websites." if start_job("Read websites", job_enrich(80, retry=bool(f.get("retry")))) else busy
    if a == "job_inbox":
        return "Checking inbox." if start_job("Check inbox", job_inbox) else busy
    if a == "job_signals":
        return "Searching posts." if start_job("Search posts", job_signals) else busy
    if a == "job_wa_scan":
        def run(con, cfg, log):
            checked, found = enrich.check_whatsapp(con, 80, log)
            log(f"Checked {checked} websites, {found} publish a WhatsApp number.")
        return "Checking websites." if start_job("Look for WhatsApp numbers", run) else busy
    if a == "wa_sent":
        row = con.execute("SELECT * FROM leads WHERE id=?", (i,)).fetchone()
        if f.get("skip"):
            db.update_lead(con, int(i), wa_step=9)
            return "Skipped."
        if db.is_suppressed(con, row["email"] or "x@" + row["domain"]):
            return "This business asked not to be contacted."
        step = 2 if f.get("step") == "2" else 1
        con.execute("UPDATE messages SET status='cancelled' WHERE lead_id=? AND status IN ('draft','approved')", (i,))
        db.update_lead(con, int(i), wa_step=step, wa_at=db.now(), status="contacted")
        db.log(con, int(i), "whatsapp", f"message {step}: " + f.get("text", "")[:300])
        return f"Recorded. Pending emails to {row['name']} were cancelled so they are not contacted twice."
    if a == "job_opps":
        return "Searching project boards." if start_job("Search project boards", job_opps) else busy
    if a == "job_youtube":
        return "Working on the YouTube queue." if start_job("YouTube queue", job_youtube) else busy
    if a == "job_yt_connect":
        if not youtube.has_secret():
            return "Save data/client_secret.json first."
        return "Opening Google sign-in." if start_job("Connect YouTube", lambda con, cfg, log: youtube.connect(log)) else busy
    if a == "job_assemble":
        name, vertical = f.get("name", ""), bool(f.get("vertical"))
        if name not in youtube.story_folders():
            return "Choose a story folder."
        def run(con, cfg, log):
            youtube.assemble(name, log, vertical)
            log(f"{youtube.scan(con, cfg)} video(s) added to the upload queue.")
        return "Joining clips." if start_job(f"Make video: {name}", run) else busy
    if a == "opp_save":
        then = f.get("then", "")
        status = then if then in (*OPP_TABS, "dismissed") else None
        con.execute("UPDATE opps SET proposal=?, status=COALESCE(?, status) WHERE id=?",
                    (f.get("proposal", "").replace("\r\n", "\n"), status, i))
        return {"applied": "Marked as applied. Good luck.", "saved": "Saved for later.", "dismissed": "Removed.",
                "replied": "Moved to replied. Answer them fast.", "won": "Congratulations on the win."}.get(then, "Saved.")
    if a == "platform_toggle":
        key = f.get("key", "")
        if key in {p[0] for p in market.PLATFORMS}:
            done = con.execute("SELECT 1 FROM kv WHERE key=?", ("platform:" + key,)).fetchone()
            con.execute("DELETE FROM kv WHERE key=?", ("platform:" + key,)) if done else \
                con.execute("INSERT INTO kv VALUES(?, 'done')", ("platform:" + key,))
        return ""
    if a == "paste_job":
        text = f.get("text", "").replace("\r\n", "\n").strip()
        if len(text) < 40:
            return "Paste the full job post first."
        title = (f.get("title", "").strip() or text.splitlines()[0])[:140]
        url = f.get("url", "").strip()
        url = url if url.startswith(("http://", "https://")) else f"pasted-{db.now()}"
        source = f.get("source", "upwork") if f.get("source") in {p[0] for p in market.PLATFORMS} else "other"
        cur = con.execute("INSERT OR IGNORE INTO opps(source,url,title,snippet,budget,score,proposal,found_at,posted_at)"
                          " VALUES(?,?,?,?,?,?,?,?,?)",
                          (source, url, title, text[:4000], f.get("budget", "").strip()[:60], max(50, opps.rank(title, text)),
                           opps.proposal(title, text, cfg), db.now(), db.now()))
        return "Proposal written. It is at the top of the list." if cur.rowcount else "That job is already in your list."
    if a == "idea_add":
        if not f.get("title", "").strip():
            return "Give the story a title."
        con.execute("INSERT INTO videos(title, script, status, created_at) VALUES(?,?,?,?)",
                    (f["title"].strip()[:100], f.get("script", "").replace("\r\n", "\n"), "idea", db.now()))
        return "Script added."
    if a == "video_save":
        con.execute("UPDATE videos SET title=?, description=?, tags=? WHERE id=? AND status<>'uploaded'",
                    (f.get("title", "").strip()[:100], f.get("description", "").replace("\r\n", "\n"), f.get("tags", ""), i))
        return "Saved."
    if a == "video_delete":
        con.execute("DELETE FROM videos WHERE id=? AND status<>'uploaded'", (i,))
        return "Removed."
    if a == "job_send_dry":
        return "Test run started." if start_job("Test run", job_send(False)) else busy
    if a == "job_send_live":
        if f.get("sure") != "yes":
            return "Tick the confirmation box first."
        if mailer.preflight(cfg):
            return "Setup is not finished, so nothing was sent."
        return "Sending started." if start_job("Sending emails", job_send(True)) else busy

    if a == "approve_all":
        n = con.execute("UPDATE messages SET status='approved' WHERE status='draft'").rowcount
        return f"Approved {n} email(s). Go to Send when you are ready."
    if a == "unapprove":
        con.execute("UPDATE messages SET status='draft' WHERE status='approved' AND id=?", (i,))
        return "Moved back to drafts."
    if a == "save_draft":
        then = f.get("then", "")
        status = {"approve": "approved", "reject": "cancelled"}.get(then, "draft")
        con.execute("UPDATE messages SET subject=?, body=?, status=? WHERE id=? AND status='draft'",
                    (f.get("subject", "").strip(), f.get("body", "").replace("\r\n", "\n").strip(), status, i))
        return {"approve": "Saved and approved.", "reject": "Draft rejected."}.get(then, "Saved.")
    if a == "rebuild_all":
        ids = [r[0] for r in con.execute("SELECT lead_id FROM messages WHERE status='draft' AND step=1")]
        for lid in ids:
            compose.rebuild_first(con, cfg, lid)
        return f"Refreshed {len(ids)} draft(s) with your current details and templates."
    if a == "lead_opener":
        db.update_lead(con, int(i), opener=f.get("opener", "").strip())
        compose.rebuild_first(con, cfg, int(i))
        return "Email rebuilt with your first line."
    if a == "lead_status" and f.get("value") in MOVE_TO:
        db.update_lead(con, int(i), status=f["value"])
        db.log(con, int(i), "manual", f"moved to {f['value']}")
        return f"Moved to {f['value']}."
    if a == "lead_edit":
        row = con.execute("SELECT * FROM leads WHERE id=?", (i,)).fetchone()
        fields = {"email": f.get("email", "").strip().lower(), "phone": f.get("phone", "").strip(),
                  "whatsapp": re.sub(r"\D", "", f.get("whatsapp", ""))[:15],
                  "channel": "email" if f.get("channel") == "email" else "manual"}
        if fields["channel"] == "email" and not (fields["email"] and config.email_policy(cfg, row["country"])[0]):
            fields["channel"] = "manual"
        if f.get("status") in MOVE_TO:
            fields["status"] = f["status"]
        if f.get("note", "").strip():
            fields["notes"] = (row["notes"] + "\n" if row["notes"] else "") + f"{db.now()[:10]}  {f['note'].strip()}"
        db.update_lead(con, int(i), **fields)
        db.log(con, int(i), "manual", "edited in dashboard")
        return "Saved." + ("" if fields["channel"] == f.get("channel") else " Email is not allowed for this lead, so it stays on manual.")
    if a == "lead_suppress":
        row = con.execute("SELECT * FROM leads WHERE id=?", (i,)).fetchone()
        db.suppress(con, row["domain"], "blocked in dashboard")
        if row["email"]:
            db.suppress(con, row["email"], "blocked in dashboard")
        return f"{row['name']} will never be contacted."
    if a == "lead_add":
        lid = db.add_lead(con, f.get("name", ""), f.get("website", ""), f.get("niche", ""), f.get("country", ""),
                          f.get("city", ""), f.get("phone", ""), f.get("email", ""), "manual")
        return "Lead added. Press “Read waiting websites” to score it." if lid else "Not added: no valid website, already known, or blocked."
    if a == "import_csv":
        tmp = ROOT / "data" / "_paste.csv"
        tmp.write_text(f.get("csv", ""), encoding="utf-8")
        total, added = sources.import_csv(con, tmp)
        tmp.unlink()
        return f"Read {total} rows, added {added} new leads."
    if a == "post_done":
        con.execute("UPDATE intent SET status='done' WHERE id=?", (i,))
        return ""
    if a in ("save_template", "new_template"):
        name = f.get("name", "")
        if not re.fullmatch(r"[a-z]+", name):
            return "Invalid template name."
        path = compose.TEMPLATES / f"{name}.txt"
        if a == "new_template":
            if not path.exists():
                path.write_text((compose.TEMPLATES / "generic.txt").read_text(encoding="utf-8"), encoding="utf-8")
            return f"Template created for {name}. Edit it below."
        text = f.get("text", "").replace("\r\n", "\n")
        if "=== step 1 ===" not in text or "Subject:" not in text:
            return "Not saved: the template must keep “=== step 1 ===” and a “Subject:” line."
        path.write_text(text.strip() + "\n", encoding="utf-8")
        return "Template saved."
    if a == "save_settings":
        ints = {"daily_cap", "min_score", "per_search_limit", "lookback_days", "per_day", "daily_limit", "followup_after"}
        changes = {}
        for key, vals in multi.items():
            if "." not in key:
                continue
            sec, k = key.split(".", 1)
            v = vals[-1].strip()
            if k in ints:
                v = int(v or 0)
            elif k in ("auto_approve_followups", "enabled"):
                v = v == "true"
            elif k == "privacy":
                v = v if v in ("private", "unlisted", "public") else "private"
            elif k == "default_tags":
                v = split_list(v)
            elif k == "niches":
                v = [x for x in vals if x in sources.NICHES]
            elif k == "followup_days":
                v = [int(x) for x in split_list(v)] or [3, 7]
            elif k in ("email_ok", "email_b2b", "whatsapp_ok"):
                v = [x.upper() for x in split_list(v)]
            elif k in ("cities", "queries"):  # also opportunities.queries
                v = [x.strip() for x in v.splitlines() if x.strip()]
            changes[(sec, k)] = v
        config.save_values(changes)
        return "Settings saved."
    if a == "save_mailbox":
        config.save_values({("sending", "from_email"): f.get("sending.from_email", "").strip()})
        config.save_env({k: f.get(k, "").strip() for k in
                         ("SMTP_HOST", "SMTP_PORT", "SMTP_USER", "SMTP_PASS", "IMAP_HOST", "IMAP_PORT", "IMAP_USER", "IMAP_PASS")})
        return "Mailbox saved."
    return ""


# ---------------------------------------------------------------- server
class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _local(self):
        return (self.headers.get("Host") or "").split(":")[0] in ("127.0.0.1", "localhost")

    def _send(self, code, body="", ctype="text/html; charset=utf-8", headers=()):
        data = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        for k, v in headers:
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if not self._local():
            return self._send(403, "forbidden")
        url = urllib.parse.urlsplit(self.path)
        q = urllib.parse.parse_qs(url.query)
        if url.path == "/job":
            return self._send(200, json.dumps({**JOB, "lines": JOB["lines"][-200:]}), "application/json")
        con = db.connect()
        try:
            if url.path == "/export":
                import csv
                import io
                buf = io.StringIO()
                cur = con.execute("SELECT * FROM leads ORDER BY score DESC")
                w = csv.writer(buf)
                w.writerow([d[0] for d in cur.description])
                w.writerows(cur)
                return self._send(200, "﻿" + buf.getvalue(), "text/csv; charset=utf-8",
                                  [("Content-Disposition", 'attachment; filename="vintelligence-leads.csv"')])
            if url.path not in PAGES:
                return self._send(404, "not found")
            title, sub, body = PAGES[url.path](con, config.load(), q)
            self._send(200, page(con, url.path, title, sub, body, q.get("msg", [""])[0]))
        except Exception as ex:
            self._send(500, page(con, url.path, "Something went wrong", "", f'<div class="note bad">{e(ex)}</div>'))
        finally:
            con.close()

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        multi = urllib.parse.parse_qs(self.rfile.read(min(length, 2_000_000)).decode("utf-8"), keep_blank_values=True)
        f = {k: v[-1] for k, v in multi.items()}
        if not self._local() or self.path != "/act" or not secrets.compare_digest(f.get("token", ""), TOKEN):
            return self._send(403, "forbidden")
        con = db.connect()
        try:
            msg = act(con, f, multi)
            con.commit()
        except Exception as ex:
            msg = f"Could not do that: {ex}"
        finally:
            con.close()
        back = f.get("back", "/")
        back = back if back.startswith("/") and not back.startswith("//") else "/"
        if msg:
            path, _, frag = back.partition("#")
            back = path + ("&" if "?" in path else "?") + urllib.parse.urlencode({"msg": msg}) + ("#" + frag if frag else "")
        self._send(303, headers=[("Location", back)])


def serve(port=8765, open_browser=True):
    config.load()  # reads .env so the mailbox settings are known
    ThreadingHTTPServer.allow_reuse_address = False  # on Windows, reuse would let two copies share the port
    url = f"http://127.0.0.1:{port}/"
    try:
        server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    except OSError:
        print(f"The control centre is already running at {url}")
        if open_browser:
            import webbrowser
            webbrowser.open(url)
        return
    print(f"Control centre running at {url}  (Ctrl+C to stop)")
    if open_browser:
        import webbrowser
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
