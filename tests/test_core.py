import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from growth import compose, config, db, enrich, inbox, mailer  # noqa: E402

CFG = config.load()
CFG["company"].update(sender_name="Test Sender", postal_address="1 Test St", demo_line="", booking_link="")
CFG["sending"].update(from_email="a@outreach.test", followup_days=[0, 0], daily_cap=5)

HTML = """<a href="tel:+15125550100">Call</a> <a href="mailto:info@smile.test">mail</a>
<img src="logo@2x.png"> jane@smile.test vendor@otherco.com
<a data-cfemail="{cf}">[protected]</a>"""


def cf_encode(addr, key=0x42):
    return f"{key:02x}" + "".join(f"{ord(c) ^ key:02x}" for c in addr)


class Core(unittest.TestCase):
    def setUp(self):
        self.con = db.connect(":memory:")

    def lead(self, country="US", site="https://www.smile.test"):
        lid = db.add_lead(self.con, "Smile Dental", site, "dental", country, "Austin", source="test")
        return self.con.execute("SELECT * FROM leads WHERE id=?", (lid,)).fetchone()

    def get(self, lid):
        return self.con.execute("SELECT * FROM leads WHERE id=?", (lid,)).fetchone()

    def test_emails(self):
        html = HTML.format(cf=cf_encode("frontdesk@smile.test"))
        emails = enrich.extract_emails(html)
        self.assertIn("frontdesk@smile.test", emails)
        self.assertNotIn("logo@2x.png", emails)
        self.assertEqual(enrich.pick_email(emails, "smile.test"), "info@smile.test")
        self.assertEqual(enrich.pick_email(["vendor@otherco.com"], "smile.test"), "")

    def test_dedupe_and_junk_hosts(self):
        self.assertIsNotNone(self.lead())
        self.assertIsNone(db.add_lead(self.con, "Dup", "http://smile.test/contact"))
        self.assertIsNone(db.add_lead(self.con, "FB", "https://facebook.com/smile"))

    def test_policy_routes_consent_countries_to_manual(self):
        us, de = self.lead("US"), self.lead("DE", "https://zahn.test")
        html = HTML.format(cf=cf_encode("x@smile.test")) + " info@zahn.test"
        enrich.apply(self.con, us, html, CFG)
        enrich.apply(self.con, de, html, CFG)
        self.assertEqual(self.get(us["id"])["channel"], "email")
        self.assertEqual(self.get(de["id"])["channel"], "manual")
        compose.draft_all(self.con, CFG)
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM messages").fetchone()[0], 1)

    def test_render_drops_empty_lines(self):
        out = compose.render("Hi {name}\n{demo_sentence}\nBye", {"name": "X", "demo_sentence": ""})
        self.assertEqual(out, "Hi X\nBye")

    def test_full_sequence_dry_run_and_reply(self):
        lead = self.lead()
        enrich.apply(self.con, lead, HTML.format(cf=cf_encode("x@smile.test")), CFG)
        self.assertEqual(compose.draft_all(self.con, CFG), (1, 0))
        msg = self.con.execute("SELECT * FROM messages").fetchone()
        self.assertNotIn("{", msg["body"])
        self.assertIn("no thanks", msg["body"])
        self.assertIn("couldn't spot a way to book an appointment online", msg["body"])
        # not approved yet -> nothing goes
        self.assertEqual(mailer.send_approved(self.con, CFG, out=lambda *_: None), 0)
        self.con.execute("UPDATE messages SET status='approved'")
        self.assertEqual(mailer.send_approved(self.con, CFG, out=lambda *_: None), 1)  # dry run
        self.assertEqual(self.get(lead["id"])["status"], "drafted")  # dry run changes nothing
        # simulate a real send, then follow-up becomes due
        self.con.execute("UPDATE messages SET status='sent', sent_at=?, message_id='<1@t>'", (db.now(),))
        db.update_lead(self.con, lead["id"], status="contacted")
        self.assertEqual(compose.draft_all(self.con, CFG), (0, 1))
        follow = self.con.execute("SELECT * FROM messages WHERE step=2").fetchone()
        self.assertTrue(follow["subject"].startswith("Re: "))
        # a reply stops the sequence
        kind, who = inbox.handle(self.con, "Jane <jane@smile.test>", "Re: missed calls", "Sure, tell me more.")
        self.assertEqual((kind, who), ("reply", "Smile Dental"))
        self.assertEqual(self.get(lead["id"])["status"], "replied")
        self.assertEqual(self.con.execute("SELECT status FROM messages WHERE step=2").fetchone()[0], "cancelled")

    def test_optout_and_bounce(self):
        lead = self.lead()
        enrich.apply(self.con, lead, HTML.format(cf=cf_encode("x@smile.test")), CFG)
        compose.draft_all(self.con, CFG)
        self.con.execute("UPDATE messages SET status='approved'")
        inbox.handle(self.con, "info@smile.test", "Re: hi", "No thanks.\n\nOn Mon, X wrote:\n> hello")
        self.assertTrue(db.is_suppressed(self.con, "info@smile.test"))
        self.assertEqual(self.get(lead["id"])["status"], "suppressed")
        self.assertEqual(mailer.send_approved(self.con, CFG, out=lambda *_: None), 0)
        self.assertIsNone(db.add_lead(self.con, "Again", "https://smile.test"))

        other = self.lead(site="https://other.test")
        db.update_lead(self.con, other["id"], email="info@other.test", status="contacted")
        kind, _ = inbox.handle(self.con, "MAILER-DAEMON@x", "Undeliverable", "Could not deliver to info@other.test")
        self.assertEqual(kind, "bounce")
        self.assertEqual(self.get(other["id"])["status"], "bounced")

    def test_classify(self):
        self.assertEqual(inbox.classify("a@b.c", "Automatic reply: hi", "I am away"), "auto")
        self.assertEqual(inbox.classify("a@b.c", "Re: hi", "Interested.\n> Reply \"no thanks\" and I won't email"), "reply")

    def test_live_send_refused_without_setup(self):
        cfg = {**CFG, "company": {**CFG["company"], "postal_address": ""}}
        self.assertTrue(any("postal_address" in p for p in mailer.preflight(cfg)))


if __name__ == "__main__":
    unittest.main()
