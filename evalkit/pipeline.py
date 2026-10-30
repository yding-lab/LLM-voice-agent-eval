"""The end-to-end evaluation pipeline.

    calls -> [pseudonymize -> judge (retries)] per call -> save run + manifest
          -> score against labels per split -> report.md

One failed call never sinks a run: it is recorded with its error in the manifest and the
rest continue.

A run can be resumed (e.g. after a rate-limit failure), but only when nothing that affects
the result has changed. Before reusing anything, the pipeline checks:
  - run level: provider, model, rubric name and rubric text hash, scenario-set hash,
    redaction on/off and the pseudonymization name list, dataset, replay source
    (runs.fingerprint); any difference -> ResumeConflict, use a new run name
  - per call: transcript hash and per-call scenario hash; any difference -> ResumeConflict,
    unless force=True, which re-judges those calls under the same settings
Reused calls keep their original manifest entries; nothing new is written for them.
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
from .runs import fingerprint, load_run, save_run, scenario_sha
from .scoring import as_dict, load_splits, score_by_split, truth_labels

log = logging.getLogger("evalkit.pipeline")


class ResumeConflict(RuntimeError):
    """The run folder holds outputs that don't match the current inputs or settings."""


def check_resume(run_dir: Path, existing: dict, manifest: dict | None, fp: dict, calls: list[Call],
                 force: bool) -> tuple[str, ...]:
    """Names of calls whose saved output may be reused. Raises ResumeConflict otherwise."""
    if not existing and not manifest:
        return ()
    hint = f"Use a new run name (--name) instead of {run_dir.name!r}"
    if manifest is None or "fingerprint" not in manifest:
        raise ResumeConflict(
            f"{run_dir} has outputs but no provenance fingerprint (it predates resume checks or was "
            f"written by hand), so it can't be verified for reuse. {hint}.")
    old = manifest["fingerprint"]
    diff = {k: (old.get(k), fp.get(k)) for k in sorted(set(old) | set(fp)) if old.get(k) != fp.get(k)}
    if diff:
        detail = "; ".join(f"{k}: {a!r} -> {b!r}" for k, (a, b) in diff.items())
        raise ResumeConflict(f"settings differ from the saved run ({detail}). Saved outputs were produced "
                             f"under the old settings and can't be mixed with new ones. {hint}.")
    reusable, changed = [], []
    for call in calls:
        if call.name not in existing:
            continue
        prev = manifest["calls"].get(call.name)
        if not prev:
            changed.append(f"{call.name} (output has no manifest entry)")
        elif prev.get("input_sha") != call.input_sha:
            changed.append(f"{call.name} (transcript changed)")
        elif prev.get("scenario_sha") != scenario_sha(call):
            changed.append(f"{call.name} (scenario changed)")
        elif prev.get("status") == "ok":
            reusable.append(call.name)
    if force:
        return ()
    if changed:
        raise ResumeConflict(f"inputs changed since these calls were judged: {', '.join(changed)}. "
                             f"{hint}, or pass force=True (--force) to re-judge every call under the same settings.")
    return tuple(reusable)


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
    fp = fingerprint(cfg, names)
    reused = check_resume(run_dir, existing, old_manifest, fp, calls, force)

    outcomes: list[Outcome] = []
    for i, call in enumerate(calls, 1):
        if call.name in reused:
            outcomes.append(Outcome(call=call.name, input_sha=call.input_sha, status="ok",
                                    output=existing[call.name]))
            log.info("[%d/%d] %s: reusing saved output (inputs and settings verified)", i, len(calls), call.name)
            continue
        log.info("[%d/%d] %s: judging with %s:%s (rubric %s)", i, len(calls), call.name,
                 provider.name, provider.model, cfg.rubric)
        outcomes.append(judge_call(provider, call, cfg, names))

    manifest = save_run(run_dir, cfg, outcomes, calls_by_name={c.name: c for c in calls}, names=names,
                        previous=old_manifest, reused=reused)
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
