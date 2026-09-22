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

# If the configured mirror denies access (HTTP 403) or does not carry a package
# version (Phase 5 hit this with pypdf>=5.0), retry against PyPI:
#   PIP_INDEX_URL=https://pypi.org/simple scripts/bootstrap.sh
if [[ -n "${PIP_INDEX_URL:-}" ]]; then
  echo "== using PIP_INDEX_URL=$PIP_INDEX_URL =="
fi

echo "== installing dev dependencies =="
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -e '.[dev]'

# The editable install registers `src/` through a .pth file in site-packages.
# CPython 3.12.5+ ignores .pth files carrying the macOS UF_HIDDEN flag, and on
# some machines a sync/security agent re-applies that flag to every .pth under
# site-packages within seconds. Re-checking is useless in that case, so fall
# back to exporting the path from the venv activation script.
SITE_PACKAGES="$(.venv/bin/python -c 'import site; print(site.getsitepackages()[0])')"
if .venv/bin/python -c 'import myagent' >/dev/null 2>&1; then
  echo "== editable install resolves =="
else
  echo "== .pth is ignored (hidden flag); exporting src/ from activate instead =="
  MARKER="# myagent: src layout (editable .pth is unusable here)"
  if ! grep -qF "$MARKER" .venv/bin/activate; then
    printf '\n%s\nexport PYTHONPATH="%s/src${PYTHONPATH:+:$PYTHONPATH}"\n' \
      "$MARKER" "$PWD" >> .venv/bin/activate
  fi
  .venv/bin/python -c 'import myagent' >/dev/null 2>&1 || {
    echo "warning: myagent still not importable; use PYTHONPATH=src .venv/bin/python -m myagent" >&2
  }
fi

echo
echo "Done. Activate with: source .venv/bin/activate"
echo "Then run the quality gate: scripts/check.sh"
