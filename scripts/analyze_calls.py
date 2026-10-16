"""First-pass QA of call transcripts with an LLM. Grades every numbered check
(e.g. SCH-01) as pass / fail / not_tested and lists any other issues. Output is
*candidate* findings for a human to confirm against the audio.

    python scripts/analyze_calls.py            # analyze calls without findings.md yet
    python scripts/analyze_calls.py --force    # re-analyze everything

Writes calls/NN-*/findings.md + findings.json, and reports/check_matrix.md
(one row per call, one column per result) plus reports/candidate_findings.md.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from openai import OpenAI  # noqa: E402

from caller.config import CALLS_DIR  # noqa: E402
from caller.persona import load_common_checks  # noqa: E402

MODEL = os.getenv("ANALYSIS_MODEL", "gpt-4.1")

RUBRIC = """You are a QA engineer reviewing a phone call between a medical clinic's AI
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


def analyze(client: OpenAI, call_dir: Path, common: list[dict]) -> dict:
    transcript = (call_dir / "transcript.txt").read_text()
    sc = json.loads((call_dir / "scenario.json").read_text())
    checks = sc.get("checks", []) + common
    checks_txt = "\n".join(f"{c['id']} [{c['severity']}]: FAIL IF {c['fail_if']}" for c in checks)
    user = (
        f"Scenario: {sc['title']}\nPatient goal: {sc['goal']}\n"
        f"Correct behaviour: {sc['expected']}\nPassing outcome: {sc.get('pass_outcome', '')}\n\n"
        f"CHECKS\n{checks_txt}\n\nTRANSCRIPT\n{transcript}"
    )
    res = client.chat.completions.create(
        model=MODEL,
        temperature=0,
        response_format={"type": "json_object"},
        messages=[{"role": "system", "content": RUBRIC}, {"role": "user", "content": user}],
    )
    result = json.loads(res.choices[0].message.content)
    severity = {c["id"]: c["severity"] for c in checks}
    for c in result.get("checks", []):
        c["severity"] = severity.get(c["id"], "?")
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
    args = parser.parse_args()

    client = OpenAI()
    common = [vars(c) for c in load_common_checks()]
    results: dict[str, dict] = {}
    sections = []
    for call_dir in sorted(p for p in CALLS_DIR.iterdir() if p.is_dir()):
        if not (call_dir / "transcript.txt").exists():
            continue
        out_json = call_dir / "findings.json"
        if args.force or not out_json.exists():
            print(f"analyzing {call_dir.name} ...")
            out_json.write_text(json.dumps(analyze(client, call_dir, common), indent=2))
        r = json.loads(out_json.read_text())
        results[call_dir.name] = r
        md = to_markdown(call_dir, r)
        (call_dir / "findings.md").write_text(md)
        sections.append(md)

    reports = ROOT / "reports"
    reports.mkdir(exist_ok=True)
    (reports / "check_matrix.md").write_text(matrix(results))
    (reports / "candidate_findings.md").write_text(
        "# Candidate findings (LLM first pass - verify against the audio)\n\n" + "\n".join(sections)
    )
    print(f"wrote {reports / 'check_matrix.md'} and {reports / 'candidate_findings.md'}")


if __name__ == "__main__":
    main()
