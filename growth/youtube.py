"""YouTube studio: story ideas, assembling clips into a finished video with ffmpeg, and scheduled uploads.

Uploads use the official YouTube Data API with your own Google account (OAuth). Every upload is marked
as containing AI-generated media, which YouTube requires for realistic synthetic content.
"""
import datetime
import json
import secrets
import shutil
import subprocess
import threading
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer

from . import db
from .config import ROOT

SCOPE = "https://www.googleapis.com/auth/youtube.upload"
SECRET = ROOT / "data" / "client_secret.json"
TOKEN = ROOT / "data" / "youtube_token.json"
VIDEOS = ROOT / "videos"
READY, STORIES = VIDEOS / "ready", VIDEOS / "stories"
VIDEO_EXT = (".mp4", ".mov", ".webm", ".mkv")
AUTH_PORT = 8766


def ensure_folders():
    for d in (READY, STORIES):
        d.mkdir(parents=True, exist_ok=True)


def has_secret():
    return SECRET.exists()


def connected():
    return TOKEN.exists()


def _client():
    data = json.loads(SECRET.read_text(encoding="utf-8"))
    return data.get("installed") or data.get("web") or data


def _post(url, fields):
    req = urllib.request.Request(url, data=urllib.parse.urlencode(fields).encode())
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def connect(log=print, timeout=300):
    """One-time sign-in: opens Google in the browser and stores a refresh token on this computer."""
    c, state, got = _client(), secrets.token_urlsafe(16), {}
    redirect = f"http://127.0.0.1:{AUTH_PORT}"

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            q = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
            if q.get("state", [""])[0] == state:
                got.update(code=q.get("code", [""])[0], error=q.get("error", [""])[0])
            body = b"<h2>Done. You can close this tab and go back to the control centre.</h2>"
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = HTTPServer(("127.0.0.1", AUTH_PORT), H)
    server.timeout = 5
    url = "https://accounts.google.com/o/oauth2/v2/auth?" + urllib.parse.urlencode({
        "client_id": c["client_id"], "redirect_uri": redirect, "response_type": "code", "scope": SCOPE,
        "access_type": "offline", "prompt": "consent", "state": state})
    log("Opening Google sign-in in your browser. Choose the channel's Google account and allow access.")
    webbrowser.open(url)
    deadline = datetime.datetime.now() + datetime.timedelta(seconds=timeout)
    try:
        while not got and datetime.datetime.now() < deadline:
            server.handle_request()
    finally:
        server.server_close()
    if not got.get("code"):
        raise RuntimeError("sign-in was not completed" + (f" ({got['error']})" if got.get("error") else ""))
    tok = _post(c.get("token_uri", "https://oauth2.googleapis.com/token"), {
        "code": got["code"], "client_id": c["client_id"], "client_secret": c["client_secret"],
        "redirect_uri": redirect, "grant_type": "authorization_code"})
    if not tok.get("refresh_token"):
        raise RuntimeError("Google did not return a refresh token; remove the app's access in your Google account and try again")
    TOKEN.write_text(json.dumps({"refresh_token": tok["refresh_token"]}), encoding="utf-8")
    log("YouTube channel connected.")


def access_token():
    c = _client()
    tok = _post(c.get("token_uri", "https://oauth2.googleapis.com/token"), {
        "client_id": c["client_id"], "client_secret": c["client_secret"],
        "refresh_token": json.loads(TOKEN.read_text(encoding="utf-8"))["refresh_token"], "grant_type": "refresh_token"})
    return tok["access_token"]


def metadata(video, cfg, now=None):
    """Request body for videos.insert. Scheduled videos must be private until their publish time."""
    y = cfg["youtube"]
    now = now or datetime.datetime.now(datetime.timezone.utc)
    tags = [t.strip() for t in (video["tags"] or "").split(",") if t.strip()] or list(y["default_tags"])
    status = {"privacyStatus": y["privacy"], "selfDeclaredMadeForKids": False, "containsSyntheticMedia": True}
    if video["publish_at"] and datetime.datetime.fromisoformat(video["publish_at"]) > now + datetime.timedelta(minutes=20):
        status.update(privacyStatus="private", publishAt=video["publish_at"].replace("+00:00", "Z"))
    return {"snippet": {"title": video["title"][:100], "description": (video["description"] or y["default_description"])[:4900],
                        "tags": tags[:30], "categoryId": str(y["category_id"])},
            "status": status}


def upload(video, cfg):
    """Upload one file (resumable protocol). Returns the YouTube video id."""
    path = ROOT / video["file"]
    size = path.stat().st_size
    auth = {"Authorization": "Bearer " + access_token()}
    start = urllib.request.Request(
        "https://www.googleapis.com/upload/youtube/v3/videos?uploadType=resumable&part=snippet,status",
        data=json.dumps(metadata(video, cfg)).encode(), method="POST",
        headers={**auth, "Content-Type": "application/json; charset=UTF-8",
                 "X-Upload-Content-Type": "video/*", "X-Upload-Content-Length": str(size)})
    try:
        with urllib.request.urlopen(start, timeout=60) as r:
            session = r.headers["Location"]
        with open(path, "rb") as f:
            put = urllib.request.Request(session, data=f, method="PUT",
                                         headers={**auth, "Content-Type": "video/*", "Content-Length": str(size)})
            with urllib.request.urlopen(put, timeout=3600) as r:
                return json.loads(r.read())["id"]
    except urllib.error.HTTPError as ex:
        detail = ex.read().decode("utf-8", "replace")[:300]
        raise RuntimeError(f"YouTube refused the upload (HTTP {ex.code}): {detail}") from None


def next_slot(con, cfg, now=None):
    """Next free publish time: videos are spread evenly, `per_day` a day."""
    now = now or datetime.datetime.now(datetime.timezone.utc)
    gap = datetime.timedelta(hours=24 / max(1, int(cfg["youtube"]["per_day"])))
    last = con.execute("SELECT MAX(publish_at) FROM videos WHERE status IN ('ready','uploaded') AND publish_at<>''").fetchone()[0]
    base = max(now, datetime.datetime.fromisoformat(last)) if last else now
    return (base + gap).replace(microsecond=0).isoformat()


def title_from(name):
    return " ".join(name.replace("_", " ").replace("-", " ").split()).strip().capitalize()[:100]


def scan(con, cfg):
    """Queue every video file in videos/ready that is not known yet. Returns how many were added."""
    ensure_folders()
    added = 0
    for p in sorted(READY.iterdir()):
        rel = p.relative_to(ROOT).as_posix()
        if p.suffix.lower() not in VIDEO_EXT or con.execute("SELECT 1 FROM videos WHERE file=?", (rel,)).fetchone():
            continue
        idea = con.execute("SELECT * FROM videos WHERE status='idea' AND lower(title)=?", (title_from(p.stem).lower(),)).fetchone()
        slot = next_slot(con, cfg)
        if idea:  # a story idea with the same name: attach the file to it
            con.execute("UPDATE videos SET file=?, status='ready', publish_at=? WHERE id=?", (rel, slot, idea["id"]))
        else:
            con.execute("INSERT INTO videos(title,file,status,publish_at,created_at) VALUES(?,?,?,?,?)",
                        (title_from(p.stem), rel, "ready", slot, db.now()))
        added += 1
    con.commit()
    return added


def upload_due(con, cfg, log=print):
    """Upload queued videos, oldest slot first, at most `per_day` (max 5) per run."""
    if not (has_secret() and connected()):
        log("YouTube is not connected yet.")
        return 0
    limit = max(1, min(int(cfg["youtube"]["per_day"]), 5))
    today = con.execute("SELECT COUNT(*) FROM videos WHERE status='uploaded' AND substr(uploaded_at,1,10)=?",
                        (db.now()[:10],)).fetchone()[0]
    done = 0
    for v in con.execute("SELECT * FROM videos WHERE status='ready' ORDER BY publish_at, id").fetchall():
        if today + done >= limit:
            break
        if not (ROOT / v["file"]).exists():
            con.execute("UPDATE videos SET status='failed', error='file is missing' WHERE id=?", (v["id"],))
            continue
        log(f"Uploading “{v['title']}” ...")
        try:
            yid = upload(v, cfg)
            con.execute("UPDATE videos SET status='uploaded', youtube_id=?, uploaded_at=?, error='' WHERE id=?",
                        (yid, db.now(), v["id"]))
            log(f"  done: https://youtu.be/{yid}")
            done += 1
        except Exception as ex:
            con.execute("UPDATE videos SET error=? WHERE id=?", (str(ex)[:400], v["id"]))
            log(f"  failed: {ex}")
            break  # quota or auth problems affect every upload; try again next run
        finally:
            con.commit()
    return done


def story_folders():
    ensure_folders()
    return sorted(d.name for d in STORIES.iterdir() if d.is_dir() and any(f.suffix.lower() in VIDEO_EXT for f in d.iterdir()))


def assemble(name, log=print, vertical=True):
    """Join the clips in videos/stories/<name>/ (in file-name order) into videos/ready/<name>.mp4.

    Clips are fitted to 1080x1920 (Shorts) or 1920x1080. If the folder has narration.mp3/.wav/.m4a it becomes
    the soundtrack, and music.mp3 (optional) is mixed quietly underneath.
    """
    if not shutil.which("ffmpeg"):
        raise RuntimeError("ffmpeg is not installed")
    folder = STORIES / name
    if folder.parent != STORIES or not folder.is_dir():
        raise RuntimeError("no such story folder")
    clips = sorted(f for f in folder.iterdir() if f.suffix.lower() in VIDEO_EXT)
    if not clips:
        raise RuntimeError("no video clips in the folder")
    find = lambda stem: next((f for f in folder.iterdir() if f.stem.lower() == stem and f.suffix.lower() in (".mp3", ".wav", ".m4a")), None)
    voice, music = find("narration"), find("music")
    w, h = (1080, 1920) if vertical else (1920, 1080)
    cmd, parts = ["ffmpeg", "-y", "-loglevel", "error"], []
    for i, c in enumerate(clips):
        cmd += ["-i", str(c)]
        parts.append(f"[{i}:v]scale={w}:{h}:force_original_aspect_ratio=decrease,pad={w}:{h}:(ow-iw)/2:(oh-ih)/2,"
                     f"setsar=1,fps=30,format=yuv420p[v{i}]")
    graph = ";".join(parts) + ";" + "".join(f"[v{i}]" for i in range(len(clips))) + f"concat=n={len(clips)}:v=1:a=0[v]"
    audio_map, n = [], len(clips)
    for a in (voice, music):
        if a:
            cmd += ["-i", str(a)]
    if voice and music:
        graph += f";[{n + 1}:a]volume=0.15[m];[{n}:a][m]amix=inputs=2:duration=first[a]"
        audio_map = ["-map", "[a]"]
    elif voice or music:
        audio_map = ["-map", f"{n}:a"]
    out = READY / f"{name}.mp4"
    cmd += ["-filter_complex", graph, "-map", "[v]", *audio_map, "-c:v", "libx264", "-preset", "medium", "-crf", "20",
            *(["-c:a", "aac", "-b:a", "160k", "-shortest"] if audio_map else ["-an"]), "-movflags", "+faststart", str(out)]
    log(f"Joining {len(clips)} clip(s)" + (" with narration" if voice else " (no narration file found, video will be silent)") + " ...")
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError("ffmpeg failed: " + result.stderr.strip()[-300:])
    log(f"Created {out.name} in the ready folder")
    return out


SEED = [
    ("The last cup of chai",
     "Lahore, a winter evening. A fictional short story for a 50-second vertical video.\n\n"
     "SCENE 1 (8s) Visual: a small roadside chai stall at dusk, steam rising, warm lantern light. An old stall owner wipes one last cup.\n"
     "Narration: Every evening for forty years, Baba Rahim poured the last cup of the day for the same customer.\n\n"
     "SCENE 2 (10s) Visual: an empty wooden stool; the cup of chai placed in front of it.\n"
     "Narration: Tonight the stool was empty. He poured the cup anyway.\n\n"
     "SCENE 3 (12s) Visual: a young woman in a simple shawl walks up, holding an old photograph.\n"
     "Narration: A young woman stopped. 'My grandfather told me, if I ever feel lost in this city, look for the lantern and the last cup.'\n\n"
     "SCENE 4 (12s) Visual: the old man's eyes fill; he pushes the cup toward her; she sits on the stool.\n"
     "Narration: Baba Rahim pushed the cup across. 'Then you are not lost,' he said. 'You are just late.'\n\n"
     "SCENE 5 (8s) Visual: wide shot, two figures under one lantern, the street going dark around them.\n"
     "Narration: Some places wait for you longer than people can.\n\n"
     "Visual style for every scene: cinematic, warm tones, shallow depth of field, fictional characters, no real people."),
    ("The rishta phone call",
     "Karachi, a light comedy. A fictional short story for a 45-second vertical video.\n\n"
     "SCENE 1 (8s) Visual: a bright living room; a mother in a neat dupatta dials a number, tea tray ready.\n"
     "Narration: Ammi had found the perfect rishta for her son. Only one step was left: the phone call.\n\n"
     "SCENE 2 (10s) Visual: split screen; a calm, friendly voice-assistant icon answers on the other side.\n"
     "Narration: 'Assalam o alaikum, you have reached the Ahmed family. How may I help?' Ammi smiled. Such a polite daughter.\n\n"
     "SCENE 3 (12s) Visual: Ammi talks happily for a long time; the son watches from the door, confused.\n"
     "Narration: For ten minutes Ammi praised her son: his job, his manners, his biryani.\n\n"
     "SCENE 4 (10s) Visual: the son whispers; Ammi's face freezes, phone still at her ear.\n"
     "Narration: 'Ammi,' he whispered, 'that is their AI receptionist.'\n\n"
     "SCENE 5 (5s) Visual: Ammi shrugs and keeps talking.\n"
     "Narration: 'Good,' said Ammi. 'At least someone in that house listens.'\n\n"
     "Visual style: bright, modern Pakistani home, gentle comedy, fictional characters, no real people."),
    ("The kite that came back",
     "Old city rooftops at Basant. A fictional short story for a 50-second vertical video.\n\n"
     "SCENE 1 (8s) Visual: rooftops full of colourful kites against a golden sky; a boy of ten holds a string.\n"
     "Narration: Bilal's kite was the smallest on the rooftops, patched with his school notebook.\n\n"
     "SCENE 2 (10s) Visual: a bigger kite cuts his string; the small kite drifts away over the old city.\n"
     "Narration: It lasted four minutes. Then the sky took it.\n\n"
     "SCENE 3 (12s) Visual: the boy runs through narrow lanes, past spice shops and bicycles, eyes on the sky.\n"
     "Narration: He ran through every lane he knew, and then through lanes he did not.\n\n"
     "SCENE 4 (12s) Visual: on a far rooftop, a girl holds his kite; she has added a new tail made of ribbon.\n"
     "Narration: A girl was waiting with it. 'It was flying badly,' she said. 'I fixed the tail.'\n\n"
     "SCENE 5 (8s) Visual: two children fly the small kite together, higher than all the others.\n"
     "Narration: That evening the smallest kite flew the highest. It only needed a second pair of hands.\n\n"
     "Visual style: golden-hour light, vivid colours, cinematic, fictional characters, no real people."),
]


def seed(con):
    """Three starter story scripts, added once."""
    if con.execute("SELECT COUNT(*) FROM videos").fetchone()[0]:
        return
    for title, script in SEED:
        con.execute("INSERT INTO videos(title, script, status, created_at) VALUES(?,?,?,?)", (title, script, "idea", db.now()))
    con.commit()
