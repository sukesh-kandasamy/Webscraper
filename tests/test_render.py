"""Render-mode decisions in fetch_smart, with fetch and the browser replaced by fakes."""
import pytest

from app import config, db
from app.extraction import extractor
from app.fetching import fetcher

EMPTY = "<html><body><div id='app'></div><script>/* renders listings */</script></body></html>"
FULL = "<ul>" + "".join(f"<li class='c'><a href='/p{i}'>Product {i}</a> A listing card with enough "
                       f"text to look like a real record, priced at {100 + i} rupees</li>"
                       for i in range(6)) + "</ul>"
RECIPE = extractor.normalize_recipe({"item_selector": "li.c", "fields": [{"name": "name", "selector": "a"}]})


@pytest.fixture
def fakes(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "t.db")
    db.init()
    log = []

    def fetch(url, use_cache=True, retries=2):
        log.append("http")
        return fetcher.Page(url, url, 200, EMPTY if "js" in url else FULL, False)

    def fetch_rendered(url, item_selector="", load_more="", scroll=False, max_rounds=0, use_cache=True):
        log.append(f"browser rounds={max_rounds} scroll={scroll} click={load_more!r}")
        return fetcher.Page(url, url, 200, FULL, False, rendered=True, rounds=max_rounds)

    monkeypatch.setattr(fetcher, "fetch", fetch)
    monkeypatch.setattr(fetcher, "fetch_rendered", fetch_rendered)
    fetcher._settings_cache.clear()
    return log


def test_auto_uses_http_when_page_has_listings(fakes):
    page = fetcher.fetch_smart("https://x.test/plain", RECIPE)
    assert not page.rendered and fakes == ["http"]


def test_auto_falls_back_to_browser_when_page_is_empty(fakes):
    page = fetcher.fetch_smart("https://x.test/js", RECIPE)
    assert page.rendered and fakes == ["http", "browser rounds=0 scroll=False click=''"]


def test_auto_without_recipe_uses_candidate_detection(fakes):
    assert fetcher.fetch_smart("https://x.test/js").rendered
    assert not fetcher.fetch_smart("https://x.test/plain").rendered


def test_never_setting_skips_browser(fakes):
    db.save_site_settings("x.test", {"render": "never"})
    page = fetcher.fetch_smart("https://x.test/js", RECIPE)
    assert not page.rendered and fakes == ["http"]


def test_always_setting_goes_straight_to_browser(fakes):
    db.save_site_settings("x.test", {"render": "always"})
    fetcher.fetch_smart("https://x.test/plain", RECIPE)
    assert fakes == ["browser rounds=0 scroll=False click=''"]


def test_click_and_scroll_pagination_need_the_browser(fakes):
    click = extractor.normalize_recipe({**RECIPE, "pagination": {"type": "click", "selector": "button.more"}})
    scroll = extractor.normalize_recipe({**RECIPE, "pagination": {"type": "scroll"}})
    fetcher.fetch_smart("https://x.test/plain", click, max_rounds=4)
    fetcher.fetch_smart("https://x.test/plain", scroll, max_rounds=3)
    assert fakes == ["browser rounds=4 scroll=False click='button.more'",
                     "browser rounds=3 scroll=True click=''"]
    assert extractor.next_page_url(FULL, click, "https://x.test/plain", 0) is None


def test_click_pagination_requires_selector():
    with pytest.raises(extractor.RecipeError, match="load-more"):
        extractor.normalize_recipe({**RECIPE, "pagination": {"type": "click"}})
