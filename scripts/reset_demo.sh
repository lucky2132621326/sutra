#!/usr/bin/env bash
# Wipes and re-seeds all demo state: SQLite campus data, LangGraph
# checkpoints, recorded event fixtures, and BOTH memory tiers (profile facts
# and the semantic turn-summary index). Run between demo takes.
set -euo pipefail
cd "$(dirname "$0")/.."

rm -f data/campus.db data/checkpoints.db data/memory.db
rm -rf chroma_db
rm -f fixtures/*.jsonl

python scripts/seed.py

echo "Demo state reset (campus data, checkpoints, fixtures, memory)."
