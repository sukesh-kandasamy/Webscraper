"""Background crawl jobs: follow each start URL (and its pagination), extract rows, store them.

At most config.MAX_CONCURRENT_JOBS run at once; the rest wait as "queued". Every fetched page is
recorded in job_pages, so a job interrupted by a server restart continues where it stopped.

Safety:
  - a CAPTCHA / challenge pauses the job as "needs_you"; a person solves it in a visible browser and
    the job resumes. A second challenge on the same site within CHALLENGE_REPEAT_MINUTES stops the
    job and puts the site on cool-down: the site clearly does not want automated access.
  - personal data (phones, emails) is masked before storing unless the job was started with an
    explicit purpose for named fields
  - jobs that use a login session store their records encrypted
"""
import threading
import time

from . import audit, config, db, extractor, fetcher, privacy

_stop_flags: dict[int, threading.Event] = {}
_slots = threading.BoundedSemaphore(config.MAX_CONCURRENT_JOBS)


def start(urls: list[str], recipe: dict, max_pages: int, logged_in: bool = False,
          personal: dict | None = None) -> int:
    job_id = db.create_job(urls, recipe, max_pages, logged_in=logged_in, personal=personal)
    audit.log("job_started", job_id=job_id, domain=fetcher.domain_of(urls[0]), urls=len(urls),
              logged_in=logged_in, personal_fields=(personal or {}).get("fields", []))
    _launch(job_id)
    return job_id


def resume(job_id: int) -> bool:
    """Continue a paused job (after a person solved a challenge or logged in again)."""
    job = db.get_job(job_id)
    if not job or job["status"] not in ("needs_you", "stopped", "blocked", "error"):
        return False
    db.update_job(job_id, status="queued", paused_url="", paused_kind="", message="Resuming",
                  finished_at=None)
    _launch(job_id)
    return True


def _challenge_repeated(domain: str) -> bool:
    last = float(fetcher.site_settings(domain).get("last_challenge") or 0)
    return time.time() - last < config.CHALLENGE_REPEAT_MINUTES * 60


def resume_pending() -> list[int]:
    """Re-launch jobs that were queued or running when the server last stopped."""
    ids = [j for j in db.pending_jobs() if j not in _stop_flags]
    for job_id in ids:
        _launch(job_id)
    return ids


def stop(job_id: int) -> bool:
    flag = _stop_flags.get(job_id)
    if flag:
        flag.set()
        return True
    return False


def _launch(job_id: int) -> None:
    _stop_flags[job_id] = threading.Event()
    threading.Thread(target=_guarded_run, args=(job_id,), daemon=True, name=f"job-{job_id}").start()


def _guarded_run(job_id: int) -> None:
    stop_flag = _stop_flags[job_id]
    # Wait for a free slot, but let a queued job be cancelled while it waits.
    while not _slots.acquire(timeout=0.5):
        if stop_flag.is_set():
            db.update_job(job_id, status="stopped", message="Stopped while queued",
                          finished_at=time.time())
            _stop_flags.pop(job_id, None)
            return
    try:
        _run(job_id)
    finally:
        _slots.release()
        _stop_flags.pop(job_id, None)


def _run(job_id: int) -> None:
    job = db.get_job(job_id)
    if job is None or job["status"] not in ("queued", "running"):
        return
    recipe = extractor.normalize_recipe(job["recipe"])
    stop_flag = _stop_flags[job_id]
    errors: list[str] = []
    keep_fields = tuple(job["personal"].get("fields", []))

    # Resume state: pages already fetched by this job and the rows they produced.
    done_pages = db.job_pages(job_id)
    seen_rows = {tuple(sorted(r.items())) for r in db.get_records(job_id)}
    pages_done = len(done_pages)
    rows_total = len(seen_rows)
    pages_total = max(len(job["urls"]), pages_done)
    db.update_job(job_id, status="running", pages_total=pages_total, pages_done=pages_done,
                  rows=rows_total, message="Resuming" if done_pages else "Starting")

    try:
        for start_url in job["urls"]:
            url, page_index, visited = start_url, 0, set()
            while url and page_index < job["max_pages"] and url not in visited:
                if stop_flag.is_set():
                    db.update_job(job_id, status="stopped", message="Stopped by user",
                                  finished_at=time.time())
                    return
                visited.add(url)
                already = url in done_pages
                db.update_job(job_id, message=("Re-reading " if already else "Fetching ") + url)
                try:
                    page = fetcher.fetch_smart(url, recipe, max_rounds=job["max_pages"])
                except (fetcher.BlockedError, fetcher.NeedsHuman, fetcher.BudgetExceeded):
                    raise           # handled below: pause, stop or block the whole job
                except fetcher.FetchError as e:
                    errors.append(str(e))
                    break

                rows = privacy.mask_rows(extractor.extract(page.html, recipe, page.final_url),
                                         keep_fields)
                fresh = []
                if not already:
                    for r in rows:
                        key = tuple(sorted(r.items()))
                        if key not in seen_rows:
                            seen_rows.add(key)
                            fresh.append(r)
                    if fresh:
                        db.add_records(job_id, page.final_url, fresh)
                    db.add_job_page(job_id, url, len(fresh))
                    done_pages[url] = len(fresh)
                    rows_total += len(fresh)
                    pages_done += 1
                produced = done_pages[url]

                nxt = extractor.next_page_url(page.html, recipe, page.final_url, page_index)
                page_index += 1
                # A page with no new rows means pagination ran past the end.
                if nxt and produced and page_index < job["max_pages"] and nxt not in visited:
                    if fetcher.robots_allows(nxt):
                        if nxt not in done_pages:
                            pages_total += 1
                    else:
                        errors.append(f"Stopped paging at {nxt}: disallowed by robots.txt")
                        nxt = None
                else:
                    nxt = None
                url = nxt
                db.update_job(job_id, pages_done=pages_done, pages_total=pages_total, rows=rows_total)

        msg = f"Done: {rows_total} rows from {pages_done} page(s)."
        if errors:
            msg += " Issues: " + " | ".join(errors[:3])
        db.update_job(job_id, status="done" if pages_done else "error", message=msg,
                      pages_done=pages_done, pages_total=max(pages_total, pages_done),
                      rows=rows_total, finished_at=time.time())
    except fetcher.SessionExpired as e:
        db.update_job(job_id, status="needs_you", paused_url=e.url, paused_kind="login",
                      message=f"Log in again to continue: {e} ({rows_total} rows saved)",
                      pages_done=pages_done, rows=rows_total)
        audit.log("job_paused", job_id=job_id, domain=fetcher.domain_of(e.url), kind="login")
    except fetcher.NeedsHuman as e:
        domain = fetcher.domain_of(e.url)
        if _challenge_repeated(domain):
            fetcher.start_cooldown(domain, config.COOLDOWN_MINUTES, "repeated challenges")
            db.update_job(job_id, status="blocked", pages_done=pages_done, rows=rows_total,
                          finished_at=time.time(),
                          message=f"Stopped: {domain} showed another challenge soon after the last "
                                  f"one, so it does not want automated access right now. "
                                  f"{rows_total} rows saved.")
            audit.log("job_blocked", job_id=job_id, domain=domain, reason="repeated challenge")
        else:
            fetcher.update_settings(domain, {"last_challenge": time.time()})
            db.update_job(job_id, status="needs_you", paused_url=e.url, paused_kind=e.kind,
                          pages_done=pages_done, rows=rows_total,
                          message=f"Action needed: {e}. Open it, solve it yourself, then resume. "
                                  f"({rows_total} rows saved)")
            audit.log("job_paused", job_id=job_id, domain=domain, kind=e.kind)
    except fetcher.BudgetExceeded as e:
        db.update_job(job_id, status="stopped", message=f"{e} ({rows_total} rows saved)",
                      pages_done=pages_done, rows=rows_total, finished_at=time.time())
    except fetcher.BlockedError as e:
        db.update_job(job_id, status="blocked", message=f"Blocked: {e} ({rows_total} rows saved)",
                      pages_done=pages_done, rows=rows_total, finished_at=time.time())
    except Exception as e:  # keep the job record meaningful instead of stuck on "running"
        db.update_job(job_id, status="error", message=f"{type(e).__name__}: {e}",
                      pages_done=pages_done, rows=rows_total, finished_at=time.time())
