"""Personal data: detect it, mask it, and redact page text before it is sent to an AI provider."""
import re

EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b")
# Phone numbers: 10+ digits allowing spaces, dashes, dots, brackets and a leading +country code.
PHONE_RE = re.compile(r"(?<![\w.])(?:\+?\d{1,3}[\s\-.]?)?(?:\(?\d{2,5}\)?[\s\-.]?)?\d{3,5}[\s\-.]?\d{4,5}(?![\w])")
PERSONAL_NAME_RE = re.compile(
    r"phone|mobile|\btel\b|telephone|whatsapp|e-?mail|contact|address|person|owner|"
    r"proprietor|first.?name|last.?name|full.?name|\bdob\b|birth|aadhaar|pan.?no|gstin.?holder", re.I)


# Web addresses are never masked: product IDs, timestamps and image names inside them are long
# digit runs that look like phone numbers, and a masked link no longer works.
URL_RE = re.compile(r"(?:https?://|www\.)[^\s\"'<>]+", re.I)


def _digits(s: str) -> str:
    return re.sub(r"\D", "", s)


def is_phone(s: str) -> bool:
    """Does this digit run look like a phone number (not an order id, barcode or timestamp)?"""
    raw = s.strip()
    d = _digits(raw)
    if raw.startswith("+"):
        return 10 <= len(d) <= 15                 # +91 98765 43210, +1 212 555 0187
    separated = bool(re.search(r"[\s\-.()]", raw))
    if len(d) == 10:
        return d[0] in "6789" or separated        # Indian mobile, or formatted like (212) 555-0187
    if len(d) == 11:
        return d[0] == "0"                        # 011 2345 6789, 098765 43210
    if len(d) == 12:
        return d.startswith("91")                 # 919876543210
    return False


def _mask_phone(m: re.Match) -> str:
    raw = m.group(0)
    if not is_phone(raw):
        return raw          # prices, years, quantities, ids: not a phone number
    d = _digits(raw)
    return d[:2] + "*" * (len(d) - 4) + d[-2:]


def _mask_email(m: re.Match) -> str:
    user, _, host = m.group(0).partition("@")
    return (user[:1] + "***@" + host.split(".")[0][:1] + "***." + host.rsplit(".", 1)[-1])


def _mask_plain(s: str) -> str:
    s = EMAIL_RE.sub(_mask_email, s)
    return PHONE_RE.sub(_mask_phone, s)


def mask_text(s):
    """Mask phones and emails in free text; web addresses are left untouched."""
    if not isinstance(s, str) or not s:
        return s
    out, pos = [], 0
    for m in URL_RE.finditer(s):
        out.append(_mask_plain(s[pos:m.start()]))
        out.append(m.group(0))
        pos = m.end()
    out.append(_mask_plain(s[pos:]))
    return "".join(out)


def has_personal(s) -> bool:
    if not isinstance(s, str):
        return False
    s = URL_RE.sub(" ", s)
    if EMAIL_RE.search(s):
        return True
    return any(is_phone(m.group(0)) for m in PHONE_RE.finditer(s))


def personal_fields(recipe: dict, rows: list[dict] | None = None) -> list[str]:
    """Fields that look personal by name, or whose values often contain phones / emails."""
    names = [f["name"] for f in recipe.get("fields", [])]
    flagged = [n for n in names if PERSONAL_NAME_RE.search(n)]
    if rows:
        for n in names:
            if n in flagged:
                continue
            values = [r.get(n) for r in rows if r.get(n)]
            if values and sum(1 for v in values if has_personal(v)) / len(values) >= 0.3:
                flagged.append(n)
    return flagged


def mask_rows(rows: list[dict], keep_fields: tuple[str, ...] | list[str] = ()) -> list[dict]:
    """Mask phones / emails in every value except fields the user opted in to."""
    out = []
    for r in rows:
        out.append({k: (v if k in keep_fields else mask_text(v)) for k, v in r.items()})
    return out


_LETTERS = re.compile(r"[^\W\d_]", re.UNICODE)


_TEXT_NODE = re.compile(r">([^<]+)<")
_ATTR = re.compile(r'\s(title|aria-label|alt|data-[\w-]+)="([^"]*)"')
_CONTACT_LINK = re.compile(r'(href=")(?:mailto|tel|sms|whatsapp):[^"]*(")', re.I)


def redact_html(html: str, full: bool = False) -> str:
    """Prepare HTML for an AI prompt.

    Always: phone numbers and emails in text and descriptive attributes are masked, and
    mailto: / tel: links are blanked. With full=True (pages fetched with a login session) all
    text is reduced to its shape, so the AI sees structure but not content.
    """
    text_fn = shape if full else mask_text
    out = _CONTACT_LINK.sub(r"\1#contact\2", html)
    out = _TEXT_NODE.sub(lambda m: ">" + text_fn(m.group(1)) + "<", out)
    out = _ATTR.sub(lambda m: f' {m.group(1)}="{text_fn(m.group(2))}"', out)
    return out


def shape(text: str) -> str:
    """Keep the shape of text but not its meaning: 'Price: 150 INR/Piece' -> 'xxxxx: 150 xxx/xxxxx'.
    Phones and emails are masked first. Used for pages fetched with a login session."""
    return _LETTERS.sub("x", mask_text(text))
