"""Experiment tracking. Every judge run is a folder with one JSON per call plus manifest.json.

    evals/runs/<run>/
      manifest.json      what produced this run and what it cost (schema below)
      <call>.json        the judge's raw output for that call
      report.md          scores against human labels, written by the pipeline

manifest.json:
  run_id, created_at, git_commit, evalkit_version
  config            the resolved EvalConfig (provider, model, rubric, redact, timeouts, retries)
  rubric_sha        SHA-256 of the rubric text actually sent
  scenario_set_sha  SHA-256 of caller/scenarios.yaml
  fingerprint       every setting that changes the judge's output (see fingerprint()); a run
                    can only be resumed with an identical fingerprint
  calls.<call>      input_sha, scenario_sha, judged_at, status, error, attempts, latency_s,
                    input/output tokens, cost_usd, redactions
  resumes           one entry per resumed invocation: when, which commit, which calls were
                    judged and which were reused unchanged
  totals            calls, ok, errors, tokens, cost_usd, mean/p50/max latency

A reused call keeps its original manifest entry byte for byte, so the record always describes
the invocation that actually produced that output.
"""

from __future__ import annotations

import json
import statistics
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import yaml

from . import __version__
from .config import EvalConfig
from .data import Call, scenario_set_sha, sha256
from .judge import Outcome
from .paths import PRICING_FILE, ROOT, RUNS_DIR
from .rubrics import rubric_sha

MANIFEST = "manifest.json"
NON_CALL_FILES = {MANIFEST}


def git_commit() -> str | None:
    try:
        return subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True,
                              text=True, timeout=5).stdout.strip() or None
    except Exception:
        return None


def price(model: str, input_tokens: int | None, output_tokens: int | None) -> float | None:
    """USD from evals/pricing.yaml (per 1M tokens). None when the model or the usage is unknown."""
    if input_tokens is None or output_tokens is None or not PRICING_FILE.exists():
        return None
    p = (yaml.safe_load(PRICING_FILE.read_text()) or {}).get("models", {}).get(model)
    if not p:
        return None
    return round((input_tokens * p["input_per_1m"] + output_tokens * p["output_per_1m"]) / 1e6, 6)


def scenario_sha(call: Call) -> str:
    """Hash of the per-call scenario (goal, expected behaviour, checks) that goes into the prompt."""
    return sha256(json.dumps(call.scenario, sort_keys=True, ensure_ascii=False))[:12]


def fingerprint(cfg: EvalConfig, names: list[str] = ()) -> dict:
    """Every run-level setting that can change what the judge returns for the same transcript.

    Timeouts and retry settings are left out: they change whether a call succeeds, not what
    a successful call returns. The pseudonymization name list is hashed (never stored) because
    it decides what the model sees when redaction is on.
    """
    return {
        "provider": cfg.provider,
        "model": cfg.model,
        "rubric": cfg.rubric,
        "rubric_sha": rubric_sha(cfg.rubric),
        "scenario_set_sha": scenario_set_sha(),
        "redact": cfg.redact,
        "redaction_names_sha": sha256("\n".join(sorted(set(names))))[:12] if cfg.redact else None,
        "dataset": cfg.dataset,
        "replay_run": cfg.replay_run or None,
    }


def call_entry(o: Outcome, cfg: EvalConfig, call: Call | None) -> dict:
    return {
        "input_sha": o.input_sha, "scenario_sha": scenario_sha(call) if call else None,
        "judged_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "status": o.status, "error": o.error, "attempts": o.attempts,
        "latency_s": o.latency_s, "input_tokens": o.input_tokens, "output_tokens": o.output_tokens,
        "cost_usd": price(cfg.model, o.input_tokens, o.output_tokens) if cfg.provider != "replay" else 0.0,
        "redactions": o.redactions,
    }


def build_manifest(run_id: str, cfg: EvalConfig, outcomes: list[Outcome], *, calls_by_name: dict | None = None,
                   names: list[str] = (), previous: dict | None = None, reused: tuple[str, ...] = ()) -> dict:
    """A fresh manifest, or `previous` extended by a resumed invocation.

    On resume, calls in `reused` and calls of `previous` not touched this time keep their
    original entries unchanged; only newly judged calls get new entries.
    """
    calls_by_name = calls_by_name or {}
    calls = dict(previous["calls"]) if previous else {}
    judged = []
    for o in outcomes:
        if o.call in reused:
            continue
        calls[o.call] = call_entry(o, cfg, calls_by_name.get(o.call))
        judged.append(o.call)
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    if previous:
        manifest = {k: v for k, v in previous.items() if k not in ("calls", "totals")}
        manifest["resumes"] = list(previous.get("resumes", [])) + [{
            "at": now, "git_commit": git_commit(), "evalkit_version": __version__,
            "judged": judged, "reused": list(reused)}]
    else:
        manifest = {
            "run_id": run_id,
            "created_at": now,
            "git_commit": git_commit(),
            "evalkit_version": __version__,
            "config": cfg.to_dict(),
            "rubric_sha": rubric_sha(cfg.rubric),
            "scenario_set_sha": scenario_set_sha(),
            "fingerprint": fingerprint(cfg, names),
        }
    manifest["calls"] = calls
    manifest["totals"] = totals(calls)
    return manifest


def totals(calls: dict) -> dict:
    lat = [c["latency_s"] for c in calls.values() if c.get("latency_s") is not None]
    toks = [(c.get("input_tokens"), c.get("output_tokens")) for c in calls.values()]
    costs = [c.get("cost_usd") for c in calls.values()]
    known = lambda xs: None if any(x is None for x in xs) else sum(xs)  # noqa: E731
    return {
        "calls": len(calls),
        "ok": sum(c["status"] == "ok" for c in calls.values()),
        "errors": sum(c["status"] != "ok" for c in calls.values()),
        "input_tokens": known([t[0] for t in toks]),
        "output_tokens": known([t[1] for t in toks]),
        "cost_usd": None if any(c is None for c in costs) else round(sum(costs), 4),
        "latency_s": {"mean": round(statistics.mean(lat), 2), "p50": round(statistics.median(lat), 2),
                      "max": round(max(lat), 2)} if lat else None,
    }


def save_run(run_dir: Path, cfg: EvalConfig, outcomes: list[Outcome], **kw) -> dict:
    """Write outputs and the manifest. Reused outputs (kw['reused']) are left untouched on disk."""
    run_dir.mkdir(parents=True, exist_ok=True)
    reused = kw.get("reused", ())
    for o in outcomes:
        if o.output is not None and o.call not in reused:
            (run_dir / f"{o.call}.json").write_text(json.dumps(o.output, indent=2))
    manifest = build_manifest(run_dir.name, cfg, outcomes, **kw)
    (run_dir / MANIFEST).write_text(json.dumps(manifest, indent=2))
    return manifest


def load_run(run_dir: Path) -> tuple[dict[str, dict], dict | None]:
    """({call: judge output}, manifest or None). Also reads legacy calls/*/findings.json folders."""
    run_dir = Path(run_dir)
    mpath = run_dir / MANIFEST
    manifest = json.loads(mpath.read_text()) if mpath.exists() else None
    outputs = {f.stem: json.loads(f.read_text()) for f in sorted(run_dir.glob("*.json"))
               if f.name not in NON_CALL_FILES}
    for o in outputs.values():
        o.pop("_meta", None)  # older runs stored metadata inline; the manifest replaces it
    return outputs, manifest


def list_runs(runs_dir: Path = RUNS_DIR) -> list[dict]:
    out = []
    for d in sorted(p for p in runs_dir.iterdir() if (p / MANIFEST).exists()):
        m = json.loads((d / MANIFEST).read_text())
        out.append({"run": d.name, "created_at": m.get("created_at"), "provider": m["config"].get("provider"),
                    "model": m["config"].get("model"), "rubric": m["config"].get("rubric"),
                    "calls": m["totals"]["calls"], "cost_usd": m["totals"].get("cost_usd")})
    return out


def latest_run(runs_dir: Path = RUNS_DIR) -> Path | None:
    """The most recent real judge run (replay runs only re-serve recorded outputs, so they're skipped)."""
    runs = [r for r in list_runs(runs_dir) if r["provider"] != "replay"]
    runs.sort(key=lambda r: r["created_at"] or "")
    return runs_dir / runs[-1]["run"] if runs else None
