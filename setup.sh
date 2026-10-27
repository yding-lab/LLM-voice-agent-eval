#!/usr/bin/env bash
# One-time setup on macOS/Linux:  ./setup.sh
# Use an existing venv folder name with:  VENV=.venv312 ./setup.sh
set -euo pipefail
cd "$(dirname "$0")"

VENV=${VENV:-.venv}

# Python 3.12 preferred; 3.13 / 3.11 accepted. Never 3.14: the voice packages (onnxruntime)
# have no 3.14 builds for Intel Macs. 3.10 can't resolve them either.
ok_python() { "$1" -c 'import sys; sys.exit(0 if (3, 11) <= sys.version_info < (3, 14) else 1)' 2>/dev/null; }

PY=${PYTHON:-}
if [ -n "$PY" ] && ! ok_python "$PY"; then
  echo "PYTHON=$PY is $("$PY" --version 2>&1); need 3.11-3.13 (3.12 preferred)." >&2
  exit 1
fi
if [ -z "$PY" ]; then
  for cand in python3.12 python3.13 python3.11 python3; do
    if command -v "$cand" >/dev/null 2>&1 && ok_python "$cand"; then
      PY=$cand; break
    fi
  done
fi
if [ -z "$PY" ]; then
  echo "Need Python 3.12 (3.11-3.13 also work). Found: $(python3 --version 2>&1)." >&2
  echo "On a Mac:  brew install python@3.12   (or the installer from python.org), then rerun ./setup.sh" >&2
  exit 1
fi
echo "Using $PY ($("$PY" --version)) -> $VENV"

# Rebuild the venv if it was made with an unsupported Python (e.g. an earlier 3.14 attempt).
if [ -d "$VENV" ] && ! ok_python "$VENV/bin/python"; then
  echo "Removing $VENV (built with $("$VENV/bin/python" --version 2>&1))"
  rm -rf "$VENV"
fi
[ -d "$VENV" ] || "$PY" -m venv "$VENV"
# shellcheck disable=SC1091
source "$VENV/bin/activate"
python -m pip install --upgrade pip -q
python -m pip install -r requirements.txt -r requirements-eval.txt pytest

[ -f .env ] || { cp .env.example .env; echo "Created .env from .env.example (it is git-ignored)."; }

python -m livekit.agents download-files   # model weights for installed plugins (VAD etc.)

echo
echo "Done. Next:"
echo "  source $VENV/bin/activate"
echo "  1. Put DEEPGRAM / OPENAI / CARTESIA keys in .env"
echo "  2. python scripts/preflight.py --console                                   (checks console needs)"
echo "  3. SCENARIO=schedule_new_checkup python -m caller.agent console --text     (type as the clinic)"
echo "     SCENARIO=schedule_new_checkup python -m caller.agent console            (talk through your mic)"
echo "  Real calls later: fill LiveKit/Twilio/patient in .env, python scripts/setup_trunk.py,"
echo "  python scripts/preflight.py, then python scripts/run_calls.py schedule_new_checkup"
