"""The end-to-end evaluation pipeline.

    calls -> [pseudonymize -> judge (retries)] per call -> save run + manifest
          -> score against labels per split -> report.md

One failed call never sinks a run: it is recorded with its error in the manifest and the
rest continue. A run can be resumed: calls that already have output are skipped unless
force=True.
"""

from __future__ import annotations

import json
import logging
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

from .config import EvalConfig
from .data import Call, iter_calls, known_names
from .judge import Outcome, judge_call
from .paths import Dataset, get_dataset
from .providers import Provider, ReplayProvider, make_provider
from .report import run_md, score_md
from .runs import load_run, save_run
from .scoring import as_dict, load_splits, score_by_split, truth_labels

log = logging.getLogger("evalkit.pipeline")


def default_run_name(cfg: EvalConfig) -> str:
    return f"{datetime.now(timezone.utc):%Y%m%d-%H%M}-{cfg.provider}-{cfg.model.split(':')[-1]}-{cfg.rubric}"


def run_pipeline(cfg: EvalConfig, run_name: str | None = None, calls: list[Call] | None = None,
                 provider: Provider | None = None, force: bool = False, runs_dir: Path | None = None,
                 dataset: Dataset | None = None) -> dict:
    ds = dataset or get_dataset(cfg.dataset)
    runs_dir = runs_dir or ds.runs_dir
    calls = calls if calls is not None else iter_calls(ds.calls_dir)
    provider = provider or make_provider(cfg)
    if isinstance(provider, ReplayProvider):
        # Record what actually produced the outputs, not what was requested.
        cfg = replace(cfg, model=provider.model, rubric=provider.recorded_rubric, redact=False,
                      replay_run=provider.model)
    run_dir = runs_dir / (run_name or default_run_name(cfg))
    existing, old_manifest = load_run(run_dir) if run_dir.exists() else ({}, None)
    names = known_names()

    outcomes: list[Outcome] = []
    for i, call in enumerate(calls, 1):
        if not force and call.name in existing and old_manifest:
            prev = old_manifest["calls"].get(call.name, {})
            outcomes.append(Outcome(call=call.name, input_sha=call.input_sha, status="ok",
                                    output=existing[call.name], attempts=prev.get("attempts") or 0,
                                    latency_s=prev.get("latency_s"), input_tokens=prev.get("input_tokens"),
                                    output_tokens=prev.get("output_tokens"), redactions=prev.get("redactions", {})))
            log.info("[%d/%d] %s: reusing saved output", i, len(calls), call.name)
            continue
        log.info("[%d/%d] %s: judging with %s:%s (rubric %s)", i, len(calls), call.name,
                 provider.name, provider.model, cfg.rubric)
        outcomes.append(judge_call(provider, call, cfg, names))

    manifest = save_run(run_dir, cfg, outcomes)
    if isinstance(provider, ReplayProvider):
        manifest["replayed_from"] = {"run": provider.model, "provider": provider.recorded_provider,
                                     "model": provider.recorded_model}
        if provider.fixture:
            manifest["fixture"] = provider.fixture
        (run_dir / "manifest.json").write_text(json.dumps(manifest, indent=2))
    outputs, _ = load_run(run_dir)
    scores = score_by_split(outputs, truth_labels(ds.labels_dir), load_splits(ds.splits_file))
    (run_dir / "report.md").write_text(run_md(manifest) + "\n\n" + score_md(scores, run_dir.name, manifest))
    log.info("run %s: %d ok, %d errors -> %s", run_dir.name, manifest["totals"]["ok"],
             manifest["totals"]["errors"], run_dir)
    return {"run_dir": str(run_dir), "manifest": manifest, "scores": as_dict(scores)}
