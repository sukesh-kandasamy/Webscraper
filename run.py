"""Start the Universal Scraper server.

    python run.py                 # http://localhost:8000, opens your browser
    python run.py --port 8080     # another port
    python run.py --reload        # restart automatically when code changes
    python run.py --no-browser    # don't open the browser

Works with any Python: if it isn't the project's .venv, it switches to it.
"""
import argparse
import os
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent
VENV_PYTHON = ROOT / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def ensure_venv() -> None:
    """Re-run this script with the project's virtualenv Python if we're not in it."""
    if not VENV_PYTHON.exists() or Path(sys.prefix).resolve() == (ROOT / ".venv").resolve():
        return
    sys.exit(subprocess.call([str(VENV_PYTHON), str(Path(__file__).resolve()), *sys.argv[1:]]))


def open_browser_when_ready(url: str) -> None:
    for _ in range(60):
        try:
            urllib.request.urlopen(url + "/api/status", timeout=1)
            webbrowser.open(url)
            return
        except OSError:
            time.sleep(0.5)


def main() -> None:
    ensure_venv()
    parser = argparse.ArgumentParser(description="Start the Universal Scraper server")
    parser.add_argument("--host", default="127.0.0.1", help="default 127.0.0.1 (this computer only)")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--reload", action="store_true", help="restart when code changes")
    parser.add_argument("--no-browser", action="store_true", help="don't open the browser")
    parser.add_argument("--i-understand-this-exposes-the-app", dest="expose", action="store_true",
                        help="required with a --host other than 127.0.0.1 / localhost")
    args = parser.parse_args()

    if args.host not in ("127.0.0.1", "localhost", "::1") and not args.expose:
        sys.exit(f"Refusing to listen on {args.host}: anyone on your network could use the app, "
                 "your login sessions and your scraped data. The app is built for local use only.\n"
                 "If you really mean it, add --i-understand-this-exposes-the-app and set "
                 "SCRAPER_ALLOWED_HOSTS in .env.")
    if args.expose:
        print("  WARNING: the app is reachable from other computers. Protect it (firewall / VPN).")

    try:
        import uvicorn
    except ImportError:
        sys.exit("Dependencies missing. Run:  python -m venv .venv  then  "
                 ".venv\\Scripts\\pip install -r requirements.txt")

    url = f"http://{'localhost' if args.host in ('127.0.0.1', '0.0.0.0') else args.host}:{args.port}"
    print(f"\n  Universal Scraper  ->  {url}\n  Press Ctrl+C to stop.\n")
    if not args.no_browser:
        threading.Thread(target=open_browser_when_ready, args=(url,), daemon=True).start()

    os.chdir(ROOT)
    uvicorn.run("app.main:app", host=args.host, port=args.port, reload=args.reload)


if __name__ == "__main__":
    main()
