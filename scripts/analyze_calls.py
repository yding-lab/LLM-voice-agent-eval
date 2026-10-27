"""Judge every call with the LLM judge and save a tracked run. Thin wrapper over evalkit.

    python scripts/analyze_calls.py                         # same as: python -m evalkit run
    python scripts/analyze_calls.py --name judge-v2 --force
    ANALYSIS_PROVIDER=bedrock ANALYSIS_MODEL=<model id> python scripts/analyze_calls.py --name v2-bedrock

Each run lands in evals/runs/<name>/ with one JSON per call, manifest.json (model, rubric
hash, inputs, tokens, cost, latency) and report.md (scores against human labels).
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from evalkit.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main(["run", *sys.argv[1:]]))
