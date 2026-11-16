"""Marketplaces: where clients post paid work, and everything needed to sell there.

Upwork and Fiverr forbid bots and scraping and close accounts that use them, so nothing here logs in or bids
for you. The app prepares the profile, gigs, searches and proposals; you paste and press send.
"""
import urllib.parse

# key, name, how it works, signup link, search link template ({q} = url-encoded phrase) or "", first steps
PLATFORMS = [
    ("upwork", "Upwork", "Clients post jobs, you send proposals. Largest market for AI agent and automation work.",
     "https://www.upwork.com/nx/signup/?dest=home", "https://www.upwork.com/nx/search/jobs/?q={q}&sort=recency",
     ["Create an agency or freelancer profile with the profile text below", "Add 2 or 3 portfolio items (a demo call recording is enough)",
      "Open the saved searches twice a day and bid on jobs less than 2 hours old",
      "Paste each job into “Write a proposal” below, adjust it, and send"]),
    ("fiverr", "Fiverr", "You publish fixed-price gigs and buyers come to you. Slow at first, then steady.",
     "https://www.fiverr.com/start_selling", "https://www.fiverr.com/search/gigs?query={q}",
     ["Create a seller account and finish the profile completely", "Publish the gigs below (a new seller may publish 4)",
      "Add a short video to each gig: a screen recording of the agent taking a call",
      "Stay online and answer every message within an hour; response time drives ranking"]),
    ("freelancer", "Freelancer.com", "Clients post projects, you bid. The app already searches it for you every day.",
     "https://www.freelancer.com/signup", "https://www.freelancer.com/jobs/?keyword={q}",
     ["Create a profile with the profile text below", "Open “Projects to bid on” in this app every day and bid on the top matches"]),
    ("pph", "PeoplePerHour", "UK-focused. Clients post projects and you can also publish fixed-price offers.",
     "https://www.peopleperhour.com/site/register", "https://www.peopleperhour.com/freelance-jobs?q={q}",
     ["Apply as a freelancer (profiles are reviewed)", "Publish two offers reusing the Fiverr gig texts", "Bid on new projects daily"]),
    ("contra", "Contra", "Commission-free marketplace popular with startups hiring AI and automation freelancers.",
     "https://contra.com/sign-up", "https://contra.com/opportunities?query={q}",
     ["Create a profile and add services using the gig texts", "Apply to matching opportunities"]),
    ("linkedin", "LinkedIn Services page", "Lets buyers send you project requests directly from your profile and company page.",
     "https://www.linkedin.com/services/", "https://www.linkedin.com/search/results/content/?keywords={q}&sortBy=%22date_posted%22",
     ["Switch on “Providing services” on the founder profile", "List the services from the profile text below",
      "Use the search link to find posts from people asking for this work, and reply by hand"]),
    ("clutch", "Clutch and GoodFirms", "Agency directories that buyers use to shortlist vendors. Free basic listing.",
     "https://clutch.co/get-listed", "",
     ["Create a free company listing on Clutch and on GoodFirms", "After your first finished project, ask the client for a review there"]),
]

SEARCHES = ["ai voice agent", "ai receptionist", "vapi", "retell ai", "ai chatbot", "whatsapp chatbot", "n8n automation",
            "make.com automation", "ai appointment booking", "twilio voice ai"]


def search_links(key):
    tmpl = next(p[4] for p in PLATFORMS if p[0] == key)
    return [(q, tmpl.format(q=urllib.parse.quote(q))) for q in SEARCHES] if tmpl else []


def profile_kit(cfg):
    """Profile texts built only from what the company actually offers. Add real results as soon as you have them."""
    c = cfg["company"]
    name = c.get("sender_name") or "[YOUR NAME]"
    return [
        ("Headline", "AI Voice Agents, Chatbots & Workflow Automation for Businesses"),
        ("Short tagline", "We build AI agents that answer your calls, book your appointments and keep your systems in sync, 24/7."),
        ("Profile overview",
         f"Hi, I'm {name}, founder of {c['name']}.\n\n"
         "We build AI systems that take repetitive conversations and admin off your team:\n\n"
         "• AI voice agents that answer and make phone calls, book appointments, take orders and hand over to a person when needed\n"
         "• AI chatbots for website, WhatsApp, Facebook, Instagram and Telegram\n"
         "• Workflow automation that connects calls, bookings, CRM and POS so nothing is typed twice\n"
         "• Appointment booking systems with calendar sync and automatic SMS and email reminders\n\n"
         "How we work:\n"
         "1. A short call to understand your process and the tools you already use\n"
         "2. A working first version within days that you can test yourself\n"
         "3. Changes from your feedback, then handover with documentation and support\n\n"
         "You will always know what is being built, when it will be ready and what it costs before we start.\n\n"
         f"See more at {c['website']}" + (f"\nTry a live agent: call {c['demo_line']}" if c.get("demo_line") else "")),
        ("Skills to add", "AI Agent Development, Voice AI, Conversational AI, AI Chatbot, Chatbot Development, Automation, n8n, "
                          "Make.com, Zapier, API Integration, Twilio, WhatsApp Business API, CRM Automation, Appointment Scheduling, Python"),
        ("First message to a new buyer",
         "Thanks for getting in touch. To give you an exact price and timeline I need three things:\n"
         "1. What should the agent do, step by step?\n2. Which tools does it need to connect to (phone system, calendar, CRM)?\n"
         "3. Roughly how many calls or chats per day?\n\nOnce I have these I will reply with a fixed quote the same day."),
    ]


# Starting prices are suggestions for a new seller with no reviews; raise them after the first five-star orders.
GIGS = [
    {"title": "I will build an AI voice agent that answers your business calls 24/7",
     "category": "Programming & Tech › AI Development › AI Agents",
     "tags": "ai voice agent, ai receptionist, voice ai, vapi, ai phone agent",
     "description":
         "Never miss a customer call again.\n\nI will build an AI voice agent that answers your phone in a natural voice, "
         "understands what the caller wants and deals with it: answering questions, booking appointments, taking messages, "
         "and passing the call to your team when a person is needed.\n\n"
         "What you get:\n• A voice agent trained on your business: services, opening hours, prices, FAQs\n"
         "• Appointment booking into your calendar\n• Call summaries sent to your email or CRM\n"
         "• Clean handover to a human for anything unusual\n• Testing with you before go-live\n\n"
         "Good for clinics, dental practices, restaurants, salons, real estate agencies and home services.\n\n"
         "Message me before ordering so I can confirm the scope and connect to the tools you already use.",
     "packages": [("Basic", "$150", "Inbound agent that answers FAQs and takes messages. 1 phone number, 3 days."),
                  ("Standard", "$400", "Basic plus appointment booking into your calendar and call summaries by email. 5 days."),
                  ("Premium", "$900", "Standard plus CRM integration, human handover rules and 14 days of tuning. 10 days.")],
     "faq": [("Which phone number does it use?", "It can use a new number or take forwarded calls from your existing one."),
             ("Does it sound like a robot?", "No. It uses a natural voice and you choose the tone. You test it before go-live."),
             ("Are the monthly platform costs included?", "No. Telephony and AI usage are billed by the providers to your own account; I will estimate them for you first.")],
     "requirements": "Business name and website, opening hours, services and prices, the questions callers ask most, your calendar tool, and who should receive call summaries."},
    {"title": "I will build an AI chatbot for your website, WhatsApp, Instagram or Facebook",
     "category": "Programming & Tech › AI Development › AI Chatbot",
     "tags": "ai chatbot, whatsapp chatbot, website chatbot, customer support bot, lead generation bot",
     "description":
         "Answer every customer message instantly, on every channel.\n\nI will build an AI chatbot that knows your business, "
         "answers questions, qualifies leads and takes bookings or orders around the clock.\n\n"
         "What you get:\n• A chatbot trained on your own content\n• Lead capture with details sent to your email, sheet or CRM\n"
         "• Booking or order taking\n• Handover to a human when the chat needs one\n\n"
         "Channels: website widget, WhatsApp, Facebook Messenger, Instagram and Telegram.\n\n"
         "Message me first with the channel you need and I will confirm the scope.",
     "packages": [("Basic", "$100", "One channel, FAQ answers from your content, lead capture. 3 days."),
                  ("Standard", "$250", "Two channels, booking or order flow, leads sent to your CRM or sheet. 5 days."),
                  ("Premium", "$600", "All channels, custom flows, human handover, 14 days of tuning. 10 days.")],
     "faq": [("Do I need the WhatsApp Business API?", "Yes for WhatsApp. I will guide you through the approval; it is in your name."),
             ("Can it answer in other languages?", "Yes. Tell me which languages your customers use.")],
     "requirements": "Your website or documents with the answers, the channels you want, what counts as a lead, and where leads should be sent."},
    {"title": "I will automate your business workflows with n8n, Make or Zapier and AI",
     "category": "Programming & Tech › AI Development › AI Automations & Agents",
     "tags": "n8n, make.com, zapier, workflow automation, ai automation",
     "description":
         "Stop copying data between tools by hand.\n\nI will connect your apps so work moves by itself: a new lead goes into the CRM, "
         "the follow-up is sent, the booking is created and your team is notified, with AI steps where judgement is needed.\n\n"
         "Examples:\n• Lead form → CRM → personal follow-up email\n• Missed call → SMS reply → task for your team\n"
         "• Booking → reminder messages → review request\n• Incoming email → AI summary and reply draft\n\n"
         "You get a documented workflow you own, with error alerts so nothing fails silently.",
     "packages": [("Basic", "$80", "One workflow, up to 5 steps, 2 apps. 2 days."),
                  ("Standard", "$220", "Up to 3 workflows or 15 steps, with AI steps and error alerts. 4 days."),
                  ("Premium", "$550", "A full process across your tools, documentation and 14 days of support. 8 days.")],
     "faq": [("Which tool do you use?", "The one that fits your budget and volume. n8n is cheapest at scale; Make and Zapier are fastest to start."),
             ("Do you need my passwords?", "No. You connect accounts yourself or invite me with limited access.")],
     "requirements": "The process step by step as it works today, the apps involved, and access or an invite to those apps."},
    {"title": "I will set up an AI appointment booking system with automatic reminders",
     "category": "Programming & Tech › AI Development › AI Automations & Agents",
     "tags": "appointment booking, ai booking, calendar automation, reminders, no show",
     "description":
         "Fill your calendar and cut no-shows without touching the phone.\n\nI will set up a booking system where customers book, "
         "reschedule and cancel by themselves, by chat or by voice, and get automatic reminders by SMS and email.\n\n"
         "What you get:\n• Online booking linked to Google or Outlook calendar\n• Automatic confirmations and reminders\n"
         "• Reschedule and cancel links\n• Optional AI voice or chat agent that books for the customer\n\n"
         "Built for clinics, salons, consultants and any service business.",
     "packages": [("Basic", "$90", "Booking page with calendar sync and email confirmations. 2 days."),
                  ("Standard", "$240", "Basic plus SMS and email reminders and reschedule flow. 4 days."),
                  ("Premium", "$650", "Standard plus an AI chat or voice agent that takes bookings. 8 days.")],
     "faq": [("Can it take payments or deposits?", "Yes, with Stripe or PayPal in the Standard and Premium packages."),
             ("Does it work for several staff?", "Yes. Each person can have their own calendar and services.")],
     "requirements": "Your services with durations and prices, working hours, staff calendars, and the reminder wording you prefer."},
]
