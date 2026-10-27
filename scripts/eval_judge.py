"""Score a judge run against human labels. Thin wrapper over evalkit.

    python scripts/eval_judge.py                     # latest tracked run; same as: python -m evalkit score
    python scripts/eval_judge.py --run evals/runs/baseline-v1-gpt-4.1
    python scripts/eval_judge.py --min-precision 0.5 --min-recall 0.5    # exit 1 below the floor
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from evalkit.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main(["score", *sys.argv[1:]]))
