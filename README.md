# Vintelligence growth engine

Finds businesses that need AI voice agents, reads their websites, writes short personal
emails, sends them slowly, and tracks every lead from first contact to won client.
Needs only Python 3.11+ (no packages to install).

Read [PLAYBOOK.md](PLAYBOOK.md) first. It covers who to target, email setup, legal rules,
and how to turn a reply into a client.

## The control centre (easiest way to use it)

```
python vi.py ui
```

Opens http://127.0.0.1:8765 in your browser. From there you can find leads, edit and approve
emails, send, work the call list, move deals through the pipeline, edit templates and change
all settings. It runs only on your computer. The commands below do the same things from the
terminal.

## One-time setup

1. Fill in `config.toml`: `sender_name`, `postal_address`, `from_email`, and if you have them
   `booking_link` and `demo_line`.
2. Copy `.env.example` to `.env` and add the outreach mailbox's SMTP and IMAP details.
3. Set your niches and cities under `[targets]` in `config.toml`.

## Daily use

```
python vi.py run            # check inbox, find new leads, read their sites, write drafts, show report
python vi.py review         # read the drafts
python vi.py approve --all  # or: approve 12 15 18
python vi.py send           # dry run: shows what would go out
python vi.py send --live    # really sends, up to the daily cap
```

Nothing is ever sent without `approve` and `--live`. Follow-ups (day 3 and day 10) are queued
automatically and stop the moment a lead replies, opts out or bounces.

## Managing leads

```
python vi.py report                         # the numbers and what needs you
python vi.py leads --status replied         # who answered
python vi.py leads --channel manual         # leads to phone or message on LinkedIn (no email allowed or found)
python vi.py show 42                        # full history of one lead
python vi.py set 42 --status meeting --note "Call booked Tuesday 3pm their time"
python vi.py set 42 --opener "Saw you just opened a second location in Round Rock."
python vi.py suppress someone@example.com   # never contact again
python vi.py signals                        # public posts asking for what you sell
python vi.py import list.csv --niche dental --country US
python vi.py export leads.csv
```

Lead statuses: `new → enriched → drafted → contacted → replied → meeting → proposal → won`
(or `lost`, `no_reply`, `bounced`, `suppressed`, `unreachable`).

## How a lead is scored (0 to 100)

Higher when: a public email exists, a phone number is prominent, no online booking and no
chat widget were found, the niche is a strong fit. Lower when the site already uses an AI
voice product. Only leads at or above `min_score` get a draft.

## Files

| Path | What |
|---|---|
| `config.toml` | All settings. No passwords |
| `.env` | Mailbox passwords. Never commit |
| `templates/*.txt` | Email sequences. Edit the wording here |
| `data/growth.db` | The CRM (SQLite). Back this up |
| `growth/` | The code |
| `daily.ps1` | Runs the daily routine; can be scheduled in Windows Task Scheduler |

Tests: `python -m unittest discover -s tests`
