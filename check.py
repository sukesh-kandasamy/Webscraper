"""One-command security check for the Universal Scraper.

    python check.py

Runs the tests, a secret scan of tracked files, a vulnerability audit of installed packages, and
checks that secrets and data are kept out of git and that the drive is encrypted. Prints a
pass / warn / fail report; exit code 1 if anything failed.
"""
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
VENV_PY = ROOT / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
PY = str(VENV_PY if VENV_PY.exists() else Path(sys.executable))

results: list[tuple[str, str, str]] = []   # (status, check, detail)


def record(status: str, name: str, detail: str = "") -> None:
    results.append((status, name, detail))


def run(cmd: list[str], timeout: int = 600) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=timeout)


def check_tests() -> None:
    r = run([PY, "-m", "pytest", "-q", "tests"])
    last = (r.stdout.strip().splitlines() or ["no output"])[-1]
    record("PASS" if r.returncode == 0 else "FAIL", "Test suite", last)


def check_secrets() -> None:
    r = run([PY, str(ROOT / "tools" / "secret_scan.py")])
    record("PASS" if r.returncode == 0 else "FAIL", "Secret scan of tracked files",
           r.stdout.strip().splitlines()[-1] if r.stdout.strip() else "")


def check_git_ignores() -> None:
    tracked = run(["git", "ls-files", ".env", "data"]).stdout.split()
    history = run(["git", "log", "--all", "--oneline", "--", ".env", "data"]).stdout.strip()
    if tracked:
        record("FAIL", ".env / data kept out of git", "tracked: " + ", ".join(tracked))
    elif history:
        record("FAIL", ".env / data kept out of git", "present in history; rewrite it and rotate keys")
    else:
        record("PASS", ".env / data kept out of git")
    hooks = run(["git", "config", "core.hooksPath"]).stdout.strip()
    record("PASS" if hooks == ".githooks" else "WARN", "Pre-commit secret hook",
           "enabled" if hooks == ".githooks" else "run: git config core.hooksPath .githooks")


def check_audit() -> None:
    r = run([PY, "-m", "pip_audit", "--progress-spinner", "off"], timeout=900)
    out = (r.stdout + r.stderr).strip()
    if "No module named pip_audit" in out:
        record("WARN", "Dependency vulnerabilities", "pip-audit not installed")
    elif r.returncode == 0:
        record("PASS", "Dependency vulnerabilities", "no known vulnerabilities")
    elif "Found" in out and "vulnerab" in out:
        lines = [l for l in out.splitlines() if "vulnerab" in l.lower()]
        record("FAIL", "Dependency vulnerabilities", lines[0] if lines else out[-200:])
    else:
        record("WARN", "Dependency vulnerabilities", "audit could not run (offline?)")


def check_bitlocker() -> None:
    if os.name != "nt":
        record("WARN", "Disk encryption", "check FileVault / LUKS yourself")
        return
    drive = str(ROOT.drive or "C:")
    r = run(["manage-bde", "-status", drive], timeout=60)
    out = r.stdout + r.stderr
    if "Protection On" in out:
        record("PASS", f"BitLocker on {drive}", "protection on")
    elif "Protection Off" in out:
        record("WARN", f"BitLocker on {drive}", "protection off: turn on BitLocker for data at rest")
    else:
        record("WARN", f"BitLocker on {drive}", "status needs an administrator prompt; check in Settings")


def check_config() -> None:
    sys.path.insert(0, str(ROOT))
    from app import config
    record("PASS" if config.RESPECT_ROBOTS else "WARN", "robots.txt respected",
           "" if config.RESPECT_ROBOTS else "SCRAPER_RESPECT_ROBOTS=0")
    exposed = [h for h in config.ALLOWED_HOSTS if h not in ("localhost", "127.0.0.1", "[::1]")]
    record("WARN" if exposed else "PASS", "Web UI answers only localhost",
           f"extra hosts: {exposed}" if exposed else "")
    if config.NET_ALLOW_HOSTS:
        record("WARN", "Network guard exceptions", ", ".join(sorted(config.NET_ALLOW_HOSTS)))
    else:
        record("PASS", "Network guard has no exceptions")
    sess = sorted(config.SESSIONS_DIR.glob("*.bin")) if config.SESSIONS_DIR.exists() else []
    record("PASS", "Saved login sessions", f"{len(sess)} (encrypted, max age "
           f"{config.SESSION_MAX_AGE_DAYS:g} days)")


def main() -> int:
    print("Universal Scraper security check\n")
    for fn in (check_config, check_git_ignores, check_secrets, check_tests, check_audit, check_bitlocker):
        try:
            fn()
        except Exception as e:
            record("WARN", fn.__name__, f"could not run: {e}")
    width = max(len(n) for _, n, _ in results)
    for status, name, detail in results:
        print(f"  [{status}] {name.ljust(width)}  {detail}")
    failed = sum(1 for s, _, _ in results if s == "FAIL")
    warned = sum(1 for s, _, _ in results if s == "WARN")
    print(f"\n{len(results) - failed - warned} passed, {warned} warning(s), {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
