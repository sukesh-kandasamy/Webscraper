import json
from pathlib import Path

import pytest

from app import extractor, recipe_builder, robots

ROOT = Path(__file__).resolve().parent.parent
CASES = [
    ("indiamart_pvc-pipes", "dir.indiamart.com", "https://dir.indiamart.com/impcat/pvc-pipes.html"),
    ("tradeindia_upvc-pipe-fittings", "www.tradeindia.com",
     "https://www.tradeindia.com/manufacturers/upvc-pipe-fittings.html"),
]


def load(fixture, domain):
    html = (ROOT / "tests" / "fixtures" / f"{fixture}.html").read_text(encoding="utf-8")
    recipe = extractor.normalize_recipe(
        json.loads((ROOT / "app" / "recipes" / f"{domain}.json").read_text(encoding="utf-8")))
    return html, recipe


@pytest.mark.parametrize("fixture,domain,url", CASES)
def test_builtin_recipes_extract_listings(fixture, domain, url):
    html, recipe = load(fixture, domain)
    rows = extractor.extract(html, recipe, url)
    assert len(rows) >= 20
    report = extractor.field_report(rows, recipe)
    for field in ("name", "seller", "city", "url"):
        assert report[field] >= 0.9, (field, report)
    assert all(r["url"].startswith("https://") for r in rows)


@pytest.mark.parametrize("fixture,domain,url", CASES)
def test_candidate_detection_finds_product_cards(fixture, domain, url):
    html, _ = load(fixture, domain)
    best = recipe_builder.find_candidates(html)[0]
    assert best["count"] >= 10
    assert "proddetail" in best["samples"][0] or "/products/" in best["samples"][0]


def test_regex_and_kv_fields():
    html = """<div class="c"><p class="p">Price: 1,250 INR/Kg</p>
      <dl><dt>MOQ</dt><dd>10 Kg</dd><dt>Color</dt><dd>Red</dd></dl></div>"""
    recipe = extractor.normalize_recipe({"item_selector": "div.c", "fields": [
        {"name": "price", "selector": ".p", "regex": r"([\d,]+)\s*INR"},
        {"name": "unit", "selector": ".p", "regex": r"/\s*(\w+)$"},
        {"name": "moq", "kind": "kv", "label_selector": "dt", "value_selector": "dd", "label": "moq"},
        {"name": "specs", "kind": "kv", "label_selector": "dt", "value_selector": "dd"},
    ]})
    assert extractor.extract(html, recipe, "https://x.test/") == [
        {"price": "1,250", "unit": "Kg", "moq": "10 Kg", "specs": "MOQ: 10 Kg; Color: Red"}]


def test_bad_selector_is_rejected():
    with pytest.raises(extractor.RecipeError):
        extractor.normalize_recipe({"item_selector": "div[", "fields": [{"name": "a"}]})


def test_url_param_pagination():
    recipe = extractor.normalize_recipe({"item_selector": "li", "fields": [{"name": "a"}],
                                         "pagination": {"type": "url_param", "param": "pg"}})
    assert extractor.next_page_url("", recipe, "https://x.test/list?q=1", 0) == "https://x.test/list?q=1&pg=2"


def test_robots_wildcards():
    rules = robots.parse("""
User-agent: ClaudeBot
Disallow: /
User-agent: *
Disallow: /search.mp
Disallow: */search.html*
Disallow: *?page=*
Allow: /search.mp/ok$
""", "Mozilla/5.0 Chrome")
    assert not robots.is_allowed(rules, "https://a.test/search.mp?ss=pvc")
    assert not robots.is_allowed(rules, "https://a.test/x/search.html?k=1")
    assert not robots.is_allowed(rules, "https://a.test/cat.html?page=2")
    assert robots.is_allowed(rules, "https://a.test/impcat/pvc-pipes.html")
    assert robots.is_allowed(rules, "https://a.test/search.mp/ok")


def test_mojibake_repair():
    assert extractor.clean_text("Up to 60Â°C") == "Up to 60°C"
    assert extractor.clean_text("₹ 450 / Piece") == "₹ 450 / Piece"


def test_over_escaped_regex_is_repaired():
    recipe = extractor.normalize_recipe({"item_selector": "p", "fields": [
        {"name": "price", "regex": "\\$([\\d.,]+)"}]})
    assert extractor.extract("<p>$1,099.50</p>", recipe, "https://x.test/") == [{"price": "1,099.50"}]


def test_prompt_samples_fit_budget():
    cands = [{"samples": ["a" * 9000, "b" * 9000]}, {"samples": ["c" * 3000, "d" * 3000]},
             {"samples": ["e" * 2000]}]
    chosen = recipe_builder._fit_samples(cands, 11000)
    assert sum(len(s) for group in chosen for s in group) <= 11000 + 50
    assert chosen[0] and chosen[1]


class _FakeRateLimit(Exception):
    status_code = 429

    def __init__(self, msg, headers=None):
        super().__init__(msg)
        self.response = type("R", (), {"headers": headers or {}})()


def test_ai_retry_waits_for_server_hint(monkeypatch):
    sleeps = []
    monkeypatch.setattr(recipe_builder.time, "sleep", sleeps.append)
    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] < 3:
            raise _FakeRateLimit("Rate limit reached. Please try again in 1.5s.")
        return "ok"

    assert recipe_builder._with_retries(flaky) == "ok"
    assert calls["n"] == 3 and sleeps == [2.0, 2.0]


def test_ai_retry_gives_up_on_long_waits(monkeypatch):
    monkeypatch.setattr(recipe_builder.time, "sleep", lambda s: None)

    def always():
        raise _FakeRateLimit("slow down", headers={"retry-after": "120"})

    with pytest.raises(recipe_builder.RecipeBuildError, match="rate-limited"):
        recipe_builder._with_retries(always)


def test_ai_too_large_is_not_retried():
    calls = {"n": 0}

    def big():
        calls["n"] += 1
        e = _FakeRateLimit("Request too large for model"); e.status_code = 413
        raise e

    with pytest.raises(recipe_builder.RecipeBuildError, match="too large"):
        recipe_builder._with_retries(big)
    assert calls["n"] == 1


def test_pagination_hints_include_load_more_buttons():
    html = """<a class="btn ecomerce-items-scroll-more">More</a>
              <button id="lm" class="x">Load more products</button>
              <a href="/about">Read more about our company history and values</a>"""
    hints = recipe_builder._pagination_hints(html)
    assert any('ecomerce-items-scroll-more' in h and '>More<' in h for h in hints)
    assert any('id="lm"' in h for h in hints)
    assert not any("company history" in h for h in hints)


def test_short_url_keeps_paging_drops_tracking():
    href = ("/s?k=Shoes&page=2&xpid=x9W1&_encoding=UTF8&content-id=amzn1.sym.acbd6832&crid=1W98"
            "&pd_rd_r=6fa1a246-65a3-4488-8f2d&qid=1792736417&ref=sr_pg_2")
    assert recipe_builder.short_url(href) == "/s?k=Shoes&page=2&…"
    assert recipe_builder.short_url("/catalogue/page-2.html") == "/catalogue/page-2.html"


def test_pagination_hints_are_deduplicated():
    links = "".join(f'<a class="pg" href="/list?page={n}&track=abcdef">{n}</a>' for n in range(2, 20))
    hints = recipe_builder._pagination_hints(links + '<a class="nx" href="/list?page=2">Next</a>')
    assert len(hints) == 2 and any(">Next<" in h for h in hints)


def test_prompt_fits_total_budget():
    html = (ROOT / "tests" / "fixtures" / "tradeindia_upvc-pipe-fittings.html").read_text(encoding="utf-8")
    cands = recipe_builder.find_candidates(html)
    for budget in (6000, 11000):
        prompt = recipe_builder._make_prompt(cands, recipe_builder._pagination_hints(html),
                                             "https://www.tradeindia.com/x.html", ["name", "price"],
                                             False, False, budget)
        assert len(prompt) <= budget + 50


def test_too_large_prompt_is_shrunk_and_retried(monkeypatch):
    html = (ROOT / "tests" / "fixtures" / "indiamart_pvc-pipes.html").read_text(encoding="utf-8")
    recipe = json.loads((ROOT / "app" / "recipes" / "dir.indiamart.com.json").read_text(encoding="utf-8"))
    sizes = []

    def fake_ask(messages, fields, auto=False):
        sizes.append(len(messages[0]["content"]))
        if len(sizes) == 1:
            raise recipe_builder.PromptTooLarge("too large", limit=8000, requested=12000)
        return json.dumps(recipe)

    monkeypatch.setattr(recipe_builder, "_ask_model", fake_ask)
    monkeypatch.setattr(recipe_builder.config, "ai_enabled", lambda: True)
    monkeypatch.setattr(recipe_builder.config, "ai_prompt_budget", lambda: 11000)   # Groq-sized, whatever .env says
    out = recipe_builder.build_recipe(html, "https://dir.indiamart.com/impcat/pvc-pipes.html", ["name", "price"])
    assert len(sizes) == 2 and sizes[1] < sizes[0] * 0.7
    assert out["rows"] and "trimmed" in out["notes"]


def test_outline_merges_cards_and_skips_tracking_attributes():
    card = ('<div class="card" data-uuid="17f54919-6c87-465b-8002-dd11e3ae40cb" data-asin="B07CRG94G3">'
            + '<div class="wrap">' * 30 + '<h2 class="title"><a href="/dp/B07?ref=x&crid=y">Drive {n}</a></h2><p class="desc">Portable USB 3.0 external hard drive for backups</p>{price}'
            + '</div>' * 30 + '</div>')
    html = "<div class='list'>" + "".join(
        card.format(n=n, price='<span class="a-price"><span class="off">INR 8,420</span></span>' if n == 2 else
                    '<a class="opt">See options</a>') for n in range(1, 6)) + "</div>"
    cands = recipe_builder.find_candidates(html)
    text = recipe_builder.render_outline(cands[0]["outline"], lambda s: s, 3000)
    assert 'h2.title a [href="/dp/B07?ref=x&…"] → "Drive 1"' in text
    assert 'span.a-price span.off → "INR 8,420"' in text        # only card 2 has a price
    assert "data-uuid" not in text and 'data-asin="B07CRG94G3"' in text
    assert "data-uuid" not in cands[0]["samples"][0]


def test_auto_schema_allows_ai_named_columns():
    fixed = recipe_builder._schema(["name", "price"])
    free = recipe_builder._schema(["name"], auto=True)
    item = lambda s: s["properties"]["fields"]["items"]["properties"]["name"]
    assert item(fixed)["enum"] == ["name", "price"] and "enum" not in item(free)
    assert "page_type" in free["required"]


def test_field_names_become_snake_case():
    assert recipe_builder.clean_field_names(["Job Title", "salary (INR)", "job title", "3BHK"]) == \
        ["job_title", "salary_inr", "f_3bhk"]


def test_auto_columns_drop_empty_and_duplicate_but_keep_required():
    html = "<ul>" + "".join(
        f"<li class='c'><a href='/j/{i}'>Job {i}</a><span class='co'>Acme</span><span class='co2'>Acme</span>"
        f"<i class='rare'>{'x' if i == 0 else ''}</i></li>" for i in range(10)) + "</ul>"
    raw = {"item_selector": "li.c", "pagination": {"type": "none"}, "fields": [
        {"name": "Title", "selector": "a"}, {"name": "company", "selector": "span.co"},
        {"name": "company_copy", "selector": "span.co2"}, {"name": "rare", "selector": "i.rare"},
        {"name": "salary", "selector": "b.none"}]}
    recipe, dropped = recipe_builder._tidy_auto_fields(raw, html, "https://x.test/", ["salary"])
    names = [f["name"] for f in recipe["fields"]]
    assert names == ["title", "company", "salary"]           # salary kept: you asked for it
    assert set(dropped) == {"company_copy", "rare"}


def test_table_cells_get_column_positions_in_outline():
    rows = "".join(f"<tr><td><a href='/s/{s}'>{s}</a></td><td>Company {s} Incorporated</td><td>{p}</td></tr>"
                   for s, p in [("AAA", "10.5"), ("BBB", "20.1"), ("CCC", "30.2"), ("DDD", "40.9"), ("EEE", "5.0")])
    cands = recipe_builder.find_candidates(f"<table><tbody>{rows}</tbody></table>")
    text = recipe_builder.render_outline(cands[0]["outline"], lambda s: s, 2000)
    assert 'td:nth-child(1) a [href="/s/AAA"] → "AAA"' in text
    assert 'td:nth-child(3) → "10.5"' in text


def test_schema_validation_glitch_is_retried(monkeypatch):
    monkeypatch.setattr(recipe_builder.time, "sleep", lambda s: None)
    calls = {"n": 0}

    class Glitch(Exception):
        status_code = 400

    def flaky():
        calls["n"] += 1
        if calls["n"] == 1:
            raise Glitch("Error code: 400 - {'code': 'json_validate_failed'}")
        return "ok"

    assert recipe_builder._with_retries(flaky) == "ok" and calls["n"] == 2


def test_numeric_columns_without_digits_are_flagged():
    recipe = {"fields": [{"name": "price"}, {"name": "rating"}, {"name": "salary"}]}
    rows = [{"price": "£", "rating": "Three", "salary": "₹ 12 LPA"}, {"price": "£", "rating": "One", "salary": "Not disclosed"}]
    assert recipe_builder.non_numeric_fields(rows, recipe) == ["price"]


def test_ai_is_asked_again_when_price_has_no_digits(monkeypatch):
    html = "<ul>" + "".join(f"<li class='c'><b>Book number {i} with a long enough title</b><span class='p'>£{i}1.50</span></li>" for i in range(4)) + "</ul>"
    wrong = {"item_selector": "li.c", "page_type": "ecommerce", "notes": "", "pagination": {"type": "none"},
             "fields": [{"name": "name", "selector": "b"}, {"name": "price", "selector": "span.p", "regex": "(£)"}]}
    right = {**wrong, "fields": [{"name": "name", "selector": "b"}, {"name": "price", "selector": "span.p", "regex": r"([\d.]+)"}]}
    prompts = []

    def fake_ask(messages, fields, auto=False):
        prompts.append(messages[0]["content"])
        return json.dumps(wrong if len(prompts) == 1 else right)

    monkeypatch.setattr(recipe_builder, "_ask_model", fake_ask)
    monkeypatch.setattr(recipe_builder.config, "ai_enabled", lambda: True)
    out = recipe_builder.build_recipe(html, "https://x.test/", ["name", "price"])
    assert len(prompts) == 2 and "should contain numbers" in prompts[1]
    assert out["rows"][0]["price"] == "01.50"


def test_short_table_rows_are_candidates():
    """Data tables of short values (film, nominations, awards) are records, not menus."""
    rows = "".join(f"<tr class='film'><td class='t'>Film {i}</td><td>{i}</td><td>{i % 3}</td></tr>" for i in range(12))
    html = ("<nav><a href='/2015'>2015</a><a href='/2014'>2014</a><a href='/2013'>2013</a><a href='/2012'>2012</a></nav>"
            f"<table><tbody>{rows}</tbody></table>")
    assert recipe_builder.find_candidates(html)[0]["selector_hint"].endswith("tr.film")
