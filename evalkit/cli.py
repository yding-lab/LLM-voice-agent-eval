"""Command line for the evaluation pipeline.

    python -m evalkit demo                         # keyless: replay the recorded judge, score it, print the report
    python -m evalkit run --name judge-v2          # judge every call with the configured model (needs a key)
    python -m evalkit run --provider bedrock --model <bedrock model id> --name v2-bedrock
    python -m evalkit score [--run DIR] [--min-precision 0.5 --min-recall 0.5]
    python -m evalkit compare BEFORE AFTER [--fail-on-regression]
    python -m evalkit runs                         # list tracked runs
    python -m evalkit agreement                    # Cohen's kappa between human reviewers
    python -m evalkit labels-template              # blank sheet for a second reviewer
    python -m evalkit serve [--port 8000]          # FastAPI service (docs at /docs)
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import tempfile
from pathlib import Path

import yaml

from .compare import compare, compare_md
from .config import EvalConfig
from .paths import LABELS_DIR, RUNS_DIR
from .pipeline import run_pipeline
from .report import run_md, score_md
from .runs import latest_run, list_runs, load_run
from .scoring import TRUTH, agreement, load_labels, score_by_split


def _cfg(args) -> EvalConfig:
    return EvalConfig.load(provider=getattr(args, "provider", None), model=getattr(args, "model", None),
                           rubric=getattr(args, "rubric", None),
                           redact=False if getattr(args, "no_redact", False) else None)


def cmd_run(args) -> int:
    res = run_pipeline(_cfg(args), args.name, force=args.force)
    print((Path(res["run_dir"]) / "report.md").read_text())
    return 0 if res["manifest"]["totals"]["errors"] == 0 else 1


def cmd_demo(args) -> int:
    cfg = EvalConfig.load(provider="replay")
    with tempfile.TemporaryDirectory() as tmp:
        res = run_pipeline(cfg, "demo-replay", runs_dir=Path(tmp))
        print((Path(res["run_dir"]) / "report.md").read_text())
    print("\nThis was a replay of the recorded judge (no API key, no cost). "
          "Run `python -m evalkit run` with OPENAI_API_KEY set to judge for real.")
    return 0


def cmd_score(args) -> int:
    run = Path(args.run) if args.run else latest_run()
    if not run:
        print("no tracked runs found", file=sys.stderr)
        return 1
    outputs, manifest = load_run(run)
    scores = score_by_split(outputs)
    print(score_md(scores, run.name, manifest))
    if args.out:
        Path(args.out).write_text(score_md(scores, run.name, manifest))
    dev = scores.get("dev")
    ok = True
    if dev and dev.calls:
        if args.min_precision is not None and (dev.precision.value or 0) < args.min_precision:
            ok = False
        if args.min_recall is not None and (dev.recall().value or 0) < args.min_recall:
            ok = False
    return 0 if ok else 1


def cmd_compare(args) -> int:
    r = compare(Path(args.before), Path(args.after))
    print(json.dumps(r, indent=2, default=str) if args.json else compare_md(r))
    return 1 if args.fail_on_regression and r["checks"]["newly_failing"] else 0


def cmd_runs(args) -> int:
    for r in list_runs():
        print(f"{r['run']:<40} {r['provider']}:{r['model']:<22} rubric {r['rubric']}  "
              f"{r['calls']} calls  cost {r['cost_usd'] if r['cost_usd'] is not None else 'n/a'}")
        if args.verbose:
            print(run_md(load_run(RUNS_DIR / r["run"])[1]), "\n")
    return 0


def cmd_agreement(args) -> int:
    rows = agreement()
    if not rows:
        print("Only one reviewer so far. Run `python -m evalkit labels-template`, have a second person "
              "fill it in without looking at reviewer_a.yaml, save it as evals/labels/reviewer_b.yaml.")
        return 0
    for r in rows:
        k3, kb = r["kappa_3way"], r["kappa_real_defect"]
        print(f"{r['reviewer']}: {r['items']} shared items, raw agreement "
              f"{r['raw_agreement']:.0%}, kappa (3 labels) {k3:.2f}, kappa (real defect?) {kb:.2f}"
              if r["items"] else f"{r['reviewer']}: no shared labelled items yet")
    return 0


def cmd_labels_template(args) -> int:
    truth = load_labels(TRUTH)
    sheet = {"reviewer": "<your name>",
             "instructions": ("Label each failed check WITHOUT looking at reviewer_a.yaml: confirmed (a real "
                              "defect in the agent), caused_by_caller (our bot did it), or not_adopted "
                              "(not a defect, or unverifiable). Use calls/<call>/transcript.txt and the audio."),
             "judge_fails": {call: {cid: {"label": None, "reason": None} for cid in checks}
                             for call, checks in truth.get("judge_fails", {}).items()}}
    out = LABELS_DIR / "templates" / "reviewer_template.yaml"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(yaml.safe_dump(sheet, sort_keys=False, width=100))
    print(f"wrote {out}")
    return 0


def cmd_serve(args) -> int:
    import uvicorn

    uvicorn.run("evalkit.api:app", host=args.host, port=args.port)
    return 0


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    ap = argparse.ArgumentParser(prog="python -m evalkit", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("run", help="judge calls and save a tracked run")
    p.add_argument("--name")
    p.add_argument("--provider", choices=["openai", "bedrock", "replay"])
    p.add_argument("--model")
    p.add_argument("--rubric", choices=["v1", "v2"])
    p.add_argument("--no-redact", action="store_true")
    p.add_argument("--force", action="store_true", help="re-judge calls that already have output")
    p.set_defaults(fn=cmd_run)

    sub.add_parser("demo", help="keyless replay of the recorded judge").set_defaults(fn=cmd_demo)

    p = sub.add_parser("score", help="score a run against human labels")
    p.add_argument("--run")
    p.add_argument("--out")
    p.add_argument("--min-precision", type=float)
    p.add_argument("--min-recall", type=float)
    p.set_defaults(fn=cmd_score)

    p = sub.add_parser("compare", help="compare two runs")
    p.add_argument("before")
    p.add_argument("after")
    p.add_argument("--json", action="store_true")
    p.add_argument("--fail-on-regression", action="store_true")
    p.set_defaults(fn=cmd_compare)

    p = sub.add_parser("runs", help="list tracked runs")
    p.add_argument("-v", "--verbose", action="store_true")
    p.set_defaults(fn=cmd_runs)

    sub.add_parser("agreement", help="inter-rater agreement").set_defaults(fn=cmd_agreement)
    sub.add_parser("labels-template", help="blank labelling sheet").set_defaults(fn=cmd_labels_template)

    p = sub.add_parser("serve", help="run the API")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8000)
    p.set_defaults(fn=cmd_serve)

    args = ap.parse_args(argv)
    return args.fn(args)
