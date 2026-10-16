"""Background crawl jobs: follow each start URL (and its pagination), extract rows, store them.

At most config.MAX_CONCURRENT_JOBS run at once; the rest wait as "queued". Every fetched page is
recorded in job_pages, so a job interrupted by a server restart continues where it stopped.
"""
import threading
import time

from . import config, db, extractor, fetcher

_stop_flags: dict[int, threading.Event] = {}
_slots = threading.BoundedSemaphore(config.MAX_CONCURRENT_JOBS)


def start(urls: list[str], recipe: dict, max_pages: int) -> int:
    job_id = db.create_job(urls, recipe, max_pages)
    _launch(job_id)
    return job_id


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
                except fetcher.BlockedError:
                    raise
                except fetcher.FetchError as e:
                    errors.append(str(e))
                    break

                rows = extractor.extract(page.html, recipe, page.final_url)
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
    except fetcher.BlockedError as e:
        db.update_job(job_id, status="blocked", message=f"Blocked: {e} ({rows_total} rows saved)",
                      pages_done=pages_done, rows=rows_total, finished_at=time.time())
    except Exception as e:  # keep the job record meaningful instead of stuck on "running"
        db.update_job(job_id, status="error", message=f"{type(e).__name__}: {e}",
                      pages_done=pages_done, rows=rows_total, finished_at=time.time())
