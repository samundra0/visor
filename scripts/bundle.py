#!/usr/bin/env python3
"""Bundle the visor server tree into the Hermes plugin directory.

The plugin at hermes/plugin/ ships its own copy of the server so a catalog
install is turnkey (no separate repo clone). This script mirrors the
canonical dev tree at the repo root into hermes/plugin/server/:

  server.py, index.html, Dockerfile, docker-compose.yml, plugins/**
  (excluding __pycache__ / *.pyc)

Usage:
  python3 scripts/bundle.py            # copy (sync) the bundle
  python3 scripts/bundle.py --check    # report drift, exit 1 if any (CI)

Run after every server-tree change; CI fails the build if the bundle
drifts from the root tree.
"""
import filecmp
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BUNDLE = ROOT / "hermes" / "plugin" / "server"

FILES = ["server.py", "index.html", "Dockerfile", "docker-compose.yml"]
DIRS = ["plugins"]

EXCLUDE = ("__pycache__", ".pyc")


def _toprinter(path: Path) -> str:
    return str(path.relative_to(ROOT))


def collect_files() -> dict:
    """Map rel-path -> absolute source path for everything the bundle ships."""
    out = {}
    for f in FILES:
        p = ROOT / f
        if p.is_file():
            out[f] = p
    for d in DIRS:
        base = ROOT / d
        if base.is_dir():
            for p in sorted(base.rglob("*")):
                if p.is_file() and not any(part in EXCLUDE for part in p.parts):
                    out[str(p.relative_to(ROOT))] = p
    return out


def _is_runtime_artifact(rel: str) -> bool:
    """__pycache__ / .pyc are runtime byproducts (a server running from the
    bundle regenerates them) — never drift material."""
    return "__pycache__" in rel.split("/") or rel.endswith(".pyc")


def sync() -> None:
    sources = collect_files()
    BUNDLE.mkdir(parents=True, exist_ok=True)
    # remove stale bundle files (mirror, not overlay)
    for p in sorted(BUNDLE.rglob("*")):
        if p.is_file() and str(p.relative_to(BUNDLE)) not in sources \
                and not _is_runtime_artifact(str(p.relative_to(BUNDLE))):
            p.unlink()
            print(f"removed  {p.relative_to(BUNDLE)}")
    for rel, src in sorted(sources.items()):
        dst = BUNDLE / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        if not dst.exists() or not filecmp.cmp(src, dst, shallow=False):
            shutil.copy2(src, dst)
            print(f"copied   {rel}")
    # prune now-empty dirs
    for p in sorted((x for x in BUNDLE.rglob("*") if x.is_dir()), key=lambda x: -len(x.parts)):
        if p != BUNDLE and not any(p.iterdir()):
            p.rmdir()
    print(f"bundle synced -> {_toprinter(BUNDLE)} ({len(sources)} files)")


def check() -> None:
    sources = collect_files()
    BUNDLE.mkdir(parents=True, exist_ok=True)
    drift = []
    for rel, src in sorted(sources.items()):
        dst = BUNDLE / rel
        if not dst.is_file():
            drift.append(f"missing   {rel}")
        elif not filecmp.cmp(src, dst, shallow=False):
            drift.append(f"differs   {rel}")
    for p in sorted(BUNDLE.rglob("*")):
        rel = str(p.relative_to(BUNDLE))
        if p.is_file() and rel not in sources and not _is_runtime_artifact(rel):
            drift.append(f"stale     {rel}")
    if drift:
        print("bundle drift (run `python3 scripts/bundle.py` to fix):")
        for d in drift:
            print("  " + d)
        sys.exit(1)
    print(f"bundle in sync ({len(sources)} files) -> {_toprinter(BUNDLE)}")


if __name__ == "__main__":
    check() if "--check" in sys.argv else sync()
