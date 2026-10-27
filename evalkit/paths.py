from __future__ import annotations

import os
from dataclasses import dataclass
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

SAMPLES_DIR = ROOT / "samples"


@dataclass(frozen=True)
class Dataset:
    """A set of calls with its labels, splits and tracked runs."""

    name: str
    calls_dir: Path
    labels_dir: Path
    splits_file: Path
    runs_dir: Path
    replay_run: str  # the recorded run the keyless replay provider serves from


DATASETS = {
    # The 11 real calls to a third-party demo agent. See DATA_AUDIT.md for publication status.
    "recorded": Dataset("recorded", CALLS_DIR, LABELS_DIR, SPLITS_FILE, RUNS_DIR, "baseline-v1-gpt-4.1"),
    # Hand-written fictional calls with seeded defects; permission-cleared, used by the offline demo.
    "synthetic": Dataset("synthetic", SAMPLES_DIR / "calls", SAMPLES_DIR / "labels", SAMPLES_DIR / "splits.yaml",
                         SAMPLES_DIR / "runs", "illustrative-fixture"),
}


def get_dataset(name: str | None = None) -> Dataset:
    name = name or os.getenv("EVALKIT_DATASET", "recorded")
    if name not in DATASETS:
        raise ValueError(f"dataset must be one of {sorted(DATASETS)}, not {name!r}")
    return DATASETS[name]
