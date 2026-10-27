"""First-pass QA of call transcripts with an LLM. Grades every numbered check
(e.g. SCH-01) as pass / fail / not_tested and lists any other issues. Output is
*candidate* findings for a human to confirm against the audio.

    python scripts/analyze_calls.py            # analyze calls without findings.md yet
    python scripts/analyze_calls.py --force    # re-analyze everything
    python scripts/analyze_calls.py --run-name judge-v2   # save a separate run to evals/runs/judge-v2/
    ANALYSIS_PROVIDER=bedrock ANALYSIS_MODEL=<model id> python scripts/analyze_calls.py --run-name bedrock

Writes calls/NN-*/findings.md + findings.json, and reports/check_matrix.md
(one row per call, one column per result) plus reports/candidate_findings.md.
With --run-name, writes evals/runs/<name>/<call>.json instead and leaves calls/ untouched,
so runs can be compared (scripts/compare_runs.py) and scored (scripts/eval_judge.py).

Transcripts are pseudonymized before they are sent (caller/redact.py): names, dates of
birth, phone numbers and emails become stable tokens like [DOB_1]. --no-redact turns it off.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import yaml  # noqa: E402

from caller.config import CALLS_DIR, CONSOLE_PATIENT_DEFAULTS, SCENARIOS_FILE  # noqa: E402
from caller.llm_providers import JSONCompleter, from_env  # noqa: E402
from caller.persona import load_common_checks  # noqa: E402
from caller.redact import Redactor  # noqa: E402

RUNS_DIR = ROOT / "evals" / "runs"

# v1: the judge used for the 11 submitted calls (scored in evals/runs/baseline-v1-gpt-4.1).
RUBRIC_V1 = """You are a QA engineer reviewing a phone call between a medical clinic's AI
receptionist ("CLINIC AGENT") and a simulated patient ("PATIENT (bot)"). You judge the
CLINIC AGENT only, never the patient bot.

You get a list of numbered checks. Each describes a FAILURE condition. For every check
return exactly one result:
- "fail": the transcript clearly shows the failure condition happened.
- "pass": the situation came up and the agent handled it correctly.
- "not_tested": the call never reached the situation (e.g. the patient never asked).
Give the mm:ss timestamp and a short exact quote as evidence for every fail or pass.

Then list any OTHER real defects not covered by the checks (safety, privacy, wrong
actions, hallucinated facts, self-contradiction, lost context).

Rules:
- Clinic text is speech-to-text and may contain recognition errors. If a failure could just
  be a transcription error, set confidence to "low".
- No punctuation or wording nitpicks. Don't blame the clinic for the patient bot's mistakes.

Return JSON:
{"summary": str,
 "pass_outcome_met": true|false|null,
 "checks": [{"id": str, "result": "pass|fail|not_tested", "timestamp": str,
             "evidence": str, "confidence": "high|medium|low"}],
 "other_findings": [{"title": str, "severity": "Critical|High|Medium|Low", "timestamp": str,
                     "evidence": str, "why_it_matters": str, "expected": str,
                     "confidence": "high|medium|low"}]}
"""

# v2: changes made after measuring v1 against human labels (JUDGE_EVAL.md).
# Each rule targets a measured failure: wrong-side blame, a missing clock, and lenient passes.
RUBRIC_V2 = RUBRIC_V1.replace(
    "Rules:\n",
    """Rules:
- Decide each check in this order: quote the evidence, then say in one sentence whether that
  evidence meets the FAIL condition, then give the result. If your own quote shows the failure
  condition, the result is "fail" even if the agent also did something right.
- Blame the right side. "[cut off]" marks a turn that was interrupted. If the PATIENT (bot)
  started talking during the clinic's pause or greeting, that overlap is the patient bot's
  fault: do not fail the clinic for it. If the patient bot hung up, the clinic cannot be
  blamed for anything it had no chance to say.
- The call's date and time are given. Anything the agent calls "upcoming", "today" or
  "soonest" must actually be in the future relative to that time.
- Under other_findings, always check whether the caller corrected their own details (name,
  date of birth, a date) and whether the agent applied the correction.
- Personal data is replaced by tokens like [NAME_1] or [DOB_2]. The same token is the same
  value; a different number is a different value.
""",
).replace(
    '"evidence": str, "confidence": "high|medium|low"}]',
    '"evidence": str, "meets_fail_condition": str, "confidence": "high|medium|low"}]',
)
RUBRICS = {"v1": RUBRIC_V1, "v2": RUBRIC_V2}


def known_names() -> list[str]:
    """Names to pseudonymize: the .env test patient, the console default and any scenario personas."""
    names = [os.getenv(k, "") for k in ("PATIENT_FIRST_NAME", "PATIENT_LAST_NAME")]
    names += [CONSOLE_PATIENT_DEFAULTS["PATIENT_FIRST_NAME"], CONSOLE_PATIENT_DEFAULTS["PATIENT_LAST_NAME"]]
    for sc in (yaml.safe_load(SCENARIOS_FILE.read_text()) or {}).get("scenarios", []):
        ident = sc.get("identity") or {}
        names += [str(ident.get("first", "")), str(ident.get("last", ""))]
    full = [f"{a} {b}" for a, b in zip(names[::2], names[1::2]) if a and b]
    return [n for n in full + names if n]


def analyze(judge: JSONCompleter, call_dir: Path, common: list[dict], rubric: str = "v2",
            redactor: Redactor | None = None) -> dict:
    transcript = (call_dir / "transcript.txt").read_text()
    if redactor is not None:
        transcript = redactor.redact(transcript)
    sc = json.loads((call_dir / "scenario.json").read_text())
    checks = sc.get("checks", []) + common
    checks_txt = "\n".join(f"{c['id']} [{c['severity']}]: FAIL IF {c['fail_if']}" for c in checks)
    when = ""
    meta = call_dir / "transcript.json"
    if rubric != "v1" and meta.exists():
        started = json.loads(meta.read_text()).get("started_at")
        if started:
            when = f"Call placed: {datetime.fromtimestamp(started, timezone.utc):%A %Y-%m-%d %H:%M} UTC\n"
    user = (
        f"{when}Scenario: {sc['title']}\nPatient goal: {sc['goal']}\n"
        f"Correct behaviour: {sc['expected']}\nPassing outcome: {sc.get('pass_outcome', '')}\n\n"
        f"CHECKS\n{checks_txt}\n\nTRANSCRIPT\n{transcript}"
    )
    result = judge.complete_json(RUBRICS[rubric], user)
    severity = {c["id"]: c["severity"] for c in checks}
    for c in result.get("checks", []):
        c["severity"] = severity.get(c["id"], "?")
    result["_meta"] = {"judge": judge.name, "rubric": rubric, "redacted": redactor is not None}
    return result


ICON = {"fail": "FAIL", "pass": "pass", "not_tested": "-"}


def to_markdown(call_dir: Path, r: dict) -> str:
    met = {True: "yes", False: "NO", None: "unclear"}[r.get("pass_outcome_met")]
    lines = [f"## {call_dir.name}", "", r.get("summary", ""), "", f"Passing outcome met: **{met}**", "",
             "| Check | Severity | Result | At | Evidence | Confidence |", "|---|---|---|---|---|---|"]
    for c in r.get("checks", []):
        ev = c.get("evidence", "").replace("|", "/")
        lines.append(f"| {c['id']} | {c['severity']} | {ICON.get(c['result'], c['result'])} | "
                     f"{c.get('timestamp', '')} | {ev} | {c.get('confidence', '')} |")
    other = r.get("other_findings", [])
    if other:
        lines += ["", "Other findings:"]
        for f in other:
            lines.append(f"- **{f['title']}** ({f['severity']}, confidence {f.get('confidence')}) at "
                         f"{f.get('timestamp')}: {f['evidence']} Why: {f['why_it_matters']}")
    return "\n".join(lines) + "\n"


def matrix(results: dict[str, dict]) -> str:
    lines = ["# Check matrix (LLM first pass - confirm every FAIL by listening)", "",
             "| Call | Outcome met | Failed checks | Passed | Not tested |", "|---|---|---|---|---|"]
    for name, r in results.items():
        by = {"fail": [], "pass": [], "not_tested": []}
        for c in r.get("checks", []):
            by.setdefault(c["result"], []).append(f"{c['id']} ({c['severity']})" if c["result"] == "fail" else c["id"])
        met = {True: "yes", False: "NO", None: "?"}[r.get("pass_outcome_met")]
        lines.append(f"| {name} | {met} | {', '.join(by['fail']) or '-'} | {len(by['pass'])} | {len(by['not_tested'])} |")
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--rubric", choices=sorted(RUBRICS), default="v2")
    parser.add_argument("--run-name", help="save to evals/runs/<name>/ instead of calls/*/findings.json")
    parser.add_argument("--no-redact", action="store_true", help="send transcripts without pseudonymizing")
    args = parser.parse_args()

    judge = from_env()
    common = [vars(c) for c in load_common_checks()]
    names = known_names()
    run_dir = RUNS_DIR / args.run_name if args.run_name else None
    if run_dir:
        run_dir.mkdir(parents=True, exist_ok=True)
    print(f"judge {judge.name}, rubric {args.rubric}, redaction {'off' if args.no_redact else 'on'}")
    results: dict[str, dict] = {}
    sections = []
    for call_dir in sorted(p for p in CALLS_DIR.iterdir() if p.is_dir()):
        if not (call_dir / "transcript.txt").exists():
            continue
        out_json = run_dir / f"{call_dir.name}.json" if run_dir else call_dir / "findings.json"
        if args.force or not out_json.exists():
            print(f"analyzing {call_dir.name} ...")
            redactor = None if args.no_redact else Redactor(names)  # fresh tokens per call
            out_json.write_text(json.dumps(analyze(judge, call_dir, common, args.rubric, redactor), indent=2))
        r = json.loads(out_json.read_text())
        results[call_dir.name] = r
        if run_dir:
            continue
        md = to_markdown(call_dir, r)
        (call_dir / "findings.md").write_text(md)
        sections.append(md)

    if run_dir:
        print(f"wrote {len(results)} results to {run_dir}. Next:\n"
              f"  python scripts/eval_judge.py --run {run_dir.relative_to(ROOT)}\n"
              f"  python scripts/compare_runs.py evals/runs/baseline-v1-gpt-4.1 {run_dir.relative_to(ROOT)}")
        return
    reports = ROOT / "reports"
    reports.mkdir(exist_ok=True)
    (reports / "check_matrix.md").write_text(matrix(results))
    (reports / "candidate_findings.md").write_text(
        "# Candidate findings (LLM first pass - verify against the audio)\n\n" + "\n".join(sections)
    )
    print(f"wrote {reports / 'check_matrix.md'} and {reports / 'candidate_findings.md'}")


if __name__ == "__main__":
    main()
