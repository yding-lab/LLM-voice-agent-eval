#!/usr/bin/env bash
# One-time setup on macOS/Linux:  ./setup.sh
set -euo pipefail
cd "$(dirname "$0")"

PY=${PYTHON:-python3}
if ! "$PY" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' 2>/dev/null; then
  echo "Python 3.10+ is required. On a Mac: brew install python@3.12, then rerun." >&2
  exit 1
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
echo "  3. SCENARIO=schedule_new_checkup python -m caller.agent console   (free rehearsal, you play the clinic)"
echo "  4. python scripts/run_calls.py schedule_new_checkup              (first real call)"
