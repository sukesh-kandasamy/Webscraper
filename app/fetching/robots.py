"""robots.txt checks with support for `*` and `$` wildcards.

The standard library's urllib.robotparser ignores wildcards, which would miss rules
like TradeIndia's `Disallow: */search.html*`.
"""
import re
from urllib.parse import urlsplit


def parse(text: str, user_agent: str = "*") -> list[tuple[bool, str]]:
    """Return (allow, pattern) rules for the group that applies to user_agent."""
    groups: list[tuple[list[str], list[tuple[bool, str]]]] = []
    agents: list[str] = []
    rules: list[tuple[bool, str]] = []
    last_was_agent = False
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if ":" not in line:
            continue
        key, value = (p.strip() for p in line.split(":", 1))
        key = key.lower()
        if key == "user-agent":
            if not last_was_agent and (agents or rules):
                groups.append((agents, rules))
                agents, rules = [], []
            agents.append(value.lower())
            last_was_agent = True
        elif key in ("allow", "disallow"):
            last_was_agent = False
            if value:
                rules.append((key == "allow", value))
    if agents or rules:
        groups.append((agents, rules))

    ua = user_agent.lower()
    specific = [r for a, r in groups if any(x != "*" and x in ua for x in a)]
    if specific:
        return [rule for rs in specific for rule in rs]
    return [rule for a, rs in groups if "*" in a for rule in rs]


def _to_regex(pattern: str) -> re.Pattern:
    anchored = pattern.endswith("$")
    body = re.escape(pattern.rstrip("$")).replace(r"\*", ".*")
    return re.compile("^" + body + ("$" if anchored else ""))


def is_allowed(rules: list[tuple[bool, str]], url: str) -> bool:
    parts = urlsplit(url)
    path = (parts.path or "/") + (f"?{parts.query}" if parts.query else "")
    best_len, allowed = -1, True
    for allow, pattern in rules:
        if _to_regex(pattern).match(path):
            # Longest match wins; on a tie, Allow wins (Google's behaviour).
            if len(pattern) > best_len or (len(pattern) == best_len and allow):
                best_len, allowed = len(pattern), allow
    return allowed
