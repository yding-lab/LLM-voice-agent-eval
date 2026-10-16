#!/usr/bin/env bash
# One-time setup on macOS/Linux:  ./setup.sh
set -euo pipefail
cd "$(dirname "$0")"

# Pick Python 3.11-3.13. The voice packages (onnxruntime) have no Python 3.14 builds for Intel Macs, and 3.10 cannot resolve them either.
PY=${PYTHON:-}
if [ -z "$PY" ]; then
  for cand in python3.13 python3.12 python3.11 python3; do
    if command -v "$cand" >/dev/null 2>&1 && "$cand" -c 'import sys; sys.exit(0 if (3, 11) <= sys.version_info < (3, 14) else 1)' 2>/dev/null; then
      PY=$cand; break
    fi
  done
fi
if [ -z "$PY" ]; then
  echo "Need Python 3.11, 3.12 or 3.13 (found: $(python3 --version 2>&1))." >&2
  echo "On a Mac:  brew install python@3.12   then run ./setup.sh again." >&2
  echo "(No Homebrew? Install it from https://brew.sh first.)" >&2
  exit 1
fi
echo "Using $PY ($("$PY" --version))"

# Rebuild the venv if it was made with an unsuitable Python (e.g. an earlier failed run).
if [ -d .venv ] && ! .venv/bin/python -c 'import sys; sys.exit(0 if (3, 11) <= sys.version_info < (3, 14) else 1)' 2>/dev/null; then
  echo "Removing old .venv built with $(.venv/bin/python --version 2>&1)"
  rm -rf .venv
fi
[ -d .venv ] || "$PY" -m venv .venv
source .venv/bin/activate
pip install --upgrade pip -q
pip install -r requirements.txt

[ -f .env ] || { cp .env.example .env; echo "Created .env from .env.example. Fill in your keys."; }

python -m caller.agent download-files   # VAD + turn-detector model weights

echo
echo "Done. Next:"
echo "  1. Fill in .env"
echo "  2. source .venv/bin/activate && python scripts/setup_trunk.py   (paste the trunk id into .env)"
echo "  3. python scripts/preflight.py   (checks every key and the trunk; fix any FAIL)"
echo "  4. SCENARIO=schedule_new_checkup python -m caller.agent console   (free rehearsal, you play the clinic)"
echo "  5. python scripts/run_calls.py schedule_new_checkup              (first real call)"
