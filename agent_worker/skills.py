"""Widget skills for the browser: small, fixed routines for the widgets the AI fumbles step by step.

    pick_suggestion(index, text)   type into a box and click the matching autocomplete suggestion
    set_date(index, date)          set a date field: native date inputs directly, else through the calendar
    next_page()                    click the page's own "next page" control

Every click and every typed value is approved by the app first (/check, the same rules as the AI's own
actions) and recorded as plain input / click steps with their targets, so the hand-over's route replay can
repeat them without the AI. The routines only type the values they are given and click suggestions,
calendar days, month arrows and next-page controls.
"""
import asyncio
import datetime as dt
import json

KEEP = ["id", "name", "type", "role", "aria-label", "placeholder", "title", "href", "value", "class",
        "data-testid", "autocomplete", "alt", "data-date"]

# Shared helpers inside the page: an element's xpath, a short description of it, visibility.
_HELPERS = r"""
const KEEP = %s;
const xp = el => { const p = []; for (; el && el.nodeType === 1; el = el.parentNode) { let i = 1;
  for (let s = el.previousElementSibling; s; s = s.previousElementSibling) if (s.nodeName === el.nodeName) i++;
  p.unshift(el.nodeName.toLowerCase() + '[' + i + ']'); } return p.join('/'); };
const desc = el => { const a = {}; for (const k of KEEP) { const v = el.getAttribute(k); if (v != null) a[k] = v.slice(0, 200); }
  return {tag: el.tagName.toLowerCase(), text: (el.innerText || el.value || '').replace(/\s+/g, ' ').trim().slice(0, 200),
          attributes: a, xpath: xp(el)}; };
const vis = el => { const r = el.getBoundingClientRect(); const s = getComputedStyle(el);
  return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none'; };
const off = el => el.disabled || el.getAttribute('aria-disabled') === 'true' || /disabled/i.test(el.className || '');
const mark = el => { document.querySelectorAll('[data-us-pick]').forEach(e => e.removeAttribute('data-us-pick'));
  el.setAttribute('data-us-pick', '1'); return desc(el); };
""" % json.dumps(KEEP)

SUGGESTIONS = "(text) => {" + _HELPERS + r"""
  const sel = '[role=option],[role=listbox] li,[role=listbox] a,[class*=suggest] li,[class*=Suggest] li,' +
    '[class*=autocomplete] li,[class*=Autocomplete] li,[class*=typeahead] li,[class*=autosuggest] li,' +
    '[id*=suggest] li,[id*=autocomplete] li,[data-testid*=suggest],[data-testid*=autocomplete] li,ul.ui-autocomplete li';
  const t = text.toLowerCase().replace(/\s+/g, ' ').trim();
  const items = [...document.querySelectorAll(sel)].filter(e => vis(e) && !off(e) && (e.innerText || '').trim()
                                                         && !e.closest('nav,footer'));
  const leaf = items.filter(e => !items.some(o => o !== e && e.contains(o)));
  if (!leaf.length) return {count: 0};
  const score = e => { const s = (e.innerText || '').toLowerCase().replace(/\s+/g, ' ').trim();
    return s === t ? 3 : s.startsWith(t) ? 2 : s.includes(t) ? 1 : 0; };
  leaf.sort((a, b) => score(b) - score(a));
  if (score(leaf[0]) === 0 && leaf.length > 1)
    return {count: leaf.length, options: leaf.slice(0, 5).map(e => e.innerText.trim().slice(0, 60))};
  return {count: leaf.length, pick: mark(leaf[0])};
}"""

CALENDAR_DAY = "(iso, words) => {" + _HELPERS + r"""
  const [day, months, year] = words;
  const hit = e => { const iso_attrs = ['data-date', 'data-day', 'data-value', 'data-testid', 'datetime', 'date'];
    if (iso_attrs.some(k => (e.getAttribute(k) || '').includes(iso))) return true;
    const l = ((e.getAttribute('aria-label') || '') + ' ' + (e.getAttribute('title') || '')).toLowerCase();
    return l.includes(year) && months.some(m => l.includes(m)) && new RegExp('(^|\\D)0?' + day + '(\\D|$)').test(l); };
  const cells = [...document.querySelectorAll('[aria-label],[title],[data-date],[data-day],[data-value],[data-testid],time')]
    .filter(e => vis(e) && !off(e) && hit(e) && !e.matches('input'));
  if (cells.length) return {day: mark(cells[cells.length - 1])};
  const arrows = [...document.querySelectorAll('button,a,[role=button],span,div')].filter(e => {
    if (!vis(e) || off(e)) return false;
    const w = ((e.getAttribute('aria-label') || '') + ' ' + (e.getAttribute('title') || '') + ' ' +
               (e.children.length ? '' : (e.innerText || ''))).toLowerCase().trim();
    return /next month|next|›|»|→|>/.test(w) && w.length < 40 &&
           !!e.closest('[class*=calendar],[class*=Calendar],[class*=picker],[class*=Picker],[class*=month],[role=dialog],[role=grid]'); });
  return arrows.length ? {next: mark(arrows[0])} : {};
}"""

NEXT_PAGE = "() => {" + _HELPERS + r"""
  const ok = e => vis(e) && !off(e) && !e.closest('[class*=calendar],[class*=picker],[role=dialog]');
  const rel = [...document.querySelectorAll('a[rel~=next],link[rel~=next]')].filter(e => e.tagName === 'A' && ok(e));
  if (rel.length) return {pick: mark(rel[0])};
  const words = /^(next|next page|next ›|next »|›|»|→|>|go to next page|older|more results)$/i;
  const all = [...document.querySelectorAll('a,button,[role=button],[role=link]')].filter(e => ok(e) &&
    (words.test((e.innerText || '').trim()) || /next page|go to next|^next$/i.test(e.getAttribute('aria-label') || '')));
  return all.length ? {pick: mark(all[all.length - 1])} : {};
}"""

MONTHS = ["january", "february", "march", "april", "may", "june", "july", "august", "september", "october",
          "november", "december"]
MAX_MONTH_CLICKS = 14


async def _js(page, fn: str, *args) -> dict:
    raw = await page.evaluate(fn, *args)
    try:
        out = json.loads(raw) if isinstance(raw, str) else raw
    except ValueError:
        return {}
    return out if isinstance(out, dict) else {}


async def _click_marked(page) -> None:
    els = await page.get_elements_by_css_selector('[data-us-pick="1"]')
    if not els:
        raise RuntimeError("the element disappeared")
    await els[0].click()


class Skills:
    """Bound to one run: `check(action, element, text, url)` asks the app, `record(...)` posts a step."""

    def __init__(self, tools, check, record):
        self.tools, self.check, self.record = tools, check, record

    async def _type(self, browser_session, index: int, el: dict, text: str) -> str:
        """Type through browser-use's own input action (real key events); returns an error or ""."""
        why = self.check("input", el, text)
        if why:
            return why
        res = await self.tools.registry.execute_action("input", {"index": int(index), "text": text, "clear": True},
                                                      browser_session=browser_session)
        err = getattr(res, "error", "") or ""
        await self.record(browser_session, "input", el, {"text": text}, err)
        return err

    async def _click(self, browser_session, page, el: dict) -> str:
        why = self.check("click", el, "")
        if why:
            return why
        try:
            await _click_marked(page)
        except Exception as e:  # noqa: BLE001 - the page changed under us; the AI gets the reason
            return f"Could not click “{el.get('text', '')[:40]}”: {e}"
        await self.record(browser_session, "click", el, {}, "")
        return ""

    async def pick_suggestion(self, index: int, text: str, browser_session, describe) -> tuple[str, str]:
        """(message, error)"""
        el = await describe(browser_session, index)
        if not el:
            return "", f"No element with index {index}."
        err = await self._type(browser_session, index, el, text)
        if err:
            return "", err
        page = await browser_session.get_current_page()
        found = {}
        for _ in range(10):                      # suggestions usually appear within a second or two
            await asyncio.sleep(0.5)
            found = await _js(page, SUGGESTIONS, text)
            if found.get("pick") or found.get("options"):
                break
        if not found.get("pick"):
            if found.get("options"):
                return "", ("None of the suggestions matches “" + text + "”: " + " | ".join(found["options"]) +
                            ". Call pick_suggestion again with the exact wording of one of them.")
            return "", "No suggestion list appeared after typing. The box may need Enter or a search button instead."
        err = await self._click(browser_session, page, found["pick"])
        if err:
            return "", err
        await asyncio.sleep(0.8)
        return f"Typed “{text}” and picked the suggestion “{found['pick']['text'][:80]}”.", ""

    async def set_date(self, index: int, date: str, browser_session, describe) -> tuple[str, str]:
        try:
            d = dt.date.fromisoformat(date.strip()[:10])
        except ValueError:
            return "", "Give the date as YYYY-MM-DD."
        el = await describe(browser_session, index)
        if not el:
            return "", f"No element with index {index}."
        kind = ((el.get("attributes") or {}).get("type") or "").lower()
        if kind in ("date", "datetime-local", "month"):
            value = d.isoformat() + ("T00:00" if kind == "datetime-local" else "")
            err = await self._type(browser_session, index, el, value[:7] if kind == "month" else value)
            return ("", err) if err else (f"Set the date field to {d.isoformat()}.", "")
        why = self.check("click", el, "")
        if why:
            return "", why
        res = await self.tools.registry.execute_action("click", {"index": int(index)}, browser_session=browser_session)
        if getattr(res, "error", ""):
            return "", f"Could not open the date picker: {res.error}"
        await self.record(browser_session, "click", el, {}, "")
        page = await browser_session.get_current_page()
        words = [str(d.day), [MONTHS[d.month - 1], MONTHS[d.month - 1][:3]], str(d.year)]
        for _ in range(MAX_MONTH_CLICKS + 1):
            await asyncio.sleep(0.6)
            found = await _js(page, CALENDAR_DAY, d.isoformat(), words)
            if found.get("day"):
                err = await self._click(browser_session, page, found["day"])
                return ("", err) if err else (f"Picked {d.strftime('%d %b %Y')} in the date picker.", "")
            if not found.get("next"):
                break
            err = await self._click(browser_session, page, found["next"])
            if err:
                return "", err
        return "", (f"Could not find {d.strftime('%d %b %Y')} in the date picker. Try typing it into the field in "
                    "the site's own format, or click the day yourself.")

    async def next_page(self, browser_session) -> tuple[str, str]:
        page = await browser_session.get_current_page()
        found = await _js(page, NEXT_PAGE)
        if not found.get("pick"):
            return "", "No next-page control is visible on this page."
        before = await browser_session.get_current_page_url()
        err = await self._click(browser_session, page, found["pick"])
        if err:
            return "", err
        await asyncio.sleep(2)
        after = await browser_session.get_current_page_url()
        return f"Clicked “{found['pick']['text'][:40] or 'next'}”" + (f"; now on {after}." if after != before else "."), ""
