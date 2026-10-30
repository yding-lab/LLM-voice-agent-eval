"""Compare two runs: what changed in the setup, the scores, the cost, and check by check.

Answers both questions a team asks before shipping:
  * Did the judge change?  Same calls, different judge (rubric, model, provider).
  * Did the agent change?  New calls against a new agent version, same judge.
"""

from __future__ import annotations

from pathlib import Path

from .runs import load_run
from .scoring import score_by_split

SEV_ORDER = {"Critical": 0, "High": 1, "Medium": 2, "Low": 3}
SETUP_KEYS = ("provider", "model", "rubric", "redact", "timeout_s", "max_retries", "dataset")


def _results(run: dict[str, dict]) -> dict[tuple[str, str], tuple[str, str]]:
    return {(call, c["id"]): (c.get("result", "?"), c.get("severity", "?"))
            for call, out in run.items() for c in out.get("checks", [])}


def check_diff(a: dict[str, dict], b: dict[str, dict]) -> dict[str, list[dict]]:
    ra, rb = _results(a), _results(b)
    groups: dict[str, list[dict]] = {"newly_failing": [], "newly_passing": [], "coverage_changed": []}
    for key in sorted(set(ra) & set(rb), key=lambda k: (SEV_ORDER.get(rb[k][1], 9), k)):
        (old, sev), (new, _) = ra[key], rb[key]
        if old == new:
            continue
        row = {"call": key[0], "check": key[1], "severity": sev, "before": old, "after": new}
        if new == "fail":
            groups["newly_failing"].append(row)
        elif old == "fail" and new == "pass":
            groups["newly_passing"].append(row)
        else:
            groups["coverage_changed"].append(row)
    return groups


def compare(before: Path, after: Path, labels: dict | None = None, splits: dict | None = None) -> dict:
    a, ma = load_run(before)
    b, mb = load_run(after)
    shared = sorted(set(a) & set(b))
    a, b = {k: a[k] for k in shared}, {k: b[k] for k in shared}
    sa, sb = score_by_split(a, labels, splits), score_by_split(b, labels, splits)
    setup = {}
    for k in SETUP_KEYS:
        va, vb = (ma or {}).get("config", {}).get(k), (mb or {}).get("config", {}).get(k)
        if va != vb:
            setup[k] = {"before": va, "after": vb}
    for k in ("rubric_sha", "scenario_set_sha"):
        if (ma or {}).get(k) != (mb or {}).get(k):
            setup[k] = {"before": (ma or {}).get(k), "after": (mb or {}).get(k)}
    inputs_changed = [c for c in shared if (ma or {}).get("calls", {}).get(c, {}).get("input_sha")
                      != (mb or {}).get("calls", {}).get(c, {}).get("input_sha")]
    metrics = {}
    for split in sa:
        if not sa[split].calls:
            continue
        x, y = sa[split], sb[split]
        metrics[split] = {
            "precision": (x.precision.value, y.precision.value),
            "recall": (x.recall().value, y.recall().value),
            "recall_critical_high": (x.recall(("Critical", "High")).value, y.recall(("Critical", "High")).value),
            "lenient_passes": (len(x.lenient), len(y.lenient)),
            "unlabeled_fails": (len(x.unlabeled_fails), len(y.unlabeled_fails)),
        }
    cost = {k: ((ma or {}).get("totals", {}).get(k), (mb or {}).get("totals", {}).get(k))
            for k in ("cost_usd", "input_tokens", "output_tokens", "errors")}
    cost["latency_p50_s"] = tuple(((m or {}).get("totals", {}).get("latency_s") or {}).get("p50") for m in (ma, mb))
    return {"before": Path(before).name, "after": Path(after).name, "shared_calls": len(shared),
            "setup_changes": setup, "inputs_changed": inputs_changed, "metrics": metrics,
            "cost_and_latency": cost, "checks": check_diff(a, b)}


def _pct(v):
    return "–" if v is None else f"{v:.0%}"


def compare_md(r: dict) -> str:
    lines = [f"# {r['before']} → {r['after']}", "", f"{r['shared_calls']} calls in both runs.", ""]
    if r["setup_changes"]:
        lines += ["## What changed in the setup", "", "| | before | after |", "|---|---|---|",
                  *[f"| {k} | {v['before']} | {v['after']} |" for k, v in r["setup_changes"].items()], ""]
    if r["inputs_changed"]:
        lines += [f"**Inputs differ** for {len(r['inputs_changed'])} calls (different transcripts): "
                  "differences below mix judge and agent changes.", ""]
    for split, m in r["metrics"].items():
        lines += [f"## Scores ({split})", "", "| | before | after |", "|---|---|---|"]
        for k, (x, y) in m.items():
            fmt = _pct if k.startswith(("precision", "recall")) else (lambda v: "–" if v is None else v)
            lines.append(f"| {k.replace('_', ' ')} | {fmt(x)} | {fmt(y)} |")
        lines.append("")
    lines += ["## Cost and latency", "", "| | before | after |", "|---|---|---|",
              *[f"| {k} | {'not recorded' if x is None else x} | {'not recorded' if y is None else y} |"
                for k, (x, y) in r["cost_and_latency"].items()], ""]
    titles = {"newly_failing": "Newly failing", "newly_passing": "Newly passing", "coverage_changed": "Coverage changed"}
    for name, rows in r["checks"].items():
        lines += [f"## {titles[name]} ({len(rows)})", ""]
        if rows:
            lines += ["| Call | Check | Severity | Before | After |", "|---|---|---|---|---|",
                      *[f"| {x['call']} | {x['check']} | {x['severity']} | {x['before']} | {x['after']} |" for x in rows]]
        lines.append("")
    return "\n".join(lines)
