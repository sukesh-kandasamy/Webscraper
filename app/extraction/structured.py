"""Data that pages carry for machines: schema.org JSON-LD and embedded app state.

Many shops, job boards and property sites put a clean copy of their data in the page:

    <script type="application/ld+json">{"@type": "Product", "name": ..., "offers": {"price": ...}}</script>
    <script id="__NEXT_DATA__" type="application/json">{"props": {"pageProps": {"items": [...]}}}</script>
    <script>window.__INITIAL_STATE__ = {...};</script>

Reading it is cleaner than CSS when it exists: prices are numbers, dates are ISO, and fields that the
page hides behind a click are often there. Objects are flattened to dot paths ("offers.price",
"hiringOrganization.name") so a recipe field can point at a value with a simple path.
"""
import json
import re

from selectolax.lexbor import LexborHTMLParser

MAX_SCRIPT_CHARS = 3_000_000
MAX_DEPTH = 6
MIN_LIST_ITEMS = 3
# schema.org types that describe one listing / record
ITEM_TYPES = {"product", "offer", "jobposting", "realestatelisting", "residence", "apartment", "house",
              "singlefamilyresidence", "accommodation", "event", "newsarticle", "article", "blogposting",
              "localbusiness", "organization", "book", "movie", "recipe", "vehicle", "car", "course",
              "hotel", "restaurant", "place", "softwareapplication", "service", "review"}
_STATE_ASSIGN = re.compile(r"window\.(__[A-Z0-9_]+__|__[a-zA-Z]+State__?)\s*=\s*", re.S)


def _types(obj: dict) -> set[str]:
    t = obj.get("@type")
    vals = t if isinstance(t, list) else [t]
    return {str(v).lower() for v in vals if v}


def _load(text: str):
    text = (text or "").strip().rstrip(";")
    if not text or len(text) > MAX_SCRIPT_CHARS:
        return None
    try:
        return json.loads(text)
    except ValueError:
        return None


def jsonld_objects(html: str | LexborHTMLParser) -> list[dict]:
    """Every JSON-LD object on the page (graphs and arrays unpacked)."""
    tree = html if isinstance(html, LexborHTMLParser) else LexborHTMLParser(html)
    out: list[dict] = []

    def add(obj):
        if isinstance(obj, list):
            for o in obj:
                add(o)
        elif isinstance(obj, dict):
            if "@graph" in obj:
                add(obj["@graph"])
            else:
                out.append(obj)

    for node in tree.css('script[type="application/ld+json"]'):
        add(_load(node.text(deep=True)))
    return out


def app_state(html: str | LexborHTMLParser) -> list[tuple[str, object]]:
    """Embedded app state: (name, data) for __NEXT_DATA__, __NUXT_DATA__ and window.__X__ = {...}."""
    tree = html if isinstance(html, LexborHTMLParser) else LexborHTMLParser(html)
    out = []
    for node in tree.css('script[type="application/json"]'):
        data = _load(node.text(deep=True))
        if isinstance(data, (dict, list)):
            out.append(("#" + (node.attributes.get("id") or "json"), data))
    for node in tree.css("script"):
        text = node.text(deep=True) or ""
        if "window.__" not in text or len(text) > MAX_SCRIPT_CHARS:
            continue
        m = _STATE_ASSIGN.search(text)
        if not m:
            continue
        data = _balanced_json(text, m.end())
        if isinstance(data, (dict, list)):
            out.append((m.group(1), data))
    return out


def _balanced_json(text: str, start: int):
    """Parse the JSON object or array that starts at text[start] (ignores what follows)."""
    while start < len(text) and text[start] in " \t\r\n":
        start += 1
    if start >= len(text) or text[start] not in "{[":
        return None
    try:
        data, _ = json.JSONDecoder().raw_decode(text, start)
        return data
    except ValueError:
        return None


# ---- flattening ----------------------------------------------------------------------

def _scalar(v) -> str | None:
    if v is None or isinstance(v, (dict, list)):
        return None
    if isinstance(v, bool):
        return "yes" if v else "no"
    s = str(v).strip()
    return s if s and len(s) <= 2000 else None


def flatten(obj, prefix: str = "", depth: int = 0, out: dict | None = None) -> dict[str, str]:
    """{"offers": {"price": 450}} -> {"offers.price": "450"}. Lists of scalars are joined with ", ";
    lists of objects use their first element (e.g. the first image or offer)."""
    out = {} if out is None else out
    if depth > MAX_DEPTH:
        return out
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k in ("@context", "@id") or not isinstance(k, str):
                continue
            key = f"{prefix}.{k}" if prefix else k
            if isinstance(v, (dict, list)):
                flatten(v, key, depth + 1, out)
            else:
                s = _scalar(v)
                if s is not None:
                    out[key] = s
    elif isinstance(obj, list) and obj:
        scalars = [s for s in (_scalar(v) for v in obj) if s is not None]
        if scalars and len(scalars) == len(obj):
            out[prefix] = ", ".join(scalars[:20])
        elif isinstance(obj[0], (dict, list)):
            flatten(obj[0], prefix, depth + 1, out)
    return out


def main_entity(html: str | LexborHTMLParser) -> dict[str, str]:
    """Flattened JSON-LD object that describes the page's item (product, job, property...): the
    first object of a listing type, else the largest object. Used for item (detail) pages."""
    objs = jsonld_objects(html)
    typed = [o for o in objs if _types(o) & ITEM_TYPES]
    pick = typed or objs
    if not pick:
        return {}
    best = max(pick, key=lambda o: len(flatten(o))) if not typed else typed[0]
    flat = flatten(best)
    if "@type" in best and "@type" not in flat:
        flat["@type"] = ", ".join(sorted(_types(best)))
    return flat


# ---- lists of items -------------------------------------------------------------------

def _record_like(lst: list) -> bool:
    if len(lst) < MIN_LIST_ITEMS or not all(isinstance(x, dict) for x in lst[:20]):
        return False
    keys = [set(x) for x in lst[:20]]
    shared = set.intersection(*keys)
    return len(shared) >= 2


def _walk_lists(obj, path: str, depth: int, found: list):
    if depth > MAX_DEPTH + 2:
        return
    if isinstance(obj, list):
        if _record_like(obj):
            found.append((path, obj))
        for i, v in enumerate(obj[:3]):
            if isinstance(v, (dict, list)):
                _walk_lists(v, f"{path}[{i}]", depth + 1, found)
    elif isinstance(obj, dict):
        for k, v in obj.items():
            if isinstance(v, (dict, list)) and isinstance(k, str):
                _walk_lists(v, f"{path}.{k}" if path else k, depth + 1, found)


def _unwrap(item: dict) -> dict:
    """ItemList elements wrap the record: {"@type": "ListItem", "item": {...}}."""
    if isinstance(item.get("item"), dict) and "listitem" in _types(item):
        return item["item"]
    return item


def find_lists(html: str) -> list[dict]:
    """Lists of record-like objects in JSON-LD and app state, best first:
    [{"path", "count", "keys": {key path: example}, "items": [flat dicts]}]."""
    tree = LexborHTMLParser(html)
    sources = [("ld", objs) for objs in [jsonld_objects(tree)] if objs] + app_state(tree)
    found = []
    for name, data in sources:
        lists: list = []
        if name == "ld":
            # Several top-level Product / JobPosting objects form a list of their own.
            items = [o for o in data if _types(o) & ITEM_TYPES]
            if len(items) >= MIN_LIST_ITEMS:
                lists.append(("", items))
            for i, o in enumerate(data):
                _walk_lists(o, f"[{i}]", 0, lists)
        else:
            _walk_lists(data, "", 0, lists)
        for path, lst in lists:
            flat = [flatten(_unwrap(x)) for x in lst]
            keys: dict[str, str] = {}
            for f in flat[:5]:
                for k, v in f.items():
                    keys.setdefault(k, v)
            # Score: many items with many useful fields (an item list with only position + url is weak).
            score = len(flat) * min(len(keys), 25)
            found.append({"path": f"{name}:{path}", "count": len(flat), "keys": keys, "items": flat,
                          "score": score})
    found.sort(key=lambda d: -d["score"])
    out, seen = [], set()
    for f in found:
        sig = (f["count"], tuple(sorted(f["keys"]))[:10])
        if sig in seen:
            continue
        seen.add(sig)
        out.append(f)
    return out[:3]


def _get_path(data, path: str):
    for part in re.findall(r"[^.\[\]]+|\[\d+\]", path):
        if part.startswith("["):
            idx = int(part[1:-1])
            if not isinstance(data, list) or idx >= len(data):
                return None
            data = data[idx]
        else:
            if not isinstance(data, dict) or part not in data:
                return None
            data = data[part]
    return data


def items_at(html: str, list_path: str) -> list[dict[str, str]]:
    """The flattened items of the list found by find_lists() at `list_path` ("ld:..." or "#id:..." or
    "window_name:...")."""
    source, _, path = list_path.partition(":")
    tree = LexborHTMLParser(html)
    if source == "ld":
        objs = jsonld_objects(tree)
        if path == "":
            lst = [o for o in objs if _types(o) & ITEM_TYPES]
        else:
            lst = _get_path(objs, path)
    else:
        data = next((d for name, d in app_state(tree) if name == source), None)
        lst = _get_path(data, path) if data is not None and path else data
    if not isinstance(lst, list):
        return []
    return [flatten(_unwrap(x)) for x in lst if isinstance(x, dict)]
