"""What browser-use is told for one browse sub-task. Shared by the ReAct loop's browse tool (via planner) and
scripts/live_browser_test.py, so the live test checks exactly the instructions Agent mode sends. No imports:
the agent worker's environment loads it too."""


def task_text(site: str, url: str, task: str) -> str:
    """Do this one thing on the page, then hand the page back (results_reached, or done with success=true,
    both end the sub-task on the current page)."""
    return f"""You are on {url} on {site}. Your sub-task: {task}

Rules:
- Stay on {site}. Never log in, sign up, contact anyone, buy, book, reserve, apply, post, or type personal \
information. Some actions are refused by the app; if one is refused, choose another way.
- This job keeps one browser window for all its sub-tasks, so earlier steps may already be done here (a \
destination typed, dates picked): look at what the page already shows before redoing anything.
- Pop-ups: close cookie, newsletter, app and sign-in pop-ups with their ×, "Dismiss", "No thanks" or "Reject" \
button. Closing a sign-in pop-up is fine; signing in is not. For cookies choose "reject" or "necessary only".
- Widgets have skills; use them instead of many single clicks: pick_suggestion(index, text) for a box that \
suggests places or names while you type; set_date(index, YYYY-MM-DD) for each date (check-in, then \
check-out; index = the date field or the button that opens the calendar), never clicking bare day numbers; \
next_page() for the page's own Next control.
- After each action, check that the page changed the way you expected. If the same action leaves the page \
unchanged twice, stop repeating it: try another way, or call done with success=false and say what you see.
- If the site shows a CAPTCHA or "verify you are human" page, stop and call done with success=false and \
the text "HUMAN CHECK".
- When the sub-task is done (the page shows what was asked for), call results_reached with a one-line note \
of what you did; the app reads the page you are on. Do not collect data yourself.
- If the sub-task can't be done within a few tries, call done with success=false and say what you see."""
