"""Find API keys, tokens and cookies before they get committed.

    python scripts/secret_scan.py            # scan all tracked files
    python scripts/secret_scan.py --staged   # scan what is about to be committed (pre-commit hook)

Exit code 1 when something looks like a secret.
"""
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PATTERNS = [
    ("Groq API key", re.compile(r"gsk_[A-Za-z0-9]{20,}")),
    ("Anthropic API key", re.compile(r"sk-ant-[A-Za-z0-9_\-]{20,}")),
    ("OpenAI-style key", re.compile(r"\bsk-(?:proj-|svcacct-|admin-)?[A-Za-z0-9_\-]{32,}")),
    ("AWS access key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("Google API key", re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b")),
    ("GitHub token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}\b")),
    ("Slack token", re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}")),
    ("Private key", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |)PRIVATE KEY-----")),
    ("Cookie header", re.compile(r"(?im)^\s*cookie:\s*\S+=\S{16,}")),
    ("Key assignment", re.compile(r"(?im)^\s*(?:GROQ|ANTHROPIC|OPENAI)_API_KEY\s*=\s*[\"']?"
                                  r"[A-Za-z0-9_\-]{16,}[\"']?\s*$")),
]
FORBIDDEN_FILES = re.compile(r"(^|/)(\.env|.*\.db|.*\.bin|audit\.log)$")
SKIP_FILES = {"scripts/secret_scan.py", "tests/test_security.py"}


def _git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True,
                          encoding="utf-8", errors="replace").stdout


def files_to_scan(staged: bool) -> list[tuple[str, str]]:
    if staged:
        names = [n for n in _git("diff", "--cached", "--name-only", "--diff-filter=ACM").splitlines() if n]
        return [(n, _git("show", f":{n}")) for n in names]
    out = []
    for n in _git("ls-files").splitlines():
        p = ROOT / n
        if p.is_file() and p.stat().st_size < 2_000_000:
            out.append((n, p.read_text(encoding="utf-8", errors="replace")))
    return out


def scan(staged: bool = False) -> list[str]:
    problems = []
    for name, text in files_to_scan(staged):
        norm = name.replace("\\", "/")
        if FORBIDDEN_FILES.search(norm):
            problems.append(f"{name}: this file must never be committed (secrets / data)")
            continue
        if norm in SKIP_FILES or norm.startswith("tests/fixtures/"):
            continue
        for label, pattern in PATTERNS:
            for m in pattern.finditer(text):
                line = text.count("\n", 0, m.start()) + 1
                problems.append(f"{name}:{line}: possible {label}")
    return problems


def main() -> int:
    staged = "--staged" in sys.argv
    problems = scan(staged)
    if problems:
        print("Secret scan found problems:")
        for p in problems:
            print("  " + p)
        print("Remove the secret (and rotate it if it was ever shared), then try again.")
        return 1
    print("Secret scan: clean")
    return 0


if __name__ == "__main__":
    sys.exit(main())
