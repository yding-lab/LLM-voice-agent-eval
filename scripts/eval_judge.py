"""Measure the LLM judge against human ground truth (evals/labels.yaml).

    python scripts/eval_judge.py                                # judge output in calls/*/findings.json
    python scripts/eval_judge.py --run evals/runs/judge-v2      # a saved judge run
    python scripts/eval_judge.py --min-precision 0.5 --min-recall 0.5   # fail (exit 1) below thresholds

Reports three things:
  precision   of the checks the judge failed, how many were real defects in the agent under test
  recall      of the bugs a human confirmed, how many the judge flagged at all
  lenient     known-bug checks the judge marked "pass" anyway (often while quoting the bug)
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
LABELS = ROOT / "evals" / "labels.yaml"
CALLS = ROOT / "calls"
MATCH_WINDOW_S = 5  # an other_finding counts if it cites a time within this many seconds


def load_run(run: Path | None) -> dict[str, dict]:
    """{call name: judge output} from calls/*/findings.json or a run folder of <call>.json."""
    files = sorted(run.glob("*.json")) if run else sorted(CALLS.glob("*/findings.json"))
    return {(f.stem if run else f.parent.name): json.loads(f.read_text()) for f in files}


def seconds(ts: str) -> list[int]:
    return [int(m) * 60 + int(s) for m, s in re.findall(r"(\d{1,2}):(\d{2})", ts or "")]


def cites(finding_ts: str, wanted: list[str]) -> bool:
    got, want = seconds(finding_ts), [t for w in wanted for t in seconds(w)]
    if len(got) == 2 and "-" in (finding_ts or ""):  # a range like 01:07-01:45
        return any(got[0] - MATCH_WINDOW_S <= w <= got[1] + MATCH_WINDOW_S for w in want)
    return any(abs(g - w) <= MATCH_WINDOW_S for g in got for w in want)


@dataclass
class Report:
    fail_labels: Counter
    unlabeled_fails: list[str]
    bugs: list[dict]  # each: id, title, severity, status (detected / partial / missed), how
    lenient: list[str]

    @property
    def precision(self) -> float:
        labeled = sum(self.fail_labels.values())
        return self.fail_labels["confirmed"] / labeled if labeled else 0.0

    def recall(self, severities: set[str] | None = None) -> float:
        pool = [b for b in self.bugs if severities is None or b["severity"] in severities]
        return sum(b["status"] == "detected" for b in pool) / len(pool) if pool else 0.0


def evaluate(run: dict[str, dict], labels: dict) -> Report:
    fail_labels: Counter = Counter()
    unlabeled = []
    for call, out in run.items():
        for c in out.get("checks", []):
            if c.get("result") != "fail":
                continue
            label = labels.get("judge_fails", {}).get(call, {}).get(c["id"])
            if label:
                fail_labels[label["label"]] += 1
            else:
                unlabeled.append(f"{call} {c['id']}")

    bugs, lenient = [], []
    for bug in labels["known_bugs"]:
        status, how = "missed", []
        for d in bug["detected_by"]:
            out = run.get(d["call"])
            if not out:
                continue
            hit = False
            if "check" in d:
                c = next((c for c in out.get("checks", []) if c["id"] == d["check"]), None)
                if c and c.get("result") == "fail":
                    hit = True
                    how.append(f"{d['call'][:2]} {d['check']}")
                elif c and c.get("result") == "pass":
                    lenient.append(f"bug {bug['id']}: {d['call']} {d['check']} graded pass "
                                   f"(evidence: \"{(c.get('evidence') or '')[:90]}\")")
            else:
                for f in out.get("other_findings", []):
                    if cites(f.get("timestamp", ""), d["other_at"]):
                        hit = True
                        how.append(f"{d['call'][:2]} other @ {f.get('timestamp')}")
                        break
            if hit:
                status = "partial" if d.get("partial") and status == "missed" else "detected"
                if not d.get("partial"):
                    status = "detected"
        bugs.append({**{k: bug[k] for k in ("id", "title", "severity")}, "status": status, "how": how})
    return Report(fail_labels, unlabeled, bugs, lenient)


def to_markdown(r: Report, name: str) -> str:
    total = sum(r.fail_labels.values())
    det = Counter(b["status"] for b in r.bugs)
    lines = [
        f"# Judge evaluation: {name}",
        "",
        "| Metric | Value |",
        "|---|---|",
        f"| Precision (failed checks that were real agent defects) | **{r.precision:.0%}** ({r.fail_labels['confirmed']}/{total}) |",
        f"| Failed checks caused by our own caller bot | {r.fail_labels['caused_by_caller']}/{total} |",
        f"| Failed checks reviewed but not adopted | {r.fail_labels['not_adopted']}/{total} |",
        f"| Recall (confirmed bugs flagged) | **{r.recall():.0%}** ({det['detected']}/{len(r.bugs)}; {det['partial']} partial) |",
        f"| Recall on Critical + High bugs | {r.recall({'Critical', 'High'}):.0%} |",
        f"| Known-bug checks graded \"pass\" | {len(r.lenient)} |",
        "",
        "| Bug | Severity | Judge | How |",
        "|---|---|---|---|",
    ]
    for b in r.bugs:
        lines.append(f"| {b['id']}. {b['title']} | {b['severity']} | {b['status']} | {', '.join(b['how']) or '-'} |")
    if r.lenient:
        lines += ["", "Lenient grades (bug present, check passed):", *[f"- {x}" for x in r.lenient]]
    if r.unlabeled_fails:
        lines += ["", "Failed checks with no human label yet (add them to evals/labels.yaml):",
                  *[f"- {x}" for x in r.unlabeled_fails]]
    return "\n".join(lines) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--run", type=Path, help="folder of <call>.json judge outputs (default: calls/*/findings.json)")
    ap.add_argument("--out", type=Path, help="also write the report here")
    ap.add_argument("--min-precision", type=float)
    ap.add_argument("--min-recall", type=float)
    args = ap.parse_args()

    run = load_run(args.run)
    if not run:
        print("no judge output found", file=sys.stderr)
        return 1
    report = evaluate(run, yaml.safe_load(LABELS.read_text()))
    md = to_markdown(report, args.run.name if args.run else "calls/*/findings.json")
    print(md)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(md)
    ok = (args.min_precision is None or report.precision >= args.min_precision) and (
        args.min_recall is None or report.recall() >= args.min_recall)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
