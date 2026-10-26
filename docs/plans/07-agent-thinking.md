# Plan: an agent that acts on what it sees (not on a fixed script)

> **Status: phase A built; phases B–C are taken up by the ReAct agent in [08-react-agent.md](08-react-agent.md)**
> (checks after every action, site-address rules and a checklist plan are built there; widget skills,
> re-planning and site memory are its phases 2–3).
>
> Phase A:  Provider errors are named and stop the run at once (a refused Groq key: 1 AI call,
> 17 s, *"Groq refused the key. Check GROQ_API_KEY in .env…"*); a backup AI for the agent (Settings → AI models)
> takes over on rate / daily limits, no credit or a refused key (live: refused Groq key → ChatGPT gpt-5-mini,
> 32/32 books); a `done` that claims success tries the hand-over; less history and a shorter element list per
> AI call for text-only models, and tokens per run on the run card. Phase B is next.
>
> Written from the two failed booking.com runs, RUN-ZDC4M4 and RUN-H6AQX1
> (Goa hotels, 25–28 Nov 2026, under ₹20,000), the agent worker log and the code in `agent_worker/worker.py`,
> `app/agent/planner.py` and `app/agent/api.py`.

## What went wrong

### RUN-ZDC4M4: stopped by the AI provider's daily limit, reported as "could not reach the results list"

| Step | What happened |
|---|---|
| 1–4 | Opened booking.com, closed the sign-in pop-up, typed "Goa" into the search box, clicked **Search** |
| 5–8 | Every AI call failed: *Rate limit reached for model openai/gpt-oss-120b … tokens per day: Limit 200000, Used 198608* |
| end | browser-use gave up after 4 failures (no fallback model). The run said **"The agent could not reach the results list."** |

- **The real reason was hidden.** The worker doesn't recognise provider errors (rate limit, quota, bad key),
  so it showed a generic message.
- **The agent burns through Groq's free plan fast.** Groq's free plan allows 200,000 tokens a day, and each agent
  step sends 5,000–9,000 tokens (the page's elements + the task). That's about 25 steps a day for all runs together.
- **It didn't check that Search worked.** After typing "Goa" it clicked Search without choosing the
  destination from booking.com's suggestion list. The address stayed `https://www.booking.com/` and nothing
  told the agent so.

### RUN-H6AQX1: guessed addresses in a loop, then ended with the wrong action

| Step | What happened |
|---|---|
| 3–14 | Built booking.com addresses by guessing their parameters (`checkin_year=…`, `price_max=…`, `price_filter=…`). Each page showed **0 properties**. It opened nearly the same address four times, one of them exactly three times |
| 15 | Decided the list was reached, but called `done` instead of `results_reached` |
| end | The app marked the run **error** with the agent's own success sentence as the message |

- **No memory of what failed.** Nothing stopped it from trying an address it had just seen fail.
- **It can't see the page.** gpt-oss on Groq is text-only: it reads a list of clickable elements, not the
  screenshot, so "0 properties", open dropdowns and calendars are easy to miss.
- **A `done` that claims success isn't checked.** Only `results_reached` hands over to the app.

### What both runs share: a fixed script written without seeing the site

The planner writes 2–8 steps before anyone has looked at the site ("search for hotels in Goa", "set check-in
date…", "filter: price max 20000 Rs", "stop on the results list"), and the agent tries to follow them
literally. Some steps aren't needed: the app filters by price and sorts after collecting anyway. There is
no check after each action, no idea of "what is done, what is left", and no re-planning when the site
works differently from the guess.

## The idea

Turn the agent from **"follow these steps"** into **"reach this state, checking the page after every action"**:

1. **Goal as a checklist, not a script.** The plan lists only what changes *which* results the site shows
   (destination Goa, check-in 25 Nov, check-out 28 Nov). Price, rating and sort are left to the app, which
   already filters and sorts after collecting. Fewer fragile steps.
2. **The app gives the agent eyes.** After every action the app (not the AI) reads the page and tells the
   agent in plain words: did the address change, what the page says about results ("0 properties",
   "1,234 results"), visible errors ("Enter a destination"), an open suggestion list or calendar, and which
   checklist items look done (e.g. the address now has `checkin=2026-11-25`). This is cheap, needs no
   screenshot, and helps text-only models most.
3. **Learn the site's own addresses; don't invent them.** After the first real search, the site's own address
   shows its parameters; the agent may edit those (dates, page, sort). Inventing parameters the site has never
   shown is refused, and an address that gave 0 results is remembered and refused.
4. **Notice when stuck, then re-plan.** If the same action or address repeats, or 3 actions change nothing,
   the app says so and suggests the other way (use the form instead of the address, pick the suggestion,
   scroll). Still stuck → ask the planner again, this time with the page outline it has now seen.
5. **Skills for hard widgets.** Small, guarded tools for things every travel / shop / jobs site has:
   pick a suggestion from an autocomplete list, set a date in a calendar or date field, load more / next page.
6. **Remember how a site works.** After a successful run, save per site: the search form, the results
   address pattern and its parameters, pop-ups to close. The next run on that site starts from that.

## Plan

### Phase A: reliable and honest (small)

- **A1. Name provider errors.** Recognise rate limit / daily quota / bad key / model not found in the worker and
  stop at once with a clear message, e.g. *"Groq's daily token limit is used up (it resets in about 29 min).
  Pick ChatGPT for the agent in Settings → AI models, or try later."* No retries that can't succeed.
- **A2. Backup model.** browser-use supports a `fallback_llm`. Add *"If the agent's AI is rate-limited, use:"*
  to Settings → AI models (any provider with a key, checked like the others) and pass it to the worker.
- **A3. A successful `done` counts as arriving.** When the agent ends with success but didn't call
  `results_reached`, the worker tries the hand-over itself. If the app accepts the page, the run collects;
  if not, the agent hears why and keeps going.
- **A4. Fewer tokens per step.** A smaller element list for text-only models, a shorter task text, and
  tokens used shown on the run (from browser-use's usage numbers) so limits are visible.

### Phase B: see and verify

- **B1. Page check after every action** (point 2 above): the worker sends the page to the app after each
  action. The app answers with a short status the agent reads next step:
  `address changed: no · results: "0 properties" · error shown: "Enter a destination" ·
  open: suggestion list (3) · checklist: destination ✗ check-in ✗ check-out ✗`.
- **B2. A `check_results` tool** using the engine's list detection (the same check `results_reached` uses):
  *"Looks like a results list: about 25 items, e.g. 'Hotel Sea Breeze · ₹12,400'"* or *"No list: the page
  says 0 properties"*. The agent checks before handing over, and the app hands over by itself when a good list
  appears and the checklist is complete.
- **B3. Steer towards a model that sees.** Settings marks text-only models for the agent (they work on simple
  sites; travel and shopping sites need screenshots), and gpt-5-mini is suggested first.

### Phase C: think dynamically

- **C1. Checklist plans.** The planner returns *must-haves* (search term, place, dates, category, the only
  filters that change which results exist) and *app-side* conditions (price, rating, sort, limit). The
  timeline shows the checklist ticking off.
- **C2. Site-address rules** (point 3): an address is allowed only if the site produced it, or it is a
  site-produced address with its own parameters edited. Addresses that gave 0 results are refused for the
  rest of the run.
- **C3. Stuck detection and re-planning** (point 4), at most 2 re-plans per run within the AI-call limit.
- **C4. Widget skills** (point 5): `pick_suggestion(text)`, `set_date(field, 2026-11-25)`, `next_page()`;
  each one goes through the guard like any click or typing.
- **C5. Site memory** (point 6), saved with the site's settings. Re-record and new runs on the site use it.

### Phase D: measure

- **D1. A test bench**: a fixed set of tasks (books.toscrape.com category, quotes tag, scrapethissite
  AJAX + search form, and real sites: booking.com hotels with dates, one shop, one jobs site), run after each
  change. Track success rate, steps, AI calls, tokens and time per task, for a text-only and a vision model.
- **D2. Plain stop reasons** on every run: what it reached, what was left on the checklist, and why it stopped.

## Unchanged rules

The agent still only searches, filters, sorts and reads; stays on the planned site; never logs in, buys,
contacts, applies or types personal data; never solves CAPTCHAs; and uses no stealth or fingerprint tricks.
If a site gives an automated browser a different page (e.g. 0 results to a hidden browser), the answer is
the visible browser window (*Show the browser*) or *Needs you*, not disguise. A destination and travel
dates are not personal data.

## Order and size

| Order | Work | Size | Why first |
|---|---|---|---|
| 1 | A1–A4 | small | Runs stop failing for hidden reasons; the free plan lasts longer |
| 2 | B1–B2 | medium | The biggest gain in intelligence: the agent checks what each action did |
| 3 | C1–C3 | medium | Goal checklist, no invented addresses, recovery when stuck |
| 4 | C4–C5 | medium | Hard widgets and per-site memory |
| always | D1–D2 | small | Shows whether each change really helped |

**Right now, without code:** pick **ChatGPT (OpenAI) · gpt-5-mini** for the agent in Settings → AI models.
It sees screenshots, and the agent's runs no longer share Groq's 200,000-tokens-a-day free limit.
