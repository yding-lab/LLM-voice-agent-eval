# Data audit: recordings, transcripts and secrets

This audit covers what is committed under `calls/`, `reports/`, `evals/` and `samples/`, plus the full git history (re-checked on 2026-10-30, 29 commits on the `portfolio-review` branch). It answers four questions: are the identities test-only, is there personal information, are there secrets, and is there permission to publish?

## Summary

| Question | Finding | Status |
|---|---|---|
| Test-only identities? | Yes. The callers are personas: "Sophia Williams" (a test account created for the line) and "Sarah Mitchell" (fictional console persona). Dates of birth are `07/04/2000` (a placeholder the agent assigns) and `August 20, 1992` (fictional, confirmed by the repository owner). No caller phone number is spoken | OK |
| Personal information? | No real person's data found. The only phone number in transcripts and metadata is the test line that was called. No emails. Clinic and doctor names are the demo agent's fictional ones | OK |
| Secrets? | None in the working tree or in history. The only key-shaped strings are the `.env.example` placeholder `ST_xxxxxxxxxxxx`. `.env` was never committed. Commit author emails were rewritten to the GitHub noreply address | OK |
| Permission to publish the recordings? | **Pending confirmation.** No written permission on file. See below | **Pending confirmation (待确认)** |

## Permission to publish

**Status: pending confirmation (待确认).** Nothing in this repository should be read as permission having been granted.

**Backup.** On 2026-10-30, before any clean-up, the complete repository (every commit and ref, as a git bundle) and a snapshot of `calls/`, `reports/` and `evals/` were backed up, restore-tested and checksummed. The backup is held privately by the owner, outside this repository.

The 11 calls were placed to a third-party company's demo voice agent, on a test line provided for a hiring assessment. The recordings contain that agent's synthesized voice and wording, and the company's name appears in its test-line message in calls 05 and 11. The assessment instructions asked candidates to keep the submission repository private. There is no written permission on file to publish these recordings or transcripts.

To keep the project usable regardless of the outcome:

- The **offline demo and the Docker image default to `samples/`**: three fictional calls written for this repository, with no third-party content (see `samples/README.md`).
- The recorded calls remain available as `--dataset recorded`, and they are the basis of the judge measurements in `JUDGE_EVAL.md`.

### What is public right now

Defaulting the demo to synthetic data does **not** resolve this. As of 2026-10-30 the repository is public and contains:

- `calls/*/recording.mp3` and `recording.ogg` (22 files, about 44 MB) and `transcript.txt`/`.json` for all 11 calls, on `main` and on this branch. They've been in the history since commit `43a915a`.
- The company's name, spoken by its test-line message in calls 05 and 11 (`transcript.txt` and `.json`), and its demo clinic's and doctors' names in transcripts, judge outputs (`evals/runs/`) and `reports/candidate_findings.md`. Public documents written for this repository (README, BUG_REPORT and the others) are anonymized; the evidence files are not edited.
- The test line's phone number in 24 places in call metadata and two early-failure notes. It's the company's test number, not a personal one, but it identifies the company.
- `reports/` excerpts quoting the agent's wording, and `BUG_REPORT.md`, which describes defects in the company's demo agent.

### Authorization records

Checked on 2026-10-30: the repository (all files and history), and a search of the owner's connected Gmail for correspondence with the company. **No written permission to publish was found.** The only related item is a note to self linking the challenge page. The assessment instructions asked for a private submission repository.

### Decisions the owner needs to make

1. **Is there any permission I haven't seen?** For example, terms on the challenge page, a message from the recruiter, or a reply to a question you asked. If so, add a link or a quote here.
2. **Which option below do you choose?** Option 1 needs a history rewrite and a force-push, which this audit deliberately doesn't do on its own.
3. **Is `BUG_REPORT.md` OK to keep public?** It names defects in another company's demo product. It can be kept even under option 1 if the company is anonymized (for example, "a demo AI receptionist").
4. **Should the company's name be redacted** from the two transcripts that contain it, independent of the audio?

Options for the owner, from most to least conservative:

1. **Remove** `calls/*/recording.*` and `calls/*/transcript.*` from the public repository *and its history* (for example with `git filter-repo`, then a force-push, and asking GitHub support to purge cached views). Keep the aggregate results, the labels and the judge outputs, and describe the calls in prose. Keep a private copy of the originals as evidence. The judge evaluation would no longer be reproducible by others, and the `recorded` demo would need the data supplied separately.
1b. **Remove the audio only.** Delete the recordings from the repo and its history, and keep pseudonymized transcripts with the company's name redacted. The judge numbers stay reproducible from the transcripts. The third party's wording is still published, so this still needs your judgement.
2. **Ask** the company for written permission to keep the recordings public, and link that permission here.
3. **Keep** them as they are, accepting that permission is undocumented and the company could ask for removal.

Making the repository private again is not an option this audit takes, because visibility is the owner's call.

## How the scan was done

```bash
# phone numbers, emails, URLs in transcripts and metadata
grep -rhoE '\+?1?[ (.-]?[0-9]{3}[) .-]?[0-9]{3}[ .-]?[0-9]{4}' calls reports
grep -rhoiE '[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[a-z]{2,}' calls reports
# names and dates of birth the caller gave
grep -rhoE "(this is|it's|name is|I'm) [A-Z][a-z]+( [A-Z][a-z]+)?" calls/*/transcript.txt
grep -rhoiE "(birth|birthday)[^.]{0,60}" calls/*/transcript.txt
# key-shaped strings across the entire history
git log -p --all | grep -E 'sk-(proj-)?[A-Za-z0-9_-]{20,}|AKIA[0-9A-Z]{16}|AC[0-9a-f]{32}|SK[0-9a-f]{32}|ST_[A-Za-z0-9]{10,}|eyJ[A-Za-z0-9_-]{30,}|BEGIN [A-Z ]*PRIVATE KEY'
git log --all --name-only --format= | sort -u | grep -E '(^|/)\.env$|\.pem$|\.key$|credentials'
```

The audio itself was not transcribed a second time. The audit relies on the committed transcripts, which were produced from the same audio by Deepgram.

## Ongoing safeguards

- Transcripts are pseudonymized (`evalkit/redact.py`) before they are sent to any judge model.
- `.gitignore` excludes `.env`, `.env.*` (except the example), key files, `logs/` and virtual environments.
- The bot only dials the one configured `TARGET_NUMBER` and refuses any other destination.
