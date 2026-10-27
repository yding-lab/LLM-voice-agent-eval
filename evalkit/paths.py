from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(os.getenv("EVALKIT_ROOT", Path(__file__).resolve().parent.parent))
CALLS_DIR = ROOT / "calls"
EVALS_DIR = ROOT / "evals"
RUNS_DIR = EVALS_DIR / "runs"
LABELS_DIR = EVALS_DIR / "labels"
SPLITS_FILE = EVALS_DIR / "splits.yaml"
PRICING_FILE = EVALS_DIR / "pricing.yaml"
CONFIG_FILE = EVALS_DIR / "config.yaml"
SCENARIOS_FILE = ROOT / "caller" / "scenarios.yaml"
