"""Versioned judge rubrics.

v1 is the rubric that graded the 11 recorded calls (evals/runs/baseline-v1-gpt-4.1). It is kept
byte-for-byte so that baseline stays reproducible. v2 adds one rule per failure pattern
measured on v1 (JUDGE_EVAL.md). A rubric's SHA-256 is stored in every run manifest, so an
edited rubric can never be mistaken for the version a result was produced with.
"""

from __future__ import annotations

import hashlib

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


def rubric_sha(name: str) -> str:
    return hashlib.sha256(RUBRICS[name].encode()).hexdigest()[:12]
