"""Command line for the evaluation pipeline.

    python -m evalkit demo                         # offline: synthetic sample calls + hand-written fixture judge
    python -m evalkit demo --dataset recorded      # offline: the 11 real calls + their recorded judge output
    python -m evalkit run --name judge-v2          # judge every call with the configured model (needs a key)
    python -m evalkit run --provider bedrock --model <bedrock model id> --name v2-bedrock
    python -m evalkit reproduce baseline-v1-gpt-4.1   # re-run a tracked run with its recorded settings, then compare
    python -m evalkit score [--run DIR] [--min-precision 0.5 --min-recall 0.5]
    python -m evalkit compare BEFORE AFTER [--fail-on-regression]
    python -m evalkit runs [-v]                    # list tracked runs
    python -m evalkit labels-template              # blank sheet for an independent second reviewer
    python -m evalkit agreement                    # Cohen's kappa + the disagreements to adjudicate
    python -m evalkit serve [--port 8000]          # FastAPI service (docs at /docs)

--dataset recorded (default) uses calls/ and evals/; --dataset synthetic uses samples/.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import yaml

from .compare import compare, compare_md
from .config import EvalConfig
from .data import iter_calls
from .paths import get_dataset
from .pipeline import ResumeConflict, run_pipeline
from .report import run_md, score_md
from .runs import latest_run, list_runs, load_run
from .scoring import TRUTH, agreement, disagreements, load_labels, load_splits, score_by_split, truth_labels


def _cfg(args, **extra) -> EvalConfig:
    return EvalConfig.load(provider=getattr(args, "provider", None), model=getattr(args, "model", None),
                           rubric=getattr(args, "rubric", None), dataset=getattr(args, "dataset", None),
                           redact=False if getattr(args, "no_redact", False) else None, **extra)


def cmd_run(args) -> int:
    try:
        res = run_pipeline(_cfg(args), args.name, force=args.force)
    except ResumeConflict as e:
        print(f"refusing to resume: {e}", file=sys.stderr)
        return 2
    print((Path(res["run_dir"]) / "report.md").read_text())
    return 0 if res["manifest"]["totals"]["errors"] == 0 else 1


def cmd_demo(args) -> int:
    cfg = EvalConfig.load(provider="replay", dataset=args.dataset)
    with tempfile.TemporaryDirectory() as tmp:
        res = run_pipeline(cfg, f"demo-{cfg.dataset}", runs_dir=Path(tmp))
        print((Path(res["run_dir"]) / "report.md").read_text())
    if cfg.dataset == "synthetic":
        print("\nOffline demo on SYNTHETIC calls (samples/). The judge output is a hand-written fixture, not a "
              "model: these numbers show how scoring works, not how good any judge is. "
              "Real measurements: JUDGE_EVAL.md, or `python -m evalkit demo --dataset recorded`.")
    else:
        print("\nReplay of the recorded gpt-4.1 judge on the 11 real calls (no API key, no cost).")
    return 0


def _resolve_run(args) -> Path | None:
    ds = get_dataset(args.dataset)
    if args.run:
        return Path(args.run)
    return latest_run(ds.runs_dir)


def cmd_score(args) -> int:
    ds = get_dataset(args.dataset)
    run = _resolve_run(args)
    if not run:
        print("no tracked runs found", file=sys.stderr)
        return 1
    outputs, manifest = load_run(run)
    scores = score_by_split(outputs, truth_labels(ds.labels_dir), load_splits(ds.splits_file))
    md = score_md(scores, run.name, manifest)
    print(md)
    if args.out:
        Path(args.out).write_text(md)
    dev = scores.get("dev")
    ok = True
    if dev and dev.calls:
        if args.min_precision is not None and (dev.precision.value or 0) < args.min_precision:
            ok = False
        if args.min_recall is not None and (dev.recall().value or 0) < args.min_recall:
            ok = False
    return 0 if ok else 1


def cmd_compare(args) -> int:
    ds = get_dataset(args.dataset)
    r = compare(Path(args.before), Path(args.after), labels=truth_labels(ds.labels_dir),
                splits=load_splits(ds.splits_file))
    print(json.dumps(r, indent=2, default=str) if args.json else compare_md(r))
    return 1 if args.fail_on_regression and r["checks"]["newly_failing"] else 0


def cmd_reproduce(args) -> int:
    """Re-run a tracked run with the settings in its manifest, then compare the two."""
    ds = get_dataset(args.dataset)
    src = Path(args.run) if Path(args.run).exists() else ds.runs_dir / args.run
    _, manifest = load_run(src)
    if not manifest:
        print(f"{src} has no manifest.json", file=sys.stderr)
        return 1
    c = manifest["config"]
    if c.get("provider") not in ("openai", "bedrock"):
        print(f"{src.name} was produced by provider {c.get('provider')!r}, which can't be re-run. "
              "Only openai and bedrock runs are reproducible.", file=sys.stderr)
        return 1
    current = {call.name: call.input_sha for call in iter_calls(ds.calls_dir)}
    drift = [name for name, meta in manifest["calls"].items() if current.get(name) != meta.get("input_sha")]
    if drift:
        print(f"WARNING: {len(drift)} transcript(s) changed since {src.name}: {', '.join(drift)}. "
              "Differences will mix input and judge changes.", file=sys.stderr)
    cfg = EvalConfig.load(**{k: c[k] for k in ("provider", "model", "rubric", "redact", "timeout_s",
                                               "max_retries", "aws_region") if c.get(k) is not None},
                          dataset=ds.name)
    name = args.name or f"{src.name}-repro-{datetime.now(timezone.utc):%Y%m%d-%H%M}"
    res = run_pipeline(cfg, name)
    print(compare_md(compare(src, Path(res["run_dir"]), labels=truth_labels(ds.labels_dir),
                             splits=load_splits(ds.splits_file))))
    return 0 if res["manifest"]["totals"]["errors"] == 0 else 1


def cmd_runs(args) -> int:
    ds = get_dataset(args.dataset)
    for r in list_runs(ds.runs_dir):
        print(f"{r['run']:<40} {r['provider']}:{r['model']:<22} rubric {r['rubric']}  "
              f"{r['calls']} calls  cost {r['cost_usd'] if r['cost_usd'] is not None else 'n/a'}")
        if args.verbose:
            print(run_md(load_run(ds.runs_dir / r["run"])[1]), "\n")
    return 0


def cmd_agreement(args) -> int:
    ds = get_dataset(args.dataset)
    rows = agreement(ds.labels_dir)
    if not rows:
        print("Only one reviewer so far. Run `python -m evalkit labels-template`, have a second person fill it "
              "in WITHOUT looking at reviewer_a.yaml, and save it as evals/labels/reviewer_b.yaml.")
        return 0
    for r in rows:
        if not r["items"]:
            print(f"{r['reviewer']}: no shared labelled items yet")
            continue
        print(f"{r['reviewer']}: {r['items']} shared items, raw agreement {r['raw_agreement']:.0%}, "
              f"kappa (3 labels) {r['kappa_3way']:.2f}, kappa (real defect?) {r['kappa_real_defect']:.2f}")
    todo = disagreements(ds.labels_dir)
    if todo:
        print(f"\n{len(todo)} disagreement(s) to adjudicate. Record the decisions in "
              f"{ds.labels_dir / 'adjudicated.yaml'} (same format as reviewer_a.yaml); scoring then uses it as truth:")
        for d in todo:
            print("  " + ", ".join(f"{k}={v}" for k, v in d.items()))
    return 0


def cmd_labels_template(args) -> int:
    ds = get_dataset(args.dataset)
    truth = load_labels(TRUTH, ds.labels_dir)
    sheet = {"reviewer": "<your name>",
             "instructions": ("Label each failed check WITHOUT looking at reviewer_a.yaml: confirmed (a real "
                              "defect in the agent), caused_by_caller (our bot did it), or not_adopted "
                              "(not a defect, or unverifiable). Use calls/<call>/transcript.txt and the audio. "
                              "Save as reviewer_<letter>.yaml next to reviewer_a.yaml."),
             "judge_fails": {call: {cid: {"label": None, "reason": None} for cid in checks}
                             for call, checks in truth.get("judge_fails", {}).items()}}
    out = ds.labels_dir / "templates" / "reviewer_template.yaml"
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
    ds = dict(choices=["recorded", "synthetic"], help="which calls/labels/runs to use (default: recorded)")

    p = sub.add_parser("run", help="judge calls and save a tracked run")
    p.add_argument("--name")
    p.add_argument("--dataset", **ds)
    p.add_argument("--provider", choices=["openai", "bedrock", "replay"])
    p.add_argument("--model")
    p.add_argument("--rubric", choices=["v1", "v2"])
    p.add_argument("--no-redact", action="store_true")
    p.add_argument("--force", action="store_true", help="re-judge every call, even ones with verified saved output (same settings only)")
    p.set_defaults(fn=cmd_run)

    p = sub.add_parser("demo", help="offline demo (no key, no cost)")
    p.add_argument("--dataset", default="synthetic", choices=["recorded", "synthetic"])
    p.set_defaults(fn=cmd_demo)

    p = sub.add_parser("reproduce", help="re-run a tracked run with its recorded settings and compare")
    p.add_argument("run", help="run name (under the dataset's runs dir) or path")
    p.add_argument("--name")
    p.add_argument("--dataset", **ds)
    p.set_defaults(fn=cmd_reproduce)

    p = sub.add_parser("score", help="score a run against human labels")
    p.add_argument("--run")
    p.add_argument("--dataset", **ds)
    p.add_argument("--out")
    p.add_argument("--min-precision", type=float)
    p.add_argument("--min-recall", type=float)
    p.set_defaults(fn=cmd_score)

    p = sub.add_parser("compare", help="compare two runs")
    p.add_argument("before")
    p.add_argument("after")
    p.add_argument("--dataset", **ds)
    p.add_argument("--json", action="store_true")
    p.add_argument("--fail-on-regression", action="store_true")
    p.set_defaults(fn=cmd_compare)

    p = sub.add_parser("runs", help="list tracked runs")
    p.add_argument("--dataset", **ds)
    p.add_argument("-v", "--verbose", action="store_true")
    p.set_defaults(fn=cmd_runs)

    p = sub.add_parser("agreement", help="inter-rater agreement and disagreements to adjudicate")
    p.add_argument("--dataset", **ds)
    p.set_defaults(fn=cmd_agreement)

    p = sub.add_parser("labels-template", help="blank labelling sheet for an independent reviewer")
    p.add_argument("--dataset", **ds)
    p.set_defaults(fn=cmd_labels_template)

    p = sub.add_parser("serve", help="run the API")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8000)
    p.set_defaults(fn=cmd_serve)

    args = ap.parse_args(argv)
    return args.fn(args)
