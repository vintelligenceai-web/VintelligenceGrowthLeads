# Vintelligence client-acquisition playbook

The software in this folder finds businesses, reads their websites, writes emails, sends them
slowly and tracks replies. It cannot close a deal for you. This playbook covers the parts that
decide whether the emails turn into paying clients.

## 1. Who to sell to

Sell to one kind of business at a time. "AI automation for everyone" gets ignored; "we answer
the calls your dental front desk misses" gets replies.

Start with businesses that have all four of these:

1. Revenue arrives by phone (bookings, orders, enquiries).
2. Calls are missed often (busy staff, closed evenings and weekends).
3. One missed call costs real money (a new dental patient, a table of six, a property viewing).
4. The owner can decide alone, without a committee.

| Order | Niche | Why |
|---|---|---|
| 1 | Dental and private clinics | High value per patient, front desk overloaded, owner decides |
| 2 | Restaurants | Calls ring out during service; your POS work is a real advantage here |
| 3 | Real estate agencies | Speed of reply wins the client; evenings and weekends matter |
| 4 | Salons, vets, home services | Same pattern, smaller budgets |

Pick one niche and two or three cities, run for four weeks, then judge. Set them in
`config.toml` under `[targets]`.

Country order: **US first** (largest market, clearest email rules), then UK, Ireland, Canada,
Australia. For Germany, Netherlands, Italy, Spain and most of the EU, see section 4.

## 2. Fix these before sending a single email

Every prospect who is interested will open vintelligenceai.com. Today the site has gaps that
will lose them:

| Gap | Fix |
|---|---|
| Contact email is a Gmail address | Use an address on your own domain (hello@vintelligenceai.com) |
| No live demo | Put a phone number on the site that a visitor can call and talk to the agent. This is your strongest sales tool. One demo agent per niche (dental, restaurant, real estate) |
| No way to book a call | Add a Calendly or Cal.com link, and put it in `config.toml` as `booking_link` |
| No proof | You have no case studies yet. Do 2 or 3 free or cheap pilots, then publish numbers (calls answered, bookings taken) with the client's permission |
| No prices | Even "from $X per month" filters out time-wasters and builds trust |
| No named person | Add the founder's name, photo and LinkedIn. Buyers abroad want to see who they are dealing with |

Never invent case studies, client logos or numbers. One real pilot beats ten made-up claims,
and a made-up claim found out ends the deal.

## 3. Email setup (do this once)

Cold email sent from Gmail or from your main domain goes to spam and can damage the main domain.

1. Buy one or two similar domains only for outreach (for example `getvintelligence.com`).
   Redirect them to the main website.
2. Create one or two mailboxes on each (Google Workspace or Microsoft 365).
3. Set up SPF, DKIM and DMARC for each domain. Your mailbox provider has a guide; check the
   result with a free tester such as mail-tester.com.
4. Warm up for two to three weeks: send a few normal emails per day to people who will reply.
5. Start at 5 to 10 cold emails per mailbox per day. Raise slowly to 20 to 30. The
   `daily_cap` in `config.toml` enforces this.
6. Copy `.env.example` to `.env` and fill in the mailbox details. Fill `sender_name`,
   `postal_address` and `from_email` in `config.toml`. The tool refuses to send until these exist.

Keep emails plain text, short, with no links in the first email except your signature, no
images and no attachments.

## 4. Legal rules (summary, not legal advice)

| Region | Rule | What the tool does |
|---|---|---|
| USA | Cold B2B email is allowed. You must be truthful, show a postal address and honour opt-outs | Sends. Adds address and opt-out line. Opt-outs are permanent |
| UK, Ireland, France, Sweden, Finland | B2B email to a business address is generally allowed with an opt-out. It must be relevant to their work | Sends, only to addresses published on the business's own site |
| Canada, Australia, New Zealand | Consent needed, but a published business address counts as implied consent when the message is relevant to their role | Same as above |
| Germany, Austria, Netherlands, Italy, Spain, Poland, Belgium, Denmark, others | Prior consent is needed even for B2B | Does **not** email. Leads are marked `manual`: phone them, use LinkedIn, or use their contact form |

Rules change and details vary. Before scaling in a country, check with a lawyer or the local
regulator's guidance. You can adjust the country lists in `config.toml` under `[policy]`.

The tool only collects contact details that a business publishes on its own website, prefers
shared inboxes (info@, hello@) over personal ones, and keeps a suppression list so nobody who
says no is contacted again.

## 5. Other channels (email alone is not enough)

**LinkedIn, 30 minutes a day, done by hand.** Do not use automation tools on LinkedIn; they
break its terms and get accounts restricted.

- Founder profile: clear headline ("I build AI phone agents that answer missed calls for
  dental practices"), a photo, the demo number in the Featured section.
- Send 10 to 15 connection requests a day to owners and practice managers in your niche, with
  no pitch in the request.
- Post twice a week: a short recording of the agent handling a call is the best content you have.
- For leads marked `manual`, message the owner on LinkedIn or call the business.

**People already asking.** `python vi.py signals` lists recent public posts where someone asks
about AI receptionists or missed calls. Reply by hand, be useful first, and mention
Vintelligence only where it fits. Do not paste the same pitch everywhere.

**Marketplaces.** Upwork and similar sites have steady demand for "AI voice agent", "Vapi",
"Retell", "n8n automation". The margins are lower, but it is the fastest way to get first
clients, reviews and case studies.

**Partners.** Web agencies, POS resellers, dental IT providers and marketing agencies already
have your ideal clients and have nothing like this to offer them. Offer a 15 to 20 percent
recurring referral fee. One good partner can be worth hundreds of cold emails.

## 6. From reply to paying client

1. **Reply within an hour** during their working day. US Eastern afternoon is late evening in
   Pakistan, so plan for it.
2. **15-minute call.** Ask, do not present: How many calls a day? What happens after hours?
   What is one new customer worth? What systems do you use for bookings? Who else decides?
3. **Demo built for them.** Within 48 hours, send a number they can call where the agent
   greets callers with their business name and their real opening hours.
4. **Paid pilot.** Two to four weeks on after-hours calls only, a small fixed price, with a
   clear success measure agreed before it starts (for example "20 bookings taken").
5. **Monthly plan** when the pilot hits its measure.

Pricing to test. These are rough market ranges, so adjust from what you hear on calls:
setup fee of $500 to $2,500, then $300 to $1,000 per month including a call-minute allowance,
with extra minutes billed on top. Price on the value of recovered calls, not on your hours.

Things overseas buyers will ask a Pakistan-based company: contract and invoice in USD (a
Wise or Payoneer business account is enough to start), where call recordings are stored,
whether you will sign a data-processing agreement, and for US clinics whether you handle
patient data under HIPAA. Have the answers ready. If you cannot meet HIPAA yet, say so and
start with restaurants and real estate.

## 7. Weekly rhythm and honest expectations

| When | What | Time |
|---|---|---|
| Every day | `python vi.py run`, read the report, answer replies | 15 min |
| Every day | `review`, `approve`, `send --live` | 10 min |
| Every day | LinkedIn routine and manual-channel leads | 30 min |
| Twice a week | `signals`, reply to useful posts | 20 min |
| Friday | Look at the numbers, change one thing (subject, opener, niche or city) | 30 min |

Typical cold email results for a new agency with no case studies: about 1 to 5 replies per
100 businesses contacted, and perhaps 1 call booked per 100 to 200. So expect the first
client to take several hundred contacts and four to eight weeks. A callable demo number and
one real case study raise these numbers more than any wording change.

If after 300 contacts in one niche you have no positive replies, change the niche or the
offer, not the volume.
