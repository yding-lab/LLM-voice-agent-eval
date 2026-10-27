"""Compare two tracked runs. Thin wrapper over evalkit.

    python scripts/compare_runs.py evals/runs/baseline-v1-gpt-4.1 evals/runs/judge-v2
    python scripts/compare_runs.py BEFORE AFTER --fail-on-regression    # exit 1 if any check newly fails
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from evalkit.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main(["compare", *sys.argv[1:]]))
