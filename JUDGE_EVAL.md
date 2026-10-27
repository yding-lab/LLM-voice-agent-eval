# Evaluating the evaluator

The harness uses an LLM as a QA judge: `scripts/analyze_calls.py` grades every call against its numbered checks. An LLM judge is only useful if you know how often it is wrong, so I measured it against human ground truth before trusting any of its output.

## Method

- **Ground truth** is [`evals/labels.yaml`](evals/labels.yaml). It comes from the human review behind [BUG_REPORT.md](BUG_REPORT.md), where every candidate finding was checked against the transcript, the audio and the other calls. Each label carries a one-line reason that points back to that review.
- **Precision:** of the checks the judge marked `fail`, how many were real defects in the agent under test. A wrong fail is labelled either `caused_by_caller` (our own bot did it and the judge blamed the agent) or `not_adopted` (reviewed and not reported, e.g. unverifiable).
- **Recall:** of the 9 confirmed bugs, how many the judge flagged at all, as a failed check or as an "other finding" at a cited timestamp (±5 s).
- **Lenient passes:** checks that cover a confirmed bug but were graded `pass`.

Run it with `python scripts/eval_judge.py`. The baseline run is saved in [`evals/runs/baseline-v1-gpt-4.1/`](evals/runs/baseline-v1-gpt-4.1), and `tests/test_judge_pipeline.py` asserts that it still reproduces the numbers below.

## Baseline: judge v1 (gpt-4.1, 11 calls, 100 graded checks)

| Metric | Value |
|---|---|
| Precision | **50%** (7 of 14 failed checks were real agent defects) |
| Failed checks caused by our own caller bot | 5 of 14 |
| Recall on confirmed bugs | **56%** (5 of 9; 1 more partial) |
| Recall on Critical + High bugs | 67% (2 of 3) |
| Lenient passes | 3 |

| Bug | Severity | v1 judge |
|---|---|---|
| 1. Another patient's appointment disclosed and cancelled (shared phone number) | Critical | detected |
| 2. Cancelled first, then failed to rebook | High | detected |
| 3. A past appointment treated as upcoming | High | **missed** |
| 4. Closed-day handling | Medium | partial |
| 5. Date-of-birth corrections ignored | Medium | **missed** |
| 6. Refill claimed as started, then not routed | Medium | detected |
| 7. Drug-interaction reassurance | Medium | **missed** |
| 8. Same answer repeated | Low | detected |
| 9. Profile required for public info; name asked twice | Low | detected |

The judge caught the most important bug, but on its own it would have been a coin flip on every `fail` and would have missed a third of the real issues.

## What went wrong, and the fix for each

Every error fell into one of four patterns, and each one has a cause I can point to:

| Pattern | Evidence | Cause | Change in judge v2 |
|---|---|---|---|
| **Blamed the wrong side** | All 5 caller-caused fails: 3× talking over (`GEN-01`) in calls whose openings collided because *our* bot answered the recording notice, and 2 in call 01, which our bot hung up | The transcript marks `[cut off]` but not who started the overlap; the rubric said "don't blame the clinic for the bot" but gave no way to tell | Explicit attribution rule: a patient turn started into the clinic's pause is the bot's fault; nothing after a bot hang-up counts against the clinic |
| **No clock** | Bug 3 missed: an appointment 6 hours in the past was called "upcoming" | The judge was never told when the call took place | The prompt now starts with `Call placed: <weekday, date, time UTC>` and a rule to check "upcoming/today/soonest" against it |
| **Lenient grading** | `WKD-01` graded pass while quoting *"If you wanna move this to a Saturday…"*; `SEC-02` graded pass while quoting *"some people are fine"* | The model wrote a verdict first and a justification second | Ordered output: quote → `meets_fail_condition` (one sentence) → result, plus "if your own quote shows the failure, it's a fail" |
| **Coverage gap** | Bug 5 (DOB corrections ignored in 4 of 6 calls) was in no check | Nothing asked about it | A standing instruction to check whether every caller correction was applied |

Two changes went along with these:

- **Privacy.** Transcripts are now pseudonymized before they reach any model (`caller/redact.py`). Names, dates of birth, phone numbers and emails become stable tokens such as `[DOB_1]` and `[DOB_2]`. They're stable so that a correction stays visible (the agent kept `[DOB_1]` after the caller said `[DOB_2]`) without the values leaving the machine. Appointment dates are kept.
- **Portability.** The judge runs on OpenAI or AWS Bedrock through one interface (`caller/llm_providers.py`, the Bedrock Converse API), so the same rubric can be compared across models.

## Re-scoring a judge

v2 is the default rubric; v1 stays available (`--rubric v1`) so the two can be compared on the same calls. v2 changes four things at once, so a fair comparison also ablates them one at a time rather than reporting a single number. The workflow:

```bash
python scripts/analyze_calls.py --run-name judge-v2 --force                           # same 11 calls, new judge
python scripts/eval_judge.py --run evals/runs/judge-v2
python scripts/compare_runs.py evals/runs/baseline-v1-gpt-4.1 evals/runs/judge-v2      # what changed, check by check
ANALYSIS_PROVIDER=bedrock ANALYSIS_MODEL=<model id> python scripts/analyze_calls.py --run-name judge-v2-bedrock --force
```

Any new `fail` that isn't in `evals/labels.yaml` is listed as unlabelled. It gets reviewed and labelled before its run is scored, so the judge never grades itself.

## Limits

- **Small sample:** 11 calls, 100 graded checks, 9 confirmed bugs. One flipped label moves recall by 11 points. These numbers show direction, not a benchmark.
- **One reviewer.** A second labeller and an agreement score (Cohen's κ) would be the next step.
- **Recall is measured on known bugs only.** Passes weren't exhaustively re-reviewed, so bugs neither the judge nor the human found are invisible here.
- **v2 was designed from errors on these same calls.** Scoring it on them would check that the fixes work, not give an unbiased estimate. That needs new calls, which is why the baseline is the only number reported here.

## In CI

`.github/workflows/tests.yml` has an `eval-gate` job that scores the committed judge output and fails below 50% precision or 50% recall. A judge change that makes things worse can't merge silently.
