# Vintelligence growth engine

Client-acquisition system for Vintelligence (vintelligenceai.com), an AI voice agent and
automation agency in Pakistan selling to small businesses in the US, UK and Europe.
The owner is a founder, not a sales specialist; explain in plain language.

## Layout
- `vi.py` → `growth/cli.py`: all commands. `python vi.py --help`.
- `growth/sources.py` lead finding (OpenStreetMap, CSV, intent posts); `enrich.py` website
  crawl, signals, scoring; `compose.py` templates → drafts; `mailer.py` SMTP sending;
  `inbox.py` replies/opt-outs/bounces; `db.py` SQLite schema and helpers.
- `growth/web.py`: the browser control centre (`python vi.py ui`, port 8765). Server-rendered,
  no JS framework. Every button posts to `act()`; long tasks run through `start_job`.
  Escape all lead data with `e()`. Tests in `tests/test_web.py` use an in-memory database.
- Standard library only. Do not add dependencies without a strong reason.
- Tests: `python -m unittest discover -s tests`. Run after any code change.

## Rules that must not be weakened
- Nothing is sent without a human decision: either `approve` + `send --live` (or the Send
  button), or the owner switching on Autopilot in Settings (`[autopilot] enabled`, default
  false), which lets the daily routine approve and send within the daily cap. Never run
  `send --live`, `approve`, or turn Autopilot on yourself unless the user asks for it in
  that message.
- YouTube: fictional characters only, never a real person's face or voice; every upload
  keeps `containsSyntheticMedia: true`. No mass-produced or reposted content.
- Proposals and emails never invent experience, client names, numbers or prices.
- Suppression is permanent. Country policy in `config.toml [policy]` routes consent-required
  countries to `manual`; do not email them.
- Emails state only what was observed on the lead's site. No invented case studies, clients,
  numbers or guarantees.
- No scraping of LinkedIn or other sites that forbid it; no automated posting to forums.
- Outbound research requests carry a generic User-Agent with no company or personal details.

## Useful things to do when asked to "work the pipeline"
1. `python vi.py run`, then summarise the report.
2. For the top-scored drafted leads, open their website, write a specific one- or two-sentence
   opener about something real, and save it with `python vi.py set <id> --opener "..."`
   (this rebuilds the pending draft).
3. For `replied` leads, read the reply with `python vi.py show <id>` and draft an answer for
   the user to send from their mailbox.
4. On Fridays, compare reply rates by niche and city and suggest one change.
5. For new rows in `opps` (Projects to bid on), read the post and rewrite the `proposal`
   column so it answers that client's actual requirements.
6. When asked for story scripts, insert rows into `videos` with `status='idea'`, a title and
   a scene-by-scene script (visual + narration per scene), in the style of `youtube.SEED`.

## Other modules
- `growth/opps.py`: project-board search (Freelancer.com, Remotive, HN) with proposal drafts.
- `growth/youtube.py`: story scripts, ffmpeg clip assembly (`videos/stories/<name>/` →
  `videos/ready/`), OAuth and scheduled uploads. Needs `data/client_secret.json`.
- `daily.ps1` runs the daily routine; its header has the command to schedule it in Windows
  (the owner must run that command; it is not scheduled by default).
