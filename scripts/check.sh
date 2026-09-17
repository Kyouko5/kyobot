#!/usr/bin/env bash
# Quality gate for MyAgent: run every check the project promises, in one command.
#
# Usage:
#   scripts/check.sh              # format check, lint, types, tests + coverage
#   scripts/check.sh -k logging   # extra arguments are forwarded to pytest
set -euo pipefail

cd "$(dirname "$0")/.."

# Prefer the project virtualenv so the gate works without activation.
if [[ -x .venv/bin/python ]]; then
  PYTHON="${PYTHON:-.venv/bin/python}"
else
  PYTHON="${PYTHON:-python3}"
fi
echo "== using $PYTHON =="

echo "== ruff format --check =="
"$PYTHON" -m ruff format --check .

echo "== ruff check =="
"$PYTHON" -m ruff check .

echo "== mypy =="
"$PYTHON" -m mypy

echo "== pytest =="
"$PYTHON" -m pytest --cov=myagent --cov-report=term-missing "$@"

echo "== all checks passed =="
