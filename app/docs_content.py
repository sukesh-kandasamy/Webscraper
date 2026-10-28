"""The Production risks review: one source for the in-app page (/docs/production-risks) and its PDF
(scripts/build_docs_pdf.py). Change the review here; then rebuild the PDF."""

TITLE = "Production Readiness: Legal, Data and Operational Risks Review"
REVIEWED = "28 October 2026"
INTRO = ("What could go wrong if this proof of concept is used in production, how likely it is to hurt, and what to "
         "do first. Reviewed on 28 October 2026 against the app as it is today. This is a technical review, not legal "
         "advice: get a legal sign-off for the website-terms and data-protection points.")
VERDICT = [  # (state, heading, text): ok / warn
    ("ok", "Open-source licenses", "No blockers. All 196 packages, the font and the icons use permissive licenses; no "
                                   "GPL or AGPL code. Add one attribution file."),
    ("warn", "Legal and data", "Needs sign-off. Website terms, AI providers' data agreements and personal-data law "
                               "must be cleared per use case."),
    ("warn", "Architecture", "Fine for one person on their own PC. A shared or server deployment needs sign-in, "
                             "access control and HTTPS first."),
]
LICENSES_INTRO = ("The license data of all 88 packages in the app's environment and all 108 in the browser worker's "
                  "environment was read, plus the bundled scripts, font and icons. Everything is permissive (MIT, BSD, "
                  "Apache 2.0, MPL 2.0 used unmodified, SIL OFL for the font). There is no copyleft code that would "
                  "force the source to be published, even if the tool is sold or offered as a service.")
LICENSES_TODO = ("MIT requires the copyright notice to travel with every copy. The bundled Alpine.js file and the "
                 "compiled Tailwind CSS (which embeds Tailwind's Preflight base styles) ship without it, so add a "
                 "THIRD_PARTY_NOTICES file with their MIT notices, the Apache-2.0 notice for Material Symbols and a "
                 "pointer to the Roboto license. Also decide who owns the project's own code (normally the employer); "
                 "it has no LICENSE file yet.")
SOURCES = ("Sources: installed package metadata in .venv and .venv-agent, the bundled front-end files, the app's own "
           "safeguards and the agent test runs of 26-27 October 2026. Not legal advice.")

RISKS = [{'id': 'terms',
  'icon': 'public',
  'level': 'High',
  'chip': 'chip-red',
  'area': 'Legal',
  'title': "Breaking websites' terms of use",
  'happens': 'Large sites such as Booking.com and Amazon forbid robots, scrapers and automated data collection in '
             'their terms of use. Collecting from them commercially can lead to warning letters, blocked accounts '
             'or IP addresses, and claims for breach of contract. Some countries add database rights or '
             'anti-hacking laws on top.',
  'here': 'The app follows robots.txt and refuses pages it disallows, but robots.txt is only a technical signal: a '
          'page being allowed there is not legal permission. The agent was tested on booking.com and planned for '
          'amazon.com, both of which forbid scraping in their terms.',
  'todo': ['Get a legal review for every site you plan to collect from in production.',
           'Use official data feeds where they exist (Booking.com Affiliate Partner programme, Amazon Product '
           'Advertising API).',
           'Keep the built-in safeguards on: robots.txt, rate limits, never solving CAPTCHAs, read-only actions.']},
 {'id': 'ai-data',
  'icon': 'send',
  'level': 'High',
  'chip': 'chip-red',
  'area': 'Data & contracts',
  'title': 'Sending page content to outside AI providers',
  'happens': 'Page text, page summaries and HTML samples leave the company and are processed by OpenAI, Groq or '
             'Anthropic. If a page holds customer, employee or other personal or confidential data, that data is '
             'shared with a third party.',
  'here': "Planning, the agent's reasoning, column setup and the browser worker all call the AI model chosen in "
          'Settings → AI models. Personal data is masked before most calls, but page content is still sent.',
  'todo': ["Sign each provider's data processing agreement (DPA) and use their business / API terms, not consumer "
           'accounts.',
           'Write a rule for which sites and kinds of data may be collected with AI help.',
           'Keep personal-data masking on (the default).']},
 {'id': 'personal-data',
  'icon': 'lock',
  'level': 'Medium',
  'chip': 'chip-amber',
  'area': 'Law',
  'title': 'Personal data protection law',
  'happens': 'Collecting names, phone numbers or e-mail addresses without a stated purpose, a retention limit and '
             "a way to delete them can breach India's Digital Personal Data Protection Act 2023, and the GDPR for "
             'data about people in the EU. Penalties under both are large.',
  'here': 'Personal data is masked by default and can only be collected with an explicit opt-in and a purpose. The '
          'risk starts when someone switches that on.',
  'todo': ['Keep personal-data collection off unless there is an approved purpose.',
           'Write down the purpose, who may see the data, how long it is kept and how it is deleted.',
           "Use the app's delete actions to remove data that is no longer needed."]},
 {'id': 'access',
  'icon': 'key',
  'level': 'High if shared',
  'chip': 'chip-red',
  'area': 'Security',
  'title': 'No login or access control',
  'happens': 'Anyone who can reach the app can read every scraped result, use saved site logins, start agents that '
             'spend AI credit, and change the AI keys in use.',
  'here': 'The app was built as a single-user tool on your own PC: it has no accounts or passwords and trusts '
          'every request. That is safe on localhost and unsafe on any shared network or server.',
  'todo': ['Keep it on localhost (one person, one PC) for now.',
           'Before any shared or server deployment, add sign-in, roles, HTTPS and an audit of who did what.']},
 {'id': 'blocking',
  'icon': 'error',
  'level': 'Medium',
  'chip': 'chip-amber',
  'area': 'Operations',
  'title': "The company's network getting blocked",
  'happens': 'Big retail and travel sites detect automation. Repeated runs from the office connection can get the '
             'office IP address blocked or flagged, which affects everyone there, not only this tool.',
  'here': 'Booking.com already serves automated browsers a different flow, and Amazon often answers automated '
          'visits with a robot check.',
  'todo': ['Keep the built-in throttling, cooldowns and never-solve-CAPTCHA rules.',
           'Avoid high volumes or tight schedules against large commercial sites.']},
 {'id': 'cost',
  'icon': 'monitoring',
  'level': 'Medium',
  'chip': 'chip-amber',
  'area': 'Operations',
  'title': 'AI cost and quota surprises',
  'happens': 'One agent run uses roughly 30,000 to 100,000 AI tokens. Free tiers run out after a few runs a day; '
             'paid usage grows quickly with more users or scheduled runs.',
  'here': "Groq's free daily limit was used up during testing after a handful of runs. Each run has step, time and "
          'AI-call limits, but there is no monthly budget.',
  'todo': ['Use paid plans with spending limits set at the provider.',
           'Keep the per-run limits, and choose a reliable main model (for example gpt-5-mini) with a backup.']},
 {'id': 'quality',
  'icon': 'fact_check',
  'level': 'Medium',
  'chip': 'chip-amber',
  'area': 'Business',
  'title': "Results that look right but aren't",
  'happens': 'Sites change layout, A/B-test pages and show different pages to automated browsers. A run can finish '
             '"successfully" with missing or wrong columns, and decisions made on that data would be wrong.',
  'here': 'This happened in testing: one booking.com run first collected the right 15 hotels, then an automatic '
          'fix replaced them with 10 rows with empty names and prices. That fix logic has since been corrected, '
          'but the risk remains in general.',
  'todo': ['Treat collected data as needing a check before it is used for decisions.',
           "Read each result's Quality tab and warnings; re-run or re-record when columns come out empty."]}]

LICENSES = [('Web app', 'FastAPI, Starlette, Uvicorn, Jinja2, Pydantic', 'MIT / BSD-3'),
 ('Fetching and parsing', 'httpx, requests, selectolax, BeautifulSoup, lxml', 'BSD / MIT / Apache-2.0'),
 ('Results and export', 'pandas, numpy, openpyxl', 'BSD-3 / MIT'),
 ('Encryption', 'cryptography', 'Apache-2.0 or BSD-3'),
 ('Browser rendering', 'Playwright (and its Chromium)', 'Apache-2.0 (Chromium BSD-style)'),
 ('Agent browser', 'browser-use, cdp-use, bubus, browser-use-sdk', 'MIT (telemetry and cloud sync switched off)'),
 ('AI provider SDKs', 'openai, anthropic, groq, google-genai', 'Apache-2.0 / MIT'),
 ('Certificates, progress bars', 'certifi, tqdm', 'MPL-2.0 (fine when used unmodified)'),
 ('Front end', 'Alpine.js 3.14.1, Tailwind CSS', 'MIT (attribution notice to add)'),
 ('Font and icons', 'Roboto, Material Symbols', 'SIL OFL 1.1 / Apache-2.0')]

CHECKLIST = [('Legal', 'Legal review of the terms of use of every site to be collected from; official APIs where they exist.'),
 ('Contracts', 'Data processing agreements and business plans with the AI providers in use, with spending limits.'),
 ('Data', 'Written purpose, access, retention and deletion rules; personal-data collection stays off by default.'),
 ('Security', 'Stay on localhost, or add sign-in, roles, HTTPS and an audit trail before sharing the app.'),
 ('Operations', 'Agree run volumes per site; keep throttling, cooldowns and the never-solve-CAPTCHA rule.'),
 ('Quality', 'A person checks the Quality tab before collected data is used for decisions.'),
 ('Paperwork', "Add the THIRD_PARTY_NOTICES file and decide the project's own license and owner.")]
