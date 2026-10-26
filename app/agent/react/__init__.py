"""The ReAct agent: a reason-and-act loop that runs in the app process.

Each step the agent's AI sees the plan, its checklist, what it did so far and the latest page summary,
and answers with one thought and one tool call (open_page, find_links, search, check_results, next_page,
collect, finish). The app runs the tool, guarded like everything else (the planned site only, robots.txt,
no personal data, no invented addresses), and feeds the observation back. collect() hands the results
page to the normal engine (handoff.py), after which collecting -> fixing -> done runs as before.

    start(run_id, app_url)   run the loop for an approved run on a background thread
"""
import threading

from ..llm_errors import classify  # noqa: F401  (re-exported for callers that only import react)

_threads: dict[int, threading.Thread] = {}


def start(run_id: int, app_url: str = "") -> None:
    from . import loop

    t = threading.Thread(target=loop.safe_run, args=(run_id, app_url), daemon=True, name=f"react-{run_id}")
    _threads[run_id] = t
    t.start()


def running(run_id: int) -> bool:
    t = _threads.get(run_id)
    return bool(t and t.is_alive())
