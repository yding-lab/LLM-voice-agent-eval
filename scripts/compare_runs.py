"""Regression report between two evaluation runs.

    python scripts/compare_runs.py evals/runs/baseline-v1-gpt-4.1 evals/runs/judge-v2
    python scripts/compare_runs.py evals/runs/agent-v1 evals/runs/agent-v2 --fail-on-regression

Works for both questions a team asks before shipping:
  * Did the judge change?  Same calls, different judge (rubric, model, provider).
  * Did the agent change?  New calls against a new agent version, same judge.

Prints every check whose result changed, grouped as newly failing, newly passing and
coverage changes, plus the judge scores (scripts/eval_judge.py) for each run when they share
the labelled calls. With --fail-on-regression it exits 1 if any check newly fails, so it can
gate a CI job.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
from eval_judge import LABELS, evaluate, load_run  # noqa: E402

SEV_ORDER = {"Critical": 0, "High": 1, "Medium": 2, "Low": 3}


def results(run: dict[str, dict]) -> dict[tuple[str, str], tuple[str, str]]:
    """{(call, check id): (result, severity)}"""
    return {(call, c["id"]): (c.get("result", "?"), c.get("severity", "?"))
            for call, out in run.items() for c in out.get("checks", [])}


def diff(a: dict[str, dict], b: dict[str, dict]) -> dict[str, list]:
    ra, rb = results(a), results(b)
    groups: dict[str, list] = {"newly_failing": [], "newly_passing": [], "coverage": []}
    for key in sorted(set(ra) & set(rb), key=lambda k: (SEV_ORDER.get(rb[k][1], 9), k)):
        (old, sev), (new, _) = ra[key], rb[key]
        if old == new:
            continue
        row = (key[0], key[1], sev, old, new)
        if new == "fail":
            groups["newly_failing"].append(row)
        elif old == "fail" and new == "pass":
            groups["newly_passing"].append(row)
        else:
            groups["coverage"].append(row)  # anything involving not_tested
    return groups


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("before", type=Path)
    ap.add_argument("after", type=Path)
    ap.add_argument("--fail-on-regression", action="store_true")
    args = ap.parse_args()

    a, b = load_run(args.before), load_run(args.after)
    shared = sorted(set(a) & set(b))
    print(f"# {args.before.name} -> {args.after.name}\n\n{len(shared)} calls in both runs.\n")
    g = diff({k: a[k] for k in shared}, {k: b[k] for k in shared})
    titles = {"newly_failing": "Newly failing", "newly_passing": "Newly passing", "coverage": "Coverage changed"}
    for name, rows in g.items():
        print(f"## {titles[name]} ({len(rows)})\n")
        if rows:
            print("| Call | Check | Severity | Before | After |\n|---|---|---|---|---|")
            for call, cid, sev, old, new in rows:
                print(f"| {call} | {cid} | {sev} | {old} | {new} |")
        print()

    labels = yaml.safe_load(LABELS.read_text())
    if set(c for c in labels["judge_fails"]) & set(shared):
        ra, rb = evaluate(a, labels), evaluate(b, labels)
        print("## Judge scores against human labels\n\n| | before | after |\n|---|---|---|")
        print(f"| Precision | {ra.precision:.0%} | {rb.precision:.0%} |")
        print(f"| Recall | {ra.recall():.0%} | {rb.recall():.0%} |")
        print(f"| Lenient passes | {len(ra.lenient)} | {len(rb.lenient)} |")
        print(f"| Unlabelled fails | {len(ra.unlabeled_fails)} | {len(rb.unlabeled_fails)} |")
    return 1 if args.fail_on_regression and g["newly_failing"] else 0


if __name__ == "__main__":
    sys.exit(main())
