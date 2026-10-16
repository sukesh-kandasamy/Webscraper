"""Recognise pages that are not content: CAPTCHAs, bot challenges, access-denied and login walls.

The scraper never tries to get past these. Challenges pause a job so a person can solve them in a
visible browser; access-denied stops it; a login wall asks you to log in yourself.
"""
import re
from urllib.parse import urlsplit

# (kind, pattern). Checked in order; the first match wins.
_SIGNATURES: list[tuple[str, re.Pattern]] = [
    ("captcha", re.compile(r"g-recaptcha|www\.google\.com/recaptcha|recaptcha/api\.js|grecaptcha", re.I)),
    ("captcha", re.compile(r"h-captcha|hcaptcha\.com/1/api\.js|js\.hcaptcha\.com", re.I)),
    ("challenge", re.compile(r"cf-turnstile|challenges\.cloudflare\.com/turnstile", re.I)),
    ("challenge", re.compile(r"cf-chl-|challenge-platform|cf_chl_opt|<title>\s*just a moment", re.I)),
    ("challenge", re.compile(r"captcha-delivery\.com|geo\.captcha-delivery|datadome", re.I)),
    ("challenge", re.compile(r"px-captcha|perimeterx|_pxCaptcha", re.I)),
    ("challenge", re.compile(r"arkoselabs|funcaptcha", re.I)),
    ("captcha", re.compile(r"verify (that )?you are (a )?human|are you a robot|i'?m not a robot|"
                           r"unusual traffic from your (computer )?network|complete the security check", re.I)),
    ("denied", re.compile(r"<title>\s*(access denied|403 forbidden|attention required)|"
                          r"you don'?t have permission to access .{0,80} on this server|"
                          r"request blocked|reference #\d+\.[0-9a-f]+", re.I)),
]
_PASSWORD_INPUT = re.compile(r"<input[^>]+type\s*=\s*[\"']?password", re.I)
_LOGIN_PATH = re.compile(r"/(log-?in|sign-?in|auth|account/login|users?/sign_in|sso)\b", re.I)

_TAGS = re.compile(r"<script\b.*?</script>|<style\b.*?</style>|<[^>]+>", re.I | re.S)
THIN_TEXT = 3_000   # challenge pages have little visible text; real listing pages have a lot


def visible_text_length(html: str) -> int:
    return len(re.sub(r"\s+", " ", _TAGS.sub(" ", html)).strip())


def detect(html: str, status: int = 200, url: str = "") -> str | None:
    """Return 'captcha', 'challenge', 'denied', 'login' or None for a normal page.

    Many real pages include a reCAPTCHA script for a contact form, so a signature only counts when
    the page is thin (little visible text) or came with an error status.
    """
    thin = visible_text_length(html[:400_000]) < THIN_TEXT
    if thin or status >= 400:
        for kind, pattern in _SIGNATURES:
            if pattern.search(html[:200_000]):
                return kind
    if status in (401, 403):
        return "denied"
    path = urlsplit(url).path if url else ""
    if _PASSWORD_INPUT.search(html) and (_LOGIN_PATH.search(path) or thin):
        return "login"
    return None


def describe(kind: str) -> str:
    return {
        "captcha": "a CAPTCHA",
        "challenge": "a bot-check / challenge page",
        "denied": "an access-denied page",
        "login": "a login page",
    }.get(kind, kind)
