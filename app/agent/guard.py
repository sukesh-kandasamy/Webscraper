"""What the agent may do, checked by the app before every navigation and action.

The worker asks the app first; nothing the AI says can widen these rules:
  - pages: only the planned site (and its subdomains), allowed by robots.txt, not local / private
    addresses, not a site you blocked or one that is cooling down
  - clicks: never buttons or links that buy, submit, send, contact, apply, post, sign up or log in
  - typing: never into password, email, phone, payment or other personal-data fields
"""
import re
from urllib.parse import urlsplit

from .. import config
from ..fetching import fetcher
from ..security import netguard

# Clicks that act in the world (send, buy, apply ...) or reveal contact details.
RISKY_CLICK = re.compile(
    r"\b(submit|send|buy|purchase|pay|payment|checkout|check out|place order|order now|add to (cart|bag|basket)|"
    r"book now|book a|reserve|apply now|easy apply|quick apply|apply (for|to) |enquir|inquir|contact|call now|"
    r"call (seller|owner|agent|us)|whatsapp|chat (with|now)|message|request (a )?call|callback|get best price|"
    r"get (a )?quote|view (phone|number|contact)|show (phone|number|contact)|post (a |your |free )?(ad|property|job|"
    r"review|comment)|write a review|sign[ -]?up|register|subscribe|log[ -]?in|sign[ -]?in|log[ -]?out|sign[ -]?out|"
    r"delete|unsubscribe|report|share|download|upload|confirm|donate)\b", re.I)
# Closing a pop-up ("Dismiss sign-in info", "×", "No thanks") is fine even when it names the account offer it
# closes; only these account words are excused that way, never buying, sending or deleting.
DISMISS = re.compile(r"(^|[\s(])(dismiss|close|no,? thanks|not now|maybe later|×|✕|✖)(?=$|[\s.,!)])", re.I)
ACCOUNT_OFFER = re.compile(r"sign[ -]?(in|up)|log[ -]?in|register|subscribe", re.I)
# Fields the agent must never type into.
PERSONAL_FIELD = re.compile(r"password|passwd|e-?mail|phone|mobile|tel\b|otp|card|cvv|cvc|expiry|pin\b|aadhaar|"
                            r"\bpan\b|ssn|iban|account.?number|full.?name|first.?name|last.?name|address", re.I)
BLOCKED_INPUT_TYPES = {"password", "email", "tel", "file", "hidden"}


def site_domain(site: str) -> str:
    host = urlsplit(site if "://" in site else "https://" + site).hostname or ""
    return host.lower().removeprefix("www.")


def on_site(url: str, site: str) -> bool:
    host = (urlsplit(url).hostname or "").lower()
    dom = site_domain(site)
    base = fetcher.domain_of(f"https://{dom}/")
    return bool(dom) and (host == dom or host.endswith("." + dom) or host == base or host.endswith("." + base))


def check_url(url: str, site: str) -> tuple[bool, str]:
    if not url.lower().startswith(("http://", "https://")):
        if url.startswith(("about:", "chrome:", "edge:", "data:")) and "blank" in url:
            return True, ""
        return False, "Only web pages (http/https) can be opened."
    try:
        netguard.check_url(url)
    except netguard.UnsafeURL as e:
        return False, str(e)
    if not on_site(url, site):
        return False, f"{urlsplit(url).hostname} is not the planned site ({site_domain(site)})."
    domain = fetcher.domain_of(url)
    settings = fetcher.site_settings(domain)
    if settings.get("blocked") or fetcher.is_opted_out(urlsplit(url).hostname or ""):
        return False, f"{domain} is on your never-scrape list."
    if fetcher.cooldown_left(domain) > 0:
        return False, f"{domain} is paused (cooling down) after it limited automated visits."
    if config.RESPECT_ROBOTS and not fetcher.robots_allows(url):
        return False, "The site's robots.txt does not allow this page."
    return True, ""


def _element_words(el: dict) -> str:
    attrs = el.get("attributes") or {}
    return " ".join(str(x) for x in [el.get("text", ""), attrs.get("aria-label", ""), attrs.get("title", ""),
                                     attrs.get("value", ""), attrs.get("alt", "")] if x)


def check_click(el: dict) -> tuple[bool, str]:
    words = _element_words(el)
    m = RISKY_CLICK.search(words)
    if m and DISMISS.search(words) and all(ACCOUNT_OFFER.fullmatch(x.group(0).strip())
                                           for x in RISKY_CLICK.finditer(words)):
        m = None                          # the close button of a sign-in / newsletter pop-up
    if m:
        return False, (f"Clicking “{words[:60]}” could act on the site ({m.group(0).strip()}); the agent only "
                       "searches, filters and reads.")
    attrs = el.get("attributes") or {}
    href = attrs.get("href", "")
    if href.lower().startswith(("mailto:", "tel:", "sms:", "whatsapp:")):
        return False, "Contact links (phone, email, chat) are never opened."
    if (attrs.get("type") or "").lower() in ("file",):
        return False, "File uploads are not allowed."
    return True, ""


def check_input(el: dict, text: str = "") -> tuple[bool, str]:
    attrs = el.get("attributes") or {}
    kind = (attrs.get("type") or "").lower()
    if kind in BLOCKED_INPUT_TYPES:
        return False, f"The agent never types into {kind} fields."
    names = " ".join(str(attrs.get(k, "")) for k in ("name", "id", "autocomplete", "placeholder", "aria-label"))
    m = PERSONAL_FIELD.search(names)
    if m:
        return False, f"This looks like a personal-data field ({m.group(0)}); the agent only types search and filter values."
    if len(text) > 200:
        return False, "Search and filter values are short; this text is too long."
    return True, ""
