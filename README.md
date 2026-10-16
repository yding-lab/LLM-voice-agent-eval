# Patient Caller: a voice QA bot for a clinic's AI receptionist

A Python voice bot, built on LiveKit Agents in pipeline mode (Deepgram STT → OpenAI LLM → Cartesia TTS), that phones a clinic's AI receptionist as a simulated patient. It runs scripted-but-improvised scenarios (scheduling, refills, insurance, emergencies, privacy probes, interruptions) and saves a recording and a timestamped transcript of every call so bugs can be found and cited.

The bot dials exactly one number, `+1-805-439-8008`. The number is hard-coded in `caller/config.py`, and the agent refuses any other destination.

## Quick start

Requirements: Python 3.10+, a LiveKit Cloud project, a Twilio account with one phone number, and API keys for Deepgram, OpenAI and Cartesia.

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env              # fill in keys, Twilio trunk details, and the test patient
python -m caller.agent download-files   # VAD / turn-detector model weights (one time)
python scripts/setup_trunk.py     # registers the Twilio trunk with LiveKit; copy the printed id into .env
```

Run the calls (one command; it starts the worker, places calls one after another, and waits for each to finish):

```bash
python scripts/run_calls.py --all                    # every scenario, in order
python scripts/run_calls.py schedule_new_checkup     # one scenario
python scripts/run_calls.py --list                   # see scenario ids
```

Then do a first-pass review of the transcripts (optional):

```bash
python scripts/analyze_calls.py   # writes calls/*/findings.md and reports/candidate_findings.md
```

## What each call produces

`calls/NN-<scenario>/`

| File | Contents |
|---|---|
| `recording.mp3` | Mono mix of both sides. Listen to this one. |
| `recording.ogg` | Stereo Opus: left channel is the clinic agent, right channel is the patient bot. Same timeline. |
| `transcript.txt` | Both sides, with `[mm:ss]` timestamps that match the recording. Barge-ins, hangups and tool calls are marked. |
| `transcript.json` | The same turns in structured form, plus the bot's per-turn response latency. |
| `scenario.json` | The goal and the expected correct behaviour for this call. |
| `findings.md` / `.json` | Each check graded pass, fail or not tested, plus other candidate issues (if you ran the analyzer). These must be verified against the audio. |

## Telephony setup (Twilio → LiveKit SIP)

1. **Buy one Twilio number.** Use it for every call; it's the number that goes on the submission form.
2. **Create a SIP trunk.** In Twilio, go to Elastic SIP Trunking, create a trunk, and under Termination:
   - Set a termination SIP URI (for example `my-caller.pstn.twilio.com`). This value is `TWILIO_TERMINATION_URI`.
   - Under Authentication, add a credential list. Its username and password are `TWILIO_SIP_USERNAME` and `TWILIO_SIP_PASSWORD`.
3. **Attach the number.** Under the trunk's Phone Numbers, add your number. Put the same number in `CALLER_ID_NUMBER`.
4. **Register the trunk.** Run `python scripts/setup_trunk.py`, then put the printed `SIP_OUTBOUND_TRUNK_ID` in `.env`.

## Environment variables

All variables are listed with comments in [`.env.example`](.env.example). Secrets live only in `.env`, which is git-ignored.

| Variable | Purpose |
|---|---|
| `LIVEKIT_URL`, `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET` | LiveKit Cloud project: rooms, SIP, turn-detector inference |
| `DEEPGRAM_API_KEY` | Speech-to-text |
| `OPENAI_API_KEY` | Patient LLM and the transcript analyzer |
| `CARTESIA_API_KEY` | Text-to-speech |
| `TWILIO_*`, `CALLER_ID_NUMBER` | Only used by `setup_trunk.py` |
| `SIP_OUTBOUND_TRUNK_ID` | LiveKit outbound trunk used to dial |
| `PATIENT_FIRST_NAME`, `PATIENT_LAST_NAME`, `PATIENT_DOB`, `PATIENT_PHONE` | The test patient. Match the account you created on the clinic's signup page so lookups work. |
| `LLM_MODEL`, `STT_MODEL`, `TTS_MODEL`, `TTS_VOICE_ID`, `MIN/MAX_ENDPOINTING_DELAY` | Optional tuning |

## Project layout

```
caller/
  agent.py        LiveKit worker: dials, runs the patient session, records, hangs up
  persona.py      scenario loader + the patient system prompt
  scenarios.yaml  the test scenarios (goal, things to try, expected behaviour)
  transcript.py   timestamped transcript + latency capture
  audio.py        recording export (stereo OGG + mono MP3)
  config.py       env settings and the hard-coded target number
scripts/
  run_calls.py    single command to place calls
  setup_trunk.py  one-time Twilio -> LiveKit trunk registration
  analyze_calls.py LLM first-pass QA over transcripts
calls/            recordings and transcripts (committed)
ARCHITECTURE.md   how it works and why
BUG_REPORT.md     issues found in the clinic agent
ITERATION_LOG.md  what changed after listening to early calls
```

## Scenarios and bug detection criteria

`caller/scenarios.yaml` holds 14 scenarios. Each one has:
- a goal and a few things to try, which the bot sees;
- numbered failure conditions with a severity (for example `SCH-01`: confirms an appointment without checking availability, Critical), which the bot does not see;
- a one-line passing outcome.

Five common checks (`GEN-01` to `GEN-05`: talking over the caller, loops, self-contradiction, false "done" claims, dead air) apply to every call. That makes 55 checks in total.

`scripts/analyze_calls.py` grades every check as pass, fail or not tested, and writes `reports/check_matrix.md`. Confirm every fail by listening before it goes into `BUG_REPORT.md`, and cite it by check ID.

A scenario can also override the caller's `identity` (name, DOB, phone), give an `opening` line, and set a `tts_voice` so the voice matches the persona. `new_patient_booking` (Sarah Mitchell) does this. Otherwise the caller is the test account from `.env`.

To add a scenario, append an entry with an `id`, `goal`, `try` items, `expected`, `checks` and `pass_outcome`. Add an `interrupt` block to script a barge-in.

## Debugging tips

- **Rehearse without phone charges:** run `SCENARIO=refill_routine python -m caller.agent console`. You play the receptionist through your mic, and nothing is dialed. Use this to tune the persona before spending on real calls.
- `python -m caller.agent dev` runs the worker with debug logs. In another shell, run `python scripts/run_calls.py --no-worker <scenario>`.
- The worker log for batch runs is at `logs/worker.log`.
- If the bot talks over the clinic's pauses, raise `MIN_ENDPOINTING_DELAY` (for example to 0.9). If it feels sluggish, lower it.
