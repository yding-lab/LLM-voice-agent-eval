# Evaluating the evaluator

The harness uses an LLM as a QA judge: `python -m evalkit run` grades every call against its numbered checks. An LLM judge is only useful if you know how often it is wrong, so I measured it against human ground truth before trusting any of its output.

## Method

- **Ground truth** is [`evals/labels/reviewer_a.yaml`](evals/labels/reviewer_a.yaml). It comes from the human review behind [BUG_REPORT.md](BUG_REPORT.md), where every candidate finding was checked against the transcript, the audio and the other calls. Each label carries a one-line reason that points back to that review.
- **Precision:** of the checks the judge marked `fail`, how many were real defects in the agent under test. A wrong fail is labelled either `caused_by_caller` (our own bot did it and the judge blamed the agent) or `not_adopted` (reviewed and not reported, e.g. unverifiable).
- **Recall:** of the 9 confirmed bugs, how many the judge flagged at all, as a failed check or as an "other finding" at a cited timestamp (±5 s).
- **Lenient passes:** checks that cover a confirmed bug but were graded `pass`.
- **Error types.** Wrong fails are split by label. Missed bugs are classed as `lenient_pass` (a check covering the bug was graded pass), `not_flagged` (nothing pointed at it) or `partial`.
- **Uncertainty.** Every rate has a 95% Wilson score interval. With n = 9 or 14, the normal approximation is unreliable, and the Wilson interval behaves well even at 0% or 100%.
- **Splits.** [`evals/splits.yaml`](evals/splits.yaml) puts the 11 recorded calls in `dev`. Any other call counts as `holdout` automatically, and each split is scored separately (see *Limits*).

Run it with `python -m evalkit score`. The baseline run, with its manifest, is saved in [`evals/runs/baseline-v1-gpt-4.1/`](evals/runs/baseline-v1-gpt-4.1). `tests/test_evalkit_scoring.py` asserts that it still reproduces every number below, including the intervals.

## Baseline: judge v1 (gpt-4.1, 11 calls, 100 graded checks)

| Metric | Value |
|---|---|
| Precision | **50%** (7 of 14 failed checks were real agent defects; 95% CI 27–73%) |
| Failed checks caused by our own caller bot | 5 of 14 |
| Failed checks reviewed but not adopted | 2 of 14 |
| Recall on confirmed bugs | **56%** (5 of 9; 95% CI 27–81%; 1 more partial) |
| Recall on Critical + High bugs | 67% (2 of 3; 95% CI 21–94%) |
| Missed bugs by type | 2 lenient pass, 2 not flagged |
| Lenient passes (checks) | 3 |

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

The judge caught the most important bug, but on its own it would have been a coin flip on every `fail` and would have missed a third of the real issues. The intervals are wide: with this little data, the honest claim is "substantially imperfect", not "exactly 50%".

## What went wrong, and the fix for each

Every error fell into one of four patterns, and each one has a cause I can point to:

| Pattern | Evidence | Cause | Change in judge v2 |
|---|---|---|---|
| **Blamed the wrong side** | All 5 caller-caused fails: 3× talking over (`GEN-01`) in calls whose openings collided because *our* bot answered the recording notice, and 2 in call 01, which our bot hung up | The transcript marks `[cut off]` but not who started the overlap; the rubric said "don't blame the clinic for the bot" but gave no way to tell | Explicit attribution rule: a patient turn started into the clinic's pause is the bot's fault; nothing after a bot hang-up counts against the clinic |
| **No clock** | Bug 3 missed: an appointment 6 hours in the past was called "upcoming" | The judge was never told when the call took place | The prompt now starts with `Call placed: <weekday, date, time UTC>` and a rule to check "upcoming/today/soonest" against it |
| **Lenient grading** | `WKD-01` graded pass while quoting *"If you wanna move this to a Saturday…"*; `SEC-02` graded pass while quoting *"some people are fine"* | The model wrote a verdict first and a justification second | Ordered output: quote → `meets_fail_condition` (one sentence) → result, plus "if your own quote shows the failure, it's a fail" |
| **Coverage gap** | Bug 5 (DOB corrections ignored in 4 of 6 calls) was in no check | Nothing asked about it | A standing instruction to check whether every caller correction was applied |

Two changes went along with these:

- **Privacy.** Transcripts are now pseudonymized before they reach any model (`evalkit/redact.py`). Names, dates of birth, phone numbers and emails become stable tokens such as `[DOB_1]` and `[DOB_2]`. They're stable so that a correction stays visible (the agent kept `[DOB_1]` after the caller said `[DOB_2]`) without the values leaving the machine. Appointment dates are kept.
- **Portability.** The judge runs on OpenAI or AWS Bedrock through one interface (`evalkit/providers.py`, the Bedrock Converse API), so the same rubric can be compared across models.

## Design calls vs. held-out calls

| | Calls | Used to design the judge? | What its numbers can claim |
|---|---|---|---|
| `dev` | The 11 recorded calls | Yes: rubric v2 was written from v1's errors on them | Whether v1 was good on these calls (it wasn't), and later whether v2 fixed what it was meant to fix. **Not** an unbiased estimate of v2 |
| `holdout` | Every call not listed in `evals/splits.yaml` | No, and must never be | Unbiased judge performance, once enough calls are labelled |

The holdout split is **empty today**. To fill it:

1. Place new calls (`python scripts/run_calls.py --all`). They land in `holdout` automatically.
2. Judge them with the frozen rubric (`python -m evalkit run --name holdout-v2`) and don't edit the rubric after looking at the output.
3. Label the failed checks and the bugs found by listening, independently of the judge's output (below).
4. Run `python -m evalkit score --run evals/runs/holdout-v2`. The holdout section is the result to report.

## Independent second review and adjudication

All labels today come from one reviewer (`evals/labels/reviewer_a.yaml`), so there's no measure yet of how reliable the labels themselves are. The process, supported end to end:

1. `python -m evalkit labels-template` writes a blank sheet listing every failed check to label.
2. A second person labels it from the transcripts and audio **without seeing reviewer_a.yaml**, and saves it as `evals/labels/reviewer_b.yaml`.
3. `python -m evalkit agreement` reports raw agreement and Cohen's κ (three-way, and on "real defect or not"), then lists each disagreement.
4. The two reviewers, or a third person, settle each disagreement in `evals/labels/adjudicated.yaml`. Once that file exists, scoring uses it as the truth automatically.

`tests/test_evalkit_review.py` exercises this flow with a simulated second reviewer, as a test of the code only. No second-reviewer data has been collected.

## Re-scoring a judge

v2 is the default rubric; v1 stays available (`--rubric v1`) so the two can be compared on the same calls. v2 changes four things at once, so a fair comparison also ablates them one at a time rather than reporting a single number. The workflow:

```bash
python -m evalkit run --name judge-v2                       # same 11 calls, new judge; tracked in evals/runs/judge-v2
python -m evalkit score --run evals/runs/judge-v2
python -m evalkit compare evals/runs/baseline-v1-gpt-4.1 evals/runs/judge-v2   # setup, scores, cost, check by check
python -m evalkit run --provider bedrock --model <model id> --name judge-v2-bedrock
```

Every run's `manifest.json` records the rubric hash, model, inputs, tokens, cost and latency, so a comparison shows exactly what changed. `python -m evalkit reproduce <run>` re-runs a tracked run with the settings in its manifest and prints the comparison. It warns if any transcript has changed since, because the differences would then mix input changes with judge changes. Any new `fail` with no label in `evals/labels/` is listed as unlabelled. It gets reviewed and labelled before its run is scored, so the judge never grades itself.

## Limits

- **Small sample:** 11 calls, 100 graded checks, 9 confirmed bugs. One flipped label moves recall by 11 points. These numbers show direction, not a benchmark.
- **One reviewer.** All labels come from one person. The tooling for a second reviewer is ready: `python -m evalkit labels-template` writes a blank sheet, and the second person fills it in without seeing `reviewer_a.yaml` and saves it as `evals/labels/reviewer_b.yaml`. `python -m evalkit agreement` then reports Cohen's κ (three-way, and on "real defect or not"). Until that exists, there is no measure of how reliable the labels themselves are.
- **Recall is measured on known bugs only.** Passes weren't exhaustively re-reviewed, so bugs neither the judge nor the human found are invisible here.
- **No held-out data yet.** v2 was designed from errors on the `dev` calls. Scoring it there checks that the fixes work, not how v2 generalizes. The `holdout` split exists and fills automatically as new calls are placed; it is empty today, which is why the v1 baseline is the only number reported here.

## In CI

`.github/workflows/tests.yml` has an `eval-gate` job that scores the newest committed judge run on `dev` and fails below 50% precision or 50% recall. A judge change that makes things worse can't merge silently.
