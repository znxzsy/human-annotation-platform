#!/usr/bin/env bash
set -euo pipefail

project_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$project_root"

mkdir -p runtime
if [[ ! -f runtime/demo.sqlite3 ]]; then
  python3 scripts/seed_demo.py
fi

export ANNOTATION_AUTH_REQUIRED=0
export ANNOTATION_DEMO_REVIEWER="演示标注员"
exec python3 -m annotation_platform.server \
  --host "${HOST:-127.0.0.1}" \
  --port "${PORT:-18068}" \
  --db runtime/demo.sqlite3 \
  --audit runtime/demo-audit.jsonl \
  --exports runtime/exports
