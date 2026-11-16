"""Dashboard pages and buttons, against an in-memory database. Never starts a job or sends anything."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from growth import compose, config, db, enrich, web  # noqa: E402

CFG = config.load()
HTML = '<a href="tel:+15125550100">Call</a> <a href="mailto:info@smile.test">mail</a>'


class Web(unittest.TestCase):
    def setUp(self):
        self.con = db.connect(":memory:")
        for name, site, country in (("Smile Dental", "https://smile.test", "US"), ("Zahn <b>Praxis</b>", "https://zahn.test", "DE")):
            lid = db.add_lead(self.con, name, site, "dental", country, "Austin", source="test")
            lead = self.con.execute("SELECT * FROM leads WHERE id=?", (lid,)).fetchone()
            enrich.apply(self.con, lead, HTML + f" info@{db.domain_of(site)}", CFG)
        compose.draft_all(self.con, CFG)
        self.msg = self.con.execute("SELECT * FROM messages").fetchone()

    def act(self, **f):
        return web.act(self.con, f, {k: [v] for k, v in f.items()})

    def lead(self, lid=1):
        return self.con.execute("SELECT * FROM leads WHERE id=?", (lid,)).fetchone()

    def test_every_page_renders_and_escapes(self):
        for path, fn in web.PAGES.items():
            q = {"id": ["2"]} if path == "/lead" else {}
            title, sub, body = fn(self.con, CFG, q)
            html = web.page(self.con, path, title, sub, body)
            self.assertIn("VINTELLIGENCE", html, path)
            self.assertNotIn("<b>Praxis</b>", html, path)  # lead data is never trusted as HTML

    def test_draft_edit_approve_unapprove(self):
        self.act(action="save_draft", id=self.msg["id"], subject="New subject", body="Hello\r\nthere", then="approve")
        m = self.con.execute("SELECT * FROM messages WHERE id=?", (self.msg["id"],)).fetchone()
        self.assertEqual((m["subject"], m["body"], m["status"]), ("New subject", "Hello\nthere", "approved"))
        self.act(action="unapprove", id=m["id"])
        self.assertEqual(self.con.execute("SELECT status FROM messages").fetchone()[0], "draft")
        self.act(action="approve_all")
        self.assertEqual(self.con.execute("SELECT status FROM messages").fetchone()[0], "approved")

    def test_custom_opener_rebuilds_email(self):
        self.act(action="lead_opener", id=1, opener="Saw your new Round Rock office.")
        self.assertIn("Saw your new Round Rock office.", self.con.execute("SELECT body FROM messages").fetchone()[0])

    def test_consent_country_cannot_be_switched_to_email(self):
        msg = self.act(action="lead_edit", id=2, email="info@zahn.test", phone="", channel="email", status="", note="called")
        self.assertEqual(self.lead(2)["channel"], "manual")
        self.assertIn("not allowed", msg)
        self.assertIn("called", self.lead(2)["notes"])

    def test_status_and_block(self):
        self.act(action="lead_status", id=1, value="meeting")
        self.assertEqual(self.lead()["status"], "meeting")
        self.act(action="lead_status", id=1, value="suppressed")  # not a button; must be ignored
        self.assertEqual(self.lead()["status"], "meeting")
        self.act(action="lead_suppress", id=1)
        self.assertTrue(db.is_suppressed(self.con, "info@smile.test"))
        self.assertEqual(self.con.execute("SELECT status FROM messages").fetchone()[0], "cancelled")

    def test_live_send_needs_tick_and_setup(self):
        self.assertIn("Tick", self.act(action="job_send_live"))
        self.assertFalse(web.JOB["running"])

    def test_template_name_is_validated(self):
        self.assertIn("Invalid", self.act(action="save_template", name="../evil", text="=== step 1 ===\nSubject: x"))
        self.assertIn("Not saved", self.act(action="save_template", name="generic", text="broken"))

    def test_whatsapp_flow(self):
        db.update_lead(self.con, 1, whatsapp="15125550100")
        db.update_lead(self.con, 2, whatsapp="4915112345678")  # Germany is not on the allowed list
        html = web.whatsapp_page(self.con, CFG, {})[2]
        self.assertIn('data-wa="15125550100"', html)
        self.assertNotIn('data-wa="4915112345678"', html)
        self.assertIn("reply STOP", html)
        self.act(action="wa_sent", id=1, step="1", text="hello")
        self.assertEqual((self.lead()["wa_step"], self.lead()["status"]), (1, "contacted"))
        self.assertEqual(self.con.execute("SELECT status FROM messages WHERE lead_id=1").fetchone()[0], "cancelled")
        self.assertNotIn('data-wa="15125550100"', web.whatsapp_page(self.con, CFG, {})[2])  # now waiting, not listed again
        self.act(action="lead_suppress", id=1)
        self.assertIn("asked not", self.act(action="wa_sent", id=1, step="2", text="again"))

    def test_marketplace_paste_job_and_tracking(self):
        job = "Looking for someone to build an AI voice agent with Twilio that books appointments for our clinic. <script>x</script>"
        self.assertIn("Paste the full", self.act(action="paste_job", text="short"))
        self.assertIn("Proposal written", self.act(action="paste_job", source="upwork", title="", url="", text=job, budget="$500"))
        o = self.con.execute("SELECT * FROM opps").fetchone()
        self.assertEqual((o["source"], o["status"]), ("upwork", "new"))
        self.assertIn("AI voice agents", o["proposal"])
        self.assertNotIn("<script>x</script>", web.opps_page(self.con, CFG, {})[2])
        self.act(action="opp_save", id=o["id"], proposal=o["proposal"], then="won")
        self.assertEqual(self.con.execute("SELECT status FROM opps").fetchone()[0], "won")
        self.act(action="platform_toggle", key="fiverr")
        self.act(action="platform_toggle", key="nonsense")
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM kv").fetchone()[0], 1)
        html = web.market_page(self.con, CFG, {})[2]
        self.assertIn("1 / 7", html)
        self.assertIn("upwork.com/nx/search/jobs/?q=ai%20voice%20agent", html)

    def test_add_lead(self):
        self.assertIn("added", self.act(action="lead_add", name="New Co", website="newco.test", niche="salon", country="us"))
        self.assertIn("Not added", self.act(action="lead_add", name="Dup", website="newco.test"))


if __name__ == "__main__":
    unittest.main()
