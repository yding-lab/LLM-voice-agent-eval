"""Markdown reports for runs, scores and comparisons."""

from __future__ import annotations

from .scoring import SEVERITIES, Score


def score_md(scores: dict[str, Score], run_name: str, manifest: dict | None) -> str:
    lines = [f"# Judge evaluation: {run_name}", ""]
    if manifest:
        c = manifest["config"]
        lines += [f"Judge: `{c.get('provider')}:{c.get('model')}`, rubric `{c.get('rubric')}` "
                  f"(sha `{manifest.get('rubric_sha')}`), redaction {'on' if c.get('redact') else 'off'}.", ""]
        if manifest.get("backfilled"):
            lines += [f"> {manifest['backfilled']}", ""]
    for name, s in scores.items():
        lines += [f"## Split: {name} ({len(s.calls)} calls)", ""]
        if not s.calls:
            lines += ["No calls in this split yet. New calls land here automatically; label their judge "
                      "output in `evals/labels/` before reading these numbers.", ""]
            continue
        if name == "dev":
            lines += ["*Development split: rubric v2 was designed from v1's errors on these calls, "
                      "so v2 scores here are in-sample.*", ""]
        lines += ["| Metric | Value |", "|---|---|",
                  f"| Precision (failed checks that were real agent defects) | **{s.precision.fmt()}** |",
                  f"| Recall (confirmed bugs flagged) | **{s.recall().fmt()}** |",
                  f"| Recall, Critical + High | {s.recall(('Critical', 'High')).fmt()} |"]
        for sev in SEVERITIES:
            r = s.recall((sev,))
            if r.n:
                lines.append(f"| Recall, {sev} | {r.fmt()} |")
        lines += ["", "**Wrong fails by type** (of all failed checks with a human label):", "",
                  "| Type | Count |", "|---|---|"]
        lines += [f"| {k} | {s.fail_labels[k]} |" for k in ("confirmed", "caused_by_caller", "not_adopted")]
        if s.miss_types:
            lines += ["", "**Missed bugs by type:** " + ", ".join(f"{k} {v}" for k, v in sorted(s.miss_types.items())), ""]
        lines += ["", "| Bug | Severity | Judge | How | Miss type |", "|---|---|---|---|---|"]
        for b in s.bugs:
            lines.append(f"| {b['id']}. {b['title']} | {b['severity']} | {b['status']} | "
                         f"{', '.join(b['how']) or '-'} | {b['miss_type'] or '-'} |")
        if s.lenient:
            lines += ["", "Lenient grades (bug present, check passed):", *[f"- {x}" for x in s.lenient]]
        if s.unlabeled_fails:
            lines += ["", "Failed checks with no human label yet (label them in evals/labels/ before trusting precision):",
                      *[f"- {x}" for x in s.unlabeled_fails]]
        lines.append("")
    return "\n".join(lines)


def run_md(manifest: dict) -> str:
    t, c = manifest["totals"], manifest["config"]
    lat = t.get("latency_s") or {}
    rows = [
        ("Run", manifest["run_id"]), ("Created", manifest.get("created_at")),
        ("Git commit", manifest.get("git_commit")), ("Judge", f"{c.get('provider')}:{c.get('model')}"),
        ("Rubric", f"{c.get('rubric')} (sha {manifest.get('rubric_sha')})"),
        ("Scenario set sha", manifest.get("scenario_set_sha")), ("Redaction", c.get("redact")),
        ("Calls", f"{t['ok']} ok, {t['errors']} errors"),
        ("Tokens in / out", f"{t.get('input_tokens')} / {t.get('output_tokens')}"),
        ("Cost (USD, est.)", t.get("cost_usd")),
        ("Latency per call (s)", f"mean {lat.get('mean')}, p50 {lat.get('p50')}, max {lat.get('max')}" if lat else None),
    ]
    return "\n".join(["| | |", "|---|---|", *[f"| {k} | {v if v is not None else 'not recorded'} |" for k, v in rows]])


ICON = {"fail": "FAIL", "pass": "pass", "not_tested": "-"}


def findings_md(name: str, r: dict) -> str:
    """One call's judge output as a readable report."""
    met = {True: "yes", False: "NO", None: "unclear"}.get(r.get("pass_outcome_met"), "unclear")
    lines = [f"## {name}", "", r.get("summary", ""), "", f"Passing outcome met: **{met}**", "",
             "| Check | Severity | Result | At | Evidence | Confidence |", "|---|---|---|---|---|---|"]
    for c in r.get("checks", []):
        ev = (c.get("evidence") or "").replace("|", "/").replace("\n", " ")
        lines.append(f"| {c.get('id')} | {c.get('severity', '')} | {ICON.get(c.get('result'), c.get('result'))} | "
                     f"{c.get('timestamp', '')} | {ev} | {c.get('confidence', '')} |")
    other = r.get("other_findings", [])
    if other:
        lines += ["", "Other findings:"]
        for f in other:
            lines.append(f"- **{f.get('title')}** ({f.get('severity')}, confidence {f.get('confidence')}) at "
                         f"{f.get('timestamp')}: {f.get('evidence')} Why: {f.get('why_it_matters', '')}")
    return "\n".join(lines) + "\n"
