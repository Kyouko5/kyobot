#!/usr/bin/env bash
# Create the project virtualenv and install dev dependencies.
# Usage: scripts/bootstrap.sh
set -euo pipefail

cd "$(dirname "$0")/.."
PYTHON="${PYTHON:-python3}"

echo "== creating .venv =="
"$PYTHON" -m venv .venv

# The python.org macOS framework build ships without a CA bundle, so pip fails
# with CERTIFICATE_VERIFY_FAILED. Fall back to the system trust store.
if [[ "$(uname -s)" == "Darwin" && -z "${SSL_CERT_FILE:-}" && -f /etc/ssl/cert.pem ]]; then
  export SSL_CERT_FILE=/etc/ssl/cert.pem
  echo "== using SSL_CERT_FILE=$SSL_CERT_FILE =="
fi

echo "== installing dev dependencies =="
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -e '.[dev]'

echo
echo "Done. Activate with: source .venv/bin/activate"
echo "Then run the quality gate: scripts/check.sh"
