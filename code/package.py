"""Build code.zip for submission and verify its contents.

    python code/package.py

Includes README.md and code/; excludes caches, virtualenvs, nested archives, .env files and the
dataset. Fails if a required report is missing or anything resembling a secret is found.
"""
from __future__ import annotations

import re
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "code.zip"
EXCLUDE_DIRS = {"__pycache__", ".cache", ".venv", "venv", "node_modules", ".git", "dataset", "data"}
EXCLUDE_SUFFIXES = {".zip", ".tar", ".gz", ".tgz", ".7z", ".rar", ".pyc"}
REQUIRED = {"README.md", "code/main.py", "code/requirements.txt",
            "code/evaluation/evaluation_report.md", "code/evaluation/usage_report.md"}
SECRET_RE = re.compile(r"sk-ant-[A-Za-z0-9_-]{10,}|AIza[0-9A-Za-z_-]{20,}|ANTHROPIC_API_KEY\s*=\s*(?!your-key-here)\S{8,}")


def files() -> list[Path]:
    out = [ROOT / "README.md"]
    for p in sorted((ROOT / "code").rglob("*")):
        rel = p.relative_to(ROOT)
        if p.is_dir() or EXCLUDE_DIRS & set(rel.parts) or p.suffix.lower() in EXCLUDE_SUFFIXES:
            continue
        if p.name.startswith(".env") and p.name != ".env.example":
            continue
        out.append(p)
    return out


def main() -> int:
    chosen = files()
    problems = []
    for p in chosen:
        try:
            if SECRET_RE.search(p.read_text(encoding="utf-8", errors="ignore")):
                problems.append(f"possible secret in {p.relative_to(ROOT)}")
        except OSError as e:
            problems.append(f"unreadable {p}: {e}")
    names = {p.relative_to(ROOT).as_posix() for p in chosen}
    problems += [f"missing required file {r}" for r in sorted(REQUIRED - names)]
    if problems:
        print("\n".join(problems))
        return 1
    with zipfile.ZipFile(OUT, "w", zipfile.ZIP_DEFLATED) as z:
        for p in chosen:
            z.write(p, p.relative_to(ROOT).as_posix())
    with zipfile.ZipFile(OUT) as z:
        listed = z.namelist()
    print(f"wrote {OUT.name}: {len(listed)} files, {OUT.stat().st_size / 1024:.0f} KB")
    for n in listed:
        print("  ", n)
    return 0


if __name__ == "__main__":
    sys.exit(main())
