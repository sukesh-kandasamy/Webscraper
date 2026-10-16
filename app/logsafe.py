"""Keep secrets out of logs, error messages and anything shown in the UI."""
import logging
import re

_PATTERNS = [
    (re.compile(r"gsk_[A-Za-z0-9]{16,}"), "gsk_[REDACTED]"),
    (re.compile(r"sk-ant-[A-Za-z0-9_\-]{16,}"), "sk-ant-[REDACTED]"),
    (re.compile(r"\b(sk|pk|rk)_(live|test)_[A-Za-z0-9]{16,}"), r"\1_\2_[REDACTED]"),
    (re.compile(r"(?i)\b(authorization|cookie|set-cookie|x-api-key|api[_-]?key|proxy-authorization)"
                r"(\"?\s*[:=]\s*\"?)([^\s\"',;]+[^\n\"']*)"), r"\1\2[REDACTED]"),
    (re.compile(r"(?i)\b(bearer)\s+[A-Za-z0-9._\-]{12,}"), r"\1 [REDACTED]"),
    (re.compile(r"(?i)([?&](?:token|key|api_key|access_token|session|sid)=)[^&\s]+"), r"\1[REDACTED]"),
]


def redact(text) -> str:
    s = str(text)
    for pattern, repl in _PATTERNS:
        s = pattern.sub(repl, s)
    return s


class RedactingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        try:
            msg = record.getMessage()
        except Exception:
            return True
        clean = redact(msg)
        if clean != msg:
            record.msg, record.args = clean, ()
        return True


def install() -> None:
    """Attach the filter to the root and uvicorn loggers (idempotent)."""
    for name in ("", "uvicorn", "uvicorn.error", "uvicorn.access", "httpx", "app"):
        logger = logging.getLogger(name)
        if not any(isinstance(f, RedactingFilter) for f in logger.filters):
            logger.addFilter(RedactingFilter())
        for handler in logger.handlers:
            if not any(isinstance(f, RedactingFilter) for f in handler.filters):
                handler.addFilter(RedactingFilter())
