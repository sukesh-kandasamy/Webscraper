"""Refuse URLs that point at this computer or the local network (SSRF guard).

The scraper fetches whatever URL it is given. Without this check, a pasted link, a redirect or a
recipe could make it read http://localhost:8000/api/..., your router at http://192.168.1.1/, or a
cloud metadata service at http://169.254.169.254/. Every request (and every redirect hop, in plain
HTTP and in the browser) goes through check_url().
"""
import ipaddress
import socket
import threading
import time
from urllib.parse import urlsplit

from . import config


class UnsafeURL(ValueError):
    """The URL points somewhere the scraper must not go."""


_cache: dict[str, tuple[float, list[str]]] = {}
_cache_lock = threading.Lock()
CACHE_TTL_S = 300


def _resolve(host: str) -> list[str]:
    now = time.time()
    with _cache_lock:
        hit = _cache.get(host)
        if hit and now - hit[0] < CACHE_TTL_S:
            return hit[1]
    try:
        infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    except socket.gaierror as e:
        raise UnsafeURL(f"Cannot resolve host {host!r}: {e}") from e
    addrs = sorted({i[4][0] for i in infos})
    with _cache_lock:
        _cache[host] = (now, addrs)
    return addrs


def _is_forbidden_ip(addr: str) -> bool:
    ip = ipaddress.ip_address(addr.split("%", 1)[0])
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast
            or ip.is_reserved or ip.is_unspecified or not ip.is_global)


def check_url(url: str) -> None:
    """Raise UnsafeURL unless url is http(s) to a public internet host (or explicitly allowed)."""
    parts = urlsplit(url)
    if parts.scheme.lower() not in ("http", "https"):
        raise UnsafeURL(f"Only http(s) URLs are allowed, not {parts.scheme or 'no scheme'!r}")
    host = (parts.hostname or "").lower().rstrip(".")
    if not host:
        raise UnsafeURL("URL has no host")
    if parts.username or parts.password:
        raise UnsafeURL("URLs with embedded usernames or passwords are not allowed")
    if host in config.NET_ALLOW_HOSTS:
        return
    try:
        literal = ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        literal = None
    addrs = [str(literal)] if literal else _resolve(host)
    bad = [a for a in addrs if _is_forbidden_ip(a)]
    if bad:
        raise UnsafeURL(f"{host} points to a local or private network address ({bad[0]}); "
                        "the scraper only fetches public websites. Add the host to "
                        "SCRAPER_ALLOW_HOSTS in .env if this is intentional.")


def is_safe(url: str) -> bool:
    try:
        check_url(url)
        return True
    except UnsafeURL:
        return False
