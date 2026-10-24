"""Compile the UI stylesheet:  frontend/src/app.css  ->  frontend/public/app.css  (Tailwind 3.4, minified).

    python scripts/build_css.py            # build once
    python scripts/build_css.py --watch    # rebuild on every change while editing the UI

Needs `pip install pytailwindcss` (it downloads the standalone Tailwind CLI on first use).
The compiled frontend/public/app.css is committed, so running the app never needs this step.
"""
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
UI = ROOT / "frontend"
os.environ.setdefault("TAILWINDCSS_VERSION", "v3.4.17")

exe = Path(sys.executable).parent / ("tailwindcss.exe" if os.name == "nt" else "tailwindcss")
cmd = [str(exe) if exe.exists() else "tailwindcss", "-c", str(UI / "tailwind.config.js"),
       "-i", str(UI / "src" / "app.css"), "-o", str(UI / "public" / "app.css"), "--minify"]
if "--watch" in sys.argv:
    cmd.append("--watch")
sys.exit(subprocess.call(cmd, cwd=UI))      # content paths in the config are relative to frontend/
