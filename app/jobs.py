"""Background crawl jobs: follow each start URL (and its pagination), extract rows, store them."""
import threading
import time

from . import db, extractor, fetcher

_stop_flags: dict[int, threading.Event] = {}


def start(urls: list[str], recipe: dict, max_pages: int) -> int:
    job_id = db.create_job(urls, recipe, max_pages)
    _stop_flags[job_id] = threading.Event()
    threading.Thread(target=_run, args=(job_id,), daemon=True, name=f"job-{job_id}").start()
    return job_id


def stop(job_id: int) -> bool:
    flag = _stop_flags.get(job_id)
    if flag:
        flag.set()
        return True
    return False


def _run(job_id: int) -> None:
    job = db.get_job(job_id)
    recipe = extractor.normalize_recipe(job["recipe"])
    stop_flag = _stop_flags[job_id]
    pages_done = rows_total = 0
    pages_total = len(job["urls"])
    seen_rows: set[tuple] = set()
    errors: list[str] = []
    db.update_job(job_id, status="running", pages_total=pages_total)

    try:
        for start_url in job["urls"]:
            url, page_index, visited = start_url, 0, set()
            while url and page_index < job["max_pages"] and url not in visited:
                if stop_flag.is_set():
                    db.update_job(job_id, status="stopped", message="Stopped by user",
                                  finished_at=time.time())
                    return
                visited.add(url)
                db.update_job(job_id, message=f"Fetching {url}")
                try:
                    page = fetcher.fetch(url)
                except fetcher.BlockedError:
                    raise
                except fetcher.FetchError as e:
                    errors.append(str(e))
                    break

                rows = extractor.extract(page.html, recipe, page.final_url)
                fresh = []
                for r in rows:
                    key = tuple(sorted(r.items()))
                    if key not in seen_rows:
                        seen_rows.add(key)
                        fresh.append(r)
                if fresh:
                    db.add_records(job_id, page.final_url, fresh)
                rows_total += len(fresh)
                pages_done += 1

                nxt = extractor.next_page_url(page.html, recipe, page.final_url, page_index)
                page_index += 1
                # A page with no new rows means pagination ran past the end.
                if nxt and fresh and page_index < job["max_pages"] and nxt not in visited:
                    if fetcher.robots_allows(nxt):
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
    finally:
        _stop_flags.pop(job_id, None)
