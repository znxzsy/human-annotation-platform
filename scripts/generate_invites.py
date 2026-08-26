#!/usr/bin/env python3
from __future__ import annotations

import argparse
import secrets
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from annotation_platform.store import Store  # noqa: E402


ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def generate_code():
    chunks = ["".join(secrets.choice(ALPHABET) for _ in range(4)) for _ in range(4)]
    return "ANO-" + "-".join(chunks)


def main():
    parser = argparse.ArgumentParser(description="Generate and import one-time reviewer invite codes.")
    parser.add_argument("--count", type=int, default=10)
    parser.add_argument("--db", type=Path, default=ROOT / "runtime/review.sqlite3")
    parser.add_argument("--output", type=Path, default=ROOT / "runtime/invites.txt")
    args = parser.parse_args()
    if not 1 <= args.count <= 10000:
        raise SystemExit("count must be between 1 and 10000")
    codes = set()
    while len(codes) < args.count:
        codes.add(generate_code())
    ordered = sorted(codes)
    result = Store(args.db, ROOT / "runtime/audit.jsonl").import_invites(ordered)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text("\n".join(ordered) + "\n", encoding="utf-8")
    print(f"Imported {result['inserted']} new invite codes. Plaintext saved to {args.output}.")
    print("Keep that file private; only hashes are stored in SQLite.")


if __name__ == "__main__":
    main()
