# Data audit: recordings, transcripts and secrets

This audit covers what is committed under `calls/`, `reports/`, `evals/` and `samples/`, plus the full git history (27 commits as of this audit). It answers four questions: are the identities test-only, is there personal information, are there secrets, and is there permission to publish?

## Summary

| Question | Finding | Status |
|---|---|---|
| Test-only identities? | Yes. The callers are personas: "Sophia Williams" (a test account created for the line) and "Sarah Mitchell" (fictional console persona). Dates of birth are `07/04/2000` (a placeholder the agent assigns) and `August 20, 1992` (fictional, confirmed by the repository owner). No caller phone number is spoken | OK |
| Personal information? | No real person's data found. The only phone number in transcripts and metadata is the test line that was called. No emails. Clinic and doctor names are the demo agent's fictional ones | OK |
| Secrets? | None in the working tree or in history. The only key-shaped strings are the `.env.example` placeholder `ST_xxxxxxxxxxxx`. `.env` was never committed. Commit author emails were rewritten to the GitHub noreply address | OK |
| Permission to publish the recordings? | **Not documented.** See below | **Needs the owner's decision** |

## Permission to publish

The 11 calls were placed to a third-party company's demo voice agent, on a test line provided for a hiring assessment. The recordings contain that agent's synthesized voice and wording, and the company's name appears in its test-line message in calls 05 and 11. The assessment instructions asked candidates to keep the submission repository private. There is no written permission on file to publish these recordings or transcripts.

To keep the project usable regardless of the outcome:

- The **offline demo and the Docker image default to `samples/`**: three fictional calls written for this repository, with no third-party content (see `samples/README.md`).
- The recorded calls remain available as `--dataset recorded`, and they are the basis of the judge measurements in `JUDGE_EVAL.md`.

Options for the owner, from most to least conservative:

1. **Remove** `calls/*/recording.*` and `calls/*/transcript.*` from the public repository *and its history* (for example with `git filter-repo`). Keep the aggregate results, the labels and the judge outputs, and describe the calls in prose. The judge evaluation would no longer be reproducible by others.
2. **Ask** the company for written permission to keep the recordings public, and link that permission here.
3. **Keep** them as they are, accepting that permission is undocumented.

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
