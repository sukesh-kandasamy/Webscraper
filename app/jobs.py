"""Background crawl jobs: follow each start URL (and its pagination), extract rows, store them.

At most config.MAX_CONCURRENT_JOBS run at once; the rest wait as "queued". Every fetched page is
recorded in job_pages, so a job interrupted by a server restart continues where it stopped.

Each page's rows are:
  1. extracted with the recipe, personal data masked (unless opted in for named fields)
  2. typed: money / numbers / ratings / dates become real values (normalize.py)
  3. de-duplicated: by the item's link when there is one, else by the whole row
  4. filtered by the user's request ("price under 3000"), then completed from each item's own page
     when the recipe has a detail part, then stored
Pages that fail are retried once at the end. Then the request's sort and limit are applied and a
quality report (quality.py) is saved with the job.

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

from . import audit, config, db, extractor, fetcher, normalize, privacy, quality, request_parser
from .agent.routes import RouteBroken

_stop_flags: dict[int, threading.Event] = {}
_slots = threading.BoundedSemaphore(config.MAX_CONCURRENT_JOBS)

MAX_DETAIL_PAGES = 500           # item pages opened per run at most
URL_FIELD_NAMES = ("url", "link", "href", "product_url", "item_url", "job_url", "listing_url", "detail_url")


def start(urls: list[str], recipe: dict, max_pages: int, logged_in: bool = False,
          personal: dict | None = None, scrape_id: int | None = None) -> int:
    job_id = db.create_job(urls, recipe, max_pages, logged_in=logged_in, personal=personal,
                           scrape_id=scrape_id)
    audit.log("job_started", job_id=job_id, domain=fetcher.domain_of(urls[0]), urls=len(urls),
              logged_in=logged_in, personal_fields=(personal or {}).get("fields", []))
    _launch(job_id)
    return job_id


def resume(job_id: int) -> bool:
    """Continue a paused job (after a person solved a challenge or logged in again), or retry the
    pages a finished job could not read."""
    job = db.get_job(job_id)
    if not job:
        return False
    retry_done = job["status"] == "done" and job["quality"].get("pages_failed")
    if job["status"] not in ("needs_you", "stopped", "blocked", "error") and not retry_done:
        return False
    q = dict(job["quality"])
    q["failed"] = []                 # they are fetched again; still-failing ones are added back
    db.update_job(job_id, status="queued", paused_url="", paused_kind="", message="Resuming",
                  finished_at=None, quality=_dumps(q))
    _launch(job_id)
    return True


def _dumps(d: dict) -> str:
    import json
    return json.dumps(d, ensure_ascii=False)


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


class _Stopped(Exception):
    pass


def url_field(recipe: dict) -> str:
    """The column holding each item's own link (for de-duplication and item pages), or ""."""
    detail = recipe.get("detail") or {}
    names = [f["name"] for f in recipe["fields"]]
    if detail.get("url_field") in names:
        return detail["url_field"]
    for n in names:
        if n in URL_FIELD_NAMES:
            return n
    for f in recipe["fields"]:
        if f["attr"] in ("href", "data-href"):
            return f["name"]
    return ""


class _Run:
    def __init__(self, job_id: int, job: dict):
        self.job_id, self.job = job_id, job
        self.recipe = extractor.normalize_recipe(job["recipe"])
        self.request = self.recipe.get("request") or {}
        self.names = [f["name"] for f in self.recipe["fields"]]
        self.keep_fields = tuple(job["personal"].get("fields", []))
        self.stop_flag = _stop_flags[job_id]
        self.url_field = url_field(self.recipe)
        self.errors: list[str] = []
        # Resume state: pages already fetched by this job and the rows they produced.
        self.done_pages = db.job_pages(job_id)
        existing = db.get_records(job_id)
        self.seen = {self._key(r) for r in existing}
        self.pages_done = len(self.done_pages)
        self.rows_total = len(existing)
        self.pages_total = max(len(job["urls"]), self.pages_done)
        self.q = {"items_seen": 0, "duplicates": 0, "excluded": 0, "failed": [],
                  "details_done": 0, "details_failed": 0, "details_skipped": 0, **job["quality"]}
        for k in ("rows", "pages_ok", "pages_failed", "columns", "warnings", "coverage", "details",
                  "health", "trimmed"):
            self.q.pop(k, None)       # report keys from an earlier finish; counters stay
        given = {**self.recipe.get("column_types", {}), **request_parser.column_types(self.request)}
        self.given_types = given
        self.types: dict[str, str] = dict(job["columns"])
        filters = self.request.get("filters") or []
        self.pre_filters = [f for f in filters if f["column"] in self.names]
        self.post_filters = [f for f in filters if f["column"] not in self.names]

    # ---- helpers ---------------------------------------------------------------------
    def _key(self, row: dict):
        link = row.get(self.url_field) if self.url_field else None
        if isinstance(link, str) and link.startswith(("http://", "https://")):
            return ("url", link.split("#")[0])
        return ("row", tuple(sorted((k, str(v)) for k, v in row.items())))

    def _check_stop(self):
        if self.stop_flag.is_set():
            raise _Stopped()

    def _save_progress(self, **extra):
        db.update_job(self.job_id, pages_done=self.pages_done, pages_total=self.pages_total,
                      rows=self.rows_total, quality=_dumps(self.q), **extra)

    def _type_rows(self, rows: list[dict]) -> list[dict]:
        if rows and not self.types:
            self.types = normalize.detect_types(rows, self.names, self.given_types)
            db.update_job(self.job_id, columns=_dumps(self.types))
        return normalize.normalize_rows(rows, self.types)

    def _note_skipped(self, skipped: list[dict]) -> None:
        notes = self.q.setdefault("skipped_filters", [])
        for f in skipped:
            label = request_parser.filter_label(f)
            if label not in notes:
                notes.append(label)

    # ---- pages -------------------------------------------------------------------------
    def process(self, page: fetcher.Page) -> int:
        """Store the new items of one listing page; returns how many new items it had."""
        rows = privacy.mask_rows(extractor.extract(page.html, self.recipe, page.final_url), self.keep_fields)
        rows = self._type_rows(rows)
        fresh = []
        for r in rows:
            key = self._key(r)
            if key not in self.seen:
                self.seen.add(key)
                fresh.append(r)
        self.q["items_seen"] += len(fresh)
        self.q["duplicates"] += len(rows) - len(fresh)
        usable, skipped = request_parser.split_filters(fresh, self.pre_filters)
        self._note_skipped(skipped)
        matched = request_parser.filter_rows(fresh, {"filters": usable}, self.types)
        if self.recipe.get("detail") and matched:
            matched = self.add_details(matched)
        if self.post_filters:
            usable, skipped = request_parser.split_filters(matched, self.post_filters)
            self._note_skipped(skipped)
            matched = request_parser.filter_rows(matched, {"filters": usable}, self.types)
        self.q["excluded"] += len(fresh) - len(matched)
        if matched:
            db.add_records(self.job_id, page.final_url, matched)
        self.rows_total += len(matched)
        return len(fresh)

    def add_details(self, rows: list[dict]) -> list[dict]:
        """Open each item's own page and fill in the detail columns (listing values win)."""
        detail = self.recipe["detail"]
        uf = detail["url_field"]
        as_recipe = {"item_selector": detail["item_selector"], "fields": detail["fields"],
                     "pagination": {"type": "none"}}
        for i, r in enumerate(rows, 1):
            self._check_stop()
            link = r.get(uf)
            if not isinstance(link, str) or not link.startswith(("http://", "https://")):
                continue
            if self.q["details_done"] + self.q["details_failed"] >= MAX_DETAIL_PAGES:
                self.q["details_skipped"] += 1
                continue
            if not fetcher.robots_allows(link):
                self.q["details_skipped"] += 1
                continue
            db.update_job(self.job_id, message=f"Reading item page {i} of {len(rows)} "
                                               f"({self.q['details_done']} item pages read so far): {link}")
            try:
                page = fetcher.fetch_smart(link, as_recipe)
            except (fetcher.BlockedError, fetcher.NeedsHuman, fetcher.BudgetExceeded):
                raise
            except fetcher.FetchError:
                self.q["details_failed"] += 1
                continue
            values = extractor.extract_detail(page.html, detail, page.final_url)
            values = privacy.mask_rows([values], self.keep_fields)[0]
            for k, v in values.items():
                if v in (None, ""):
                    continue
                if k not in self.types:
                    self.types[k] = self.given_types.get(k) or normalize.detect_type(k, [v])
                    db.update_job(self.job_id, columns=_dumps(self.types))
                if r.get(k) in (None, ""):
                    r.update(normalize.normalize_rows([{k: v}], {k: self.types[k]})[0])
            self.q["details_done"] += 1
        return rows

    def crawl_route(self, start_url: str) -> None:
        """Agent scrapes whose filters live inside the page: replay the recorded steps (no AI), then
        collect the pages inside the same browser."""
        from .agent import routes

        route = self.recipe["route"]
        db.update_job(self.job_id, message=f"Replaying {len(route)} recorded steps on {start_url}")

        def on_step(n, step):
            db.update_job(self.job_id, message=f"Replaying step {n} of {len(route)}: {routes.describe(step)}")

        pages = routes.replay(start_url, route, self.recipe, self.job["max_pages"], on_step=on_step)
        self.q["route_steps"] = len(route)
        for i, page in enumerate(pages):
            if i == 0 and not self.q.get("expected_total"):
                self.q["expected_total"] = quality.stated_total(page.html)
            if page.final_url not in self.done_pages:
                produced = self.process(page)
                db.add_job_page(self.job_id, page.final_url, produced)
                self.done_pages[page.final_url] = produced
                self.pages_done += 1
            self.pages_total = max(self.pages_total, self.pages_done)
            self._save_progress()

    def crawl(self, start_url: str, url: str, page_index: int) -> None:
        """Follow one chain of pages from `url` (page number `page_index` of `start_url`)."""
        visited = set()
        while url and page_index < self.job["max_pages"] and url not in visited:
            self._check_stop()
            visited.add(url)
            already = url in self.done_pages
            db.update_job(self.job_id, message=("Re-reading " if already else "Fetching ") + url)
            try:
                page = fetcher.fetch_smart(url, self.recipe, max_rounds=self.job["max_pages"])
            except (fetcher.BlockedError, fetcher.NeedsHuman, fetcher.BudgetExceeded):
                raise           # handled by _run: pause, stop or block the whole job
            except fetcher.FetchError as e:
                self.q["failed"].append({"url": url, "start": start_url, "index": page_index,
                                         "error": str(e)[:300]})
                return
            if page_index == 0 and start_url == self.job["urls"][0] and not self.q.get("expected_total"):
                self.q["expected_total"] = quality.stated_total(page.html)

            if not already:
                produced = self.process(page)
                db.add_job_page(self.job_id, url, produced)
                self.done_pages[url] = produced
                self.pages_done += 1
            produced = self.done_pages[url]

            nxt = extractor.next_page_url(page.html, self.recipe, page.final_url, page_index, visited)
            page_index += 1
            # A page with no new items means pagination ran past the end.
            if nxt and produced and page_index < self.job["max_pages"] and nxt not in visited:
                if fetcher.robots_allows(nxt):
                    if nxt not in self.done_pages:
                        self.pages_total += 1
                else:
                    self.errors.append(f"Stopped paging at {nxt}: disallowed by robots.txt")
                    nxt = None
            else:
                nxt = None
            url = nxt
            self._save_progress()

    def retry_failed(self) -> None:
        """Failed pages get one more try at the end of the run (after the polite delay)."""
        failed, self.q["failed"] = self.q["failed"], []
        for f in failed:
            self._check_stop()
            db.update_job(self.job_id, message=f"Retrying a page that failed: {f['url']}")
            self.crawl(f.get("start") or f["url"], f["url"], int(f.get("index") or 0))

    def finish(self) -> dict:
        """Apply sort / limit, build the quality report and the health check."""
        if (self.request.get("sort") or {}).get("column") or self.request.get("limit"):
            items = db.get_record_pages(self.job_id)
            order = request_parser.sort_and_limit([r for _, r in items], self.request)
            pages = {id(r): p for p, r in items}
            if [id(r) for r in order] != [id(r) for _, r in items]:
                db.replace_records(self.job_id, [(pages[id(r)], r) for r in order])
            self.q["trimmed"] = self.q.get("trimmed", 0) + len(items) - len(order)
            self.rows_total = len(order)
        rows = db.get_records(self.job_id)
        names = self.names + [n for n in self.types if n not in self.names and not n.endswith(("_currency", "_unit"))]
        report = quality.build(rows, names, self.types, self.q, self.pages_done)
        if self.job.get("scrape_id"):
            prev = db.last_finished_job(self.job["scrape_id"], self.job_id)
            h = quality.health(report, prev["quality"] if prev else None)
            if h:
                report["health"] = h
                report["warnings"] = quality.warnings(report)
        return {**self.q, **report}


def _run(job_id: int) -> None:
    job = db.get_job(job_id)
    if job is None or job["status"] not in ("queued", "running"):
        return
    run = _Run(job_id, job)
    db.update_job(job_id, status="running", pages_total=run.pages_total, pages_done=run.pages_done,
                  rows=run.rows_total, message="Resuming" if run.done_pages else "Starting")

    def saved(**fields):
        db.update_job(job_id, pages_done=run.pages_done, rows=run.rows_total, quality=_dumps(run.q), **fields)

    try:
        for start_url in job["urls"]:
            if run.recipe.get("route"):
                run.crawl_route(start_url)
            else:
                run.crawl(start_url, start_url, 0)
        if run.q["failed"]:
            run.retry_failed()

        q = run.finish()
        msg = f"Done: {run.rows_total} rows from {run.pages_done} page(s)."
        issues = run.errors + [f"Could not read {f['url']}: {f['error']}" for f in run.q["failed"]]
        if issues:
            msg += " Issues: " + " | ".join(issues[:3])
        db.update_job(job_id, status="done" if run.pages_done else "error", message=msg,
                      pages_done=run.pages_done, pages_total=max(run.pages_total, run.pages_done),
                      rows=run.rows_total, finished_at=time.time(), quality=_dumps(q))
    except _Stopped:
        saved(status="stopped", message="Stopped by user", finished_at=time.time())
    except fetcher.SessionExpired as e:
        saved(status="needs_you", paused_url=e.url, paused_kind="login",
              message=f"Log in again to continue: {e} ({run.rows_total} rows saved)")
        audit.log("job_paused", job_id=job_id, domain=fetcher.domain_of(e.url), kind="login")
    except fetcher.NeedsHuman as e:
        domain = fetcher.domain_of(e.url)
        if _challenge_repeated(domain):
            fetcher.start_cooldown(domain, config.COOLDOWN_MINUTES, "repeated challenges")
            saved(status="blocked", finished_at=time.time(),
                  message=f"Stopped: {domain} showed another challenge soon after the last "
                          f"one, so it does not want automated access right now. "
                          f"{run.rows_total} rows saved.")
            audit.log("job_blocked", job_id=job_id, domain=domain, reason="repeated challenge")
        else:
            fetcher.update_settings(domain, {"last_challenge": time.time()})
            saved(status="needs_you", paused_url=e.url, paused_kind=e.kind,
                  message=f"Action needed: {e}. Open it, solve it yourself, then resume. "
                          f"({run.rows_total} rows saved)")
            audit.log("job_paused", job_id=job_id, domain=domain, kind=e.kind)
    except RouteBroken as e:
        run.q["route_broken"] = {"step": e.step, "reason": e.reason}
        saved(status="error", finished_at=time.time(),
              message=f"The recorded steps no longer work on this site (step {e.step}: {e.reason}). "
                      f"The site probably changed: re-record the route with the agent. {run.rows_total} rows saved.")
    except fetcher.BudgetExceeded as e:
        saved(status="stopped", message=f"{e} ({run.rows_total} rows saved)", finished_at=time.time())
    except fetcher.BlockedError as e:
        saved(status="blocked", message=f"Blocked: {e} ({run.rows_total} rows saved)", finished_at=time.time())
    except Exception as e:  # keep the job record meaningful instead of stuck on "running"
        saved(status="error", message=f"{type(e).__name__}: {e}", finished_at=time.time())


def collect_page(urls: list[str], recipe: dict, page: fetcher.Page, scrape_id: int | None = None,
                 logged_in: bool = False) -> int:
    """A finished one-page job from a page that was loaded elsewhere (the page the agent reached,
    whose filters exist only inside the page). Same typing, filters, item pages and quality report."""
    job_id = db.create_job(urls, recipe, 1, logged_in=logged_in, scrape_id=scrape_id)
    _stop_flags[job_id] = threading.Event()
    try:
        run = _Run(job_id, db.get_job(job_id))
        db.update_job(job_id, status="running", message="Collecting the page the agent reached")
        run.q["expected_total"] = quality.stated_total(page.html)
        produced = run.process(page)
        db.add_job_page(job_id, page.final_url, produced)
        run.done_pages[page.final_url] = produced
        run.pages_done = 1
        q = run.finish()
        db.update_job(job_id, status="done", pages_done=1, pages_total=1, rows=run.rows_total,
                      message=f"Done: {run.rows_total} rows from the page the agent reached.",
                      finished_at=time.time(), quality=_dumps(q))
    except Exception as e:
        db.update_job(job_id, status="error", message=f"{type(e).__name__}: {e}", finished_at=time.time())
    finally:
        _stop_flags.pop(job_id, None)
    return job_id
