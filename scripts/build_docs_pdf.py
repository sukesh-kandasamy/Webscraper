"""Build the Production risks review as a PDF: frontend/public/docs/production-risks.pdf (the "Download PDF"
button on /docs/production-risks). The content is app/docs_content.py, the layout
frontend/templates/print/production_risks.html; Playwright's Chromium prints it to A4.

    .venv\\Scripts\\python scripts\\build_docs_pdf.py
"""
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from jinja2 import Environment, FileSystemLoader, select_autoescape  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402

from app import docs_content  # noqa: E402

OUT = ROOT / "frontend" / "public" / "docs" / "production-risks.pdf"
FONT = ROOT / "frontend" / "public" / "fonts" / "roboto-latin-wght-normal.woff2"
FOOTER = ('<div style="width:100%; font-family: Arial, sans-serif; font-size:7.5pt; color:#777; padding:0 16mm; '
          'display:flex; justify-content:space-between;"><span>Universal Scraper · Production Readiness Review · '
          f'{docs_content.REVIEWED}</span><span>Page <span class="pageNumber"></span> of <span class="totalPages"></span></span></div>')


def main() -> None:
    env = Environment(loader=FileSystemLoader(str(ROOT / "frontend" / "templates")), autoescape=select_autoescape())
    html = env.get_template("print/production_risks.html").render(
        **{k: getattr(docs_content, k) for k in dir(docs_content) if k.isupper()}, font_url=FONT.as_uri())
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        page_file = Path(tmp) / "production-risks.html"
        page_file.write_text(html, encoding="utf-8")
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page()
            page.goto(page_file.as_uri(), wait_until="load")
            page.evaluate("document.fonts.ready")
            page.pdf(path=str(OUT), format="A4", print_background=True, prefer_css_page_size=True,
                     display_header_footer=True, header_template="<span></span>", footer_template=FOOTER,
                     margin={"top": "18mm", "bottom": "18mm", "left": "16mm", "right": "16mm"})
            browser.close()
    print(f"wrote {OUT.relative_to(ROOT)} ({OUT.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main()
