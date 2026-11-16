"""Opportunities, YouTube helpers and autopilot. No network, no uploads, no sending."""
import datetime
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from growth import config, db, mailer, opps, web, youtube  # noqa: E402

CFG = config.load()
UTC = datetime.timezone.utc


class Opps(unittest.TestCase):
    def test_match_and_proposal(self):
        score, offers, _ = opps.match("Need an AI voice agent with Twilio to book appointments into our CRM")
        self.assertGreaterEqual(score, 80)
        self.assertEqual(len(offers), 3)
        self.assertLess(opps.match("SEO link building for my blog")[0], 40)
        text = opps.proposal("AI receptionist for dental clinic", "voice agent, appointment booking", CFG)
        self.assertIn("AI receptionist for dental clinic", text)
        self.assertIn("AI voice agents", text)
        self.assertNotRegex(text, r"\d+\+? (clients|years|projects)")  # no invented track record

    def test_page_and_actions(self):
        con = db.connect(":memory:")
        con.execute("INSERT INTO opps(source,url,title,snippet,score,proposal,found_at) VALUES('x','https://x.test/1','<i>Job</i>','s',80,'p','t')")
        html = web.opps_page(con, CFG, {})[2]
        self.assertNotIn("<i>Job</i>", html)
        web.act(con, {"action": "opp_save", "id": "1", "proposal": "new text", "then": "applied"}, {})
        self.assertEqual(tuple(con.execute("SELECT proposal, status FROM opps").fetchone()), ("new text", "applied"))


class YouTube(unittest.TestCase):
    def test_metadata_labels_ai_and_schedules_privately(self):
        now = datetime.datetime(2026, 1, 1, tzinfo=UTC)
        v = {"title": "T" * 150, "description": "", "tags": "a, b", "publish_at": "2026-01-02T10:00:00+00:00"}
        m = youtube.metadata(v, {"youtube": {**CFG["youtube"], "privacy": "public"}}, now)
        self.assertTrue(m["status"]["containsSyntheticMedia"])
        self.assertEqual((m["status"]["privacyStatus"], m["status"]["publishAt"]), ("private", "2026-01-02T10:00:00Z"))
        self.assertEqual((len(m["snippet"]["title"]), m["snippet"]["tags"]), (100, ["a", "b"]))
        past = youtube.metadata({**v, "publish_at": "2025-12-31T10:00:00+00:00"}, {"youtube": {**CFG["youtube"], "privacy": "public"}}, now)
        self.assertEqual(past["status"]["privacyStatus"], "public")
        self.assertNotIn("publishAt", past["status"])

    def test_slots_are_spread(self):
        con = db.connect(":memory:")
        now = datetime.datetime(2026, 1, 1, tzinfo=UTC)
        first = youtube.next_slot(con, CFG, now)
        con.execute("INSERT INTO videos(title,status,publish_at) VALUES('a','ready',?)", (first,))
        second = youtube.next_slot(con, CFG, now)
        gap = datetime.datetime.fromisoformat(second) - datetime.datetime.fromisoformat(first)
        self.assertEqual(gap, datetime.timedelta(hours=24 / CFG["youtube"]["per_day"]))

    def test_upload_refuses_when_not_connected(self):
        if youtube.connected():
            self.skipTest("a real channel is connected")
        self.assertEqual(youtube.upload_due(db.connect(":memory:"), CFG, log=lambda *_: None), 0)

    @unittest.skipUnless(shutil.which("ffmpeg"), "ffmpeg not installed")
    def test_assemble_joins_clips(self):
        tmp = Path(tempfile.mkdtemp())
        old = youtube.STORIES, youtube.READY
        youtube.STORIES, youtube.READY = tmp / "stories", tmp / "ready"
        try:
            story = youtube.STORIES / "demo"
            story.mkdir(parents=True)
            youtube.READY.mkdir()
            for i, (size, colour) in enumerate((("320x240", "red"), ("240x426", "blue")), 1):
                subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", f"color={colour}:s={size}:d=1",
                                str(story / f"0{i}.mp4")], check=True)
            subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "sine=frequency=440:duration=3",
                            str(story / "narration.wav")], check=True)
            out = youtube.assemble("demo", log=lambda *_: None)
            probe = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type,width,height:format=duration",
                                    "-of", "default=nw=1", str(out)], capture_output=True, text=True).stdout
            self.assertIn("width=1080", probe)
            self.assertIn("height=1920", probe)
            self.assertIn("codec_type=audio", probe)
            duration = float(next(l.split("=")[1] for l in probe.splitlines() if l.startswith("duration")))
            self.assertAlmostEqual(duration, 2.0, delta=0.3)
            with self.assertRaises(RuntimeError):
                youtube.assemble("../ready", log=lambda *_: None)
        finally:
            youtube.STORIES, youtube.READY = old
            shutil.rmtree(tmp, ignore_errors=True)


class Autopilot(unittest.TestCase):
    def test_off_by_default_and_locked_without_setup(self):
        con = db.connect(":memory:")
        con.execute("INSERT INTO messages(lead_id,step,subject,body) VALUES(1,1,'s','b')")
        off = {**CFG, "autopilot": {"enabled": False}}
        self.assertEqual(mailer.autopilot(con, off, out=lambda *_: None), 0)
        on = {**CFG, "autopilot": {"enabled": True}, "company": {**CFG["company"], "postal_address": ""}}
        self.assertEqual(mailer.autopilot(con, on, out=lambda *_: None), 0)
        self.assertEqual(con.execute("SELECT status FROM messages").fetchone()[0], "draft")  # nothing approved


if __name__ == "__main__":
    unittest.main()
