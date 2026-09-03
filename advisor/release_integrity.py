"""Content digest for the deployed Advisor code/config release."""
from __future__ import annotations

import argparse
import hashlib
from pathlib import Path

EXCLUDED_PARTS = {"data", "logs", "__pycache__", ".pytest_cache"}


def tree_digest(root: Path) -> str:
    digest = hashlib.sha256()
    paths = sorted(p for p in root.rglob("*")
                   if p.is_file() and not EXCLUDED_PARTS.intersection(p.relative_to(root).parts))
    for path in paths:
        rel = path.relative_to(root).as_posix().encode()
        digest.update(len(rel).to_bytes(4, "big"))
        digest.update(rel)
        content_hash = hashlib.sha256(path.read_bytes()).digest()
        digest.update(content_hash)
    return digest.hexdigest()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", type=Path,
                    default=Path(__file__).resolve().parent)
    args = ap.parse_args()
    print(tree_digest(args.root.resolve()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
