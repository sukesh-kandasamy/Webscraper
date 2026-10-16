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
