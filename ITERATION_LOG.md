# Iteration log

What I saw or heard, what I changed, and what happened next. Infrastructure problems came first: they decided whether any call could happen at all. Conversation quality came second.

## Getting to a working call

| # | Symptom | Cause | Change | Result |
|---|---|---|---|---|
| 1 | `pip install` failed with `ResolutionImpossible` on the Intel Mac | `onnxruntime` (used by the VAD) has no Python 3.14 builds for macOS x86_64 | `setup.sh` picks Python 3.12/3.13 and never 3.14; dropped the unused `turn-detector` plugin extra | Clean install on Python 3.12 |
| 2 | Console rehearsal crashed: `Missing required environment variable: SIP_OUTBOUND_TRUNK_ID` | One config path for both local rehearsal and real calls | Split config into console mode (provider keys only, fictional default patient) and outbound mode (strict SIP + patient validation, refuses to start a worker otherwise) | Rehearsal works without any telephony setup |
| 3 | Every HTTPS call failed with `CERTIFICATE_VERIFY_FAILED` | python.org's macOS Python ships without CA certificates | Fall back to the `certifi` bundle when the system has none | Deepgram / Cartesia / LiveKit reachable |
| 4 | Console hung with `either token, or api_key and api_secret, must be set` | A `LIVEKIT_URL` without key/secret (e.g. the `.env.example` placeholder) crashes the LiveKit worker | Console mode ignores a half-filled LiveKit config, before `AgentServer()` reads it | Console starts with any `.env` |
| 5 | First real call: `SIP 403 No International Permission` ([evidence](reports/early_failures/call-01-sip-403.txt)) | Twilio voice geo-permissions blocked the destination | Enabled the destination in Twilio's voice geographic permissions | All later calls connected |
| 6 | Some calls ran with no recording or transcript, or never finished | Laptop went to sleep or ran out of battery mid-call | `run_calls.py` now checks every call (recording + transcript saved, ≥45 s, ≥3 turns each side) and `renumber_calls.py` drops failures before submission | Only full conversations are submitted |

## Conversation quality (from listening to calls 01–11)

| # | What I heard | Cause | Change | Result |
|---|---|---|---|---|
| 7 | Every call opens with both sides talking over each other: the bot starts in the pause after *"This call may be recorded…"*, the clinic starts its real greeting, both yield, repeat. Up to 7 collisions in the first 25 s (`calls/01` 00:12–00:35); 33 bot turns cut off across 11 calls | The recording notice is a complete sentence followed by a ~5 s pause, so the end-of-turn model correctly says "turn over" and the LLM answers it as if it were a greeting | `PatientAgent.llm_node` skips the reply while the bot hasn't spoken yet and the clinic's latest turn is only the recording notice (matched by `is_only_recording_notice`). Everything after the first reply is untouched. A first attempt used `on_user_turn_completed`, but that hook is skipped for typed input, so `console --text` could not test it; `llm_node` runs for every reply, spoken or typed | Notice classifier matches all 11 notices and 0 of the other 125 clinic lines in calls 01–11. `tests/test_recording_notice.py` (real LiveKit session, stand-in LLM): notice → silent, greeting → answered, notice + greeting in one turn → answered, notice mid-call → answered. Real-call result: *to fill in* |
| 8 | Bot ended the call 2 s after asking *"Can you repeat the date and time of my appointment?"* (`calls/01` 02:05–02:11), so the clinic never got to answer | The LLM emitted `end_call` in the same turn as a question, despite the prompt rule | *Planned:* `end_call` refuses (returns an instruction instead of hanging up) when the bot's last line was a question or had no goodbye | *To fill in* |
| 9 | 19-second call: the bot heard the cut-off greeting *"Thank you for call…"* as a goodbye and hung up ([evidence](reports/early_failures/call-08-bot-talked-over-greeting.txt)) | Same opening collision as #7, plus "thank you for calling" read as a farewell | Covered by #7; prompt also states that "thank you for calling" is a greeting | *To fill in* |
| 10 | Bot said goodbye and hung up while being transferred (`calls/10` 04:25–04:30) | Prompt says to wait on transfers, but the LLM treated "Transferring you now. Thank you." as the end | *Planned:* stronger hold-on-transfer rule | *To fill in* |

## Numbers (calls 01–11)

- Bot response latency (clinic stops speaking → bot starts): **median 1.31 s, p90 1.73 s**, max 3.29 s over 110 bot turns.
- Call length: 96–323 s, mean 2.8 min.
- Bot turns cut off by overlap: 33 of 113, and 30 of those 33 in the first 35 s of a call. That is item #7: the opening, not the conversation itself.

## Note on scenarios

Calls 01–11 were run with the first, primary-care version of `caller/scenarios.yaml` (annual physical, lisinopril, alprazolam). The scenarios were later rewritten for the orthopedic demo clinic, with the same ids and checks. Each call folder's `scenario.json` records exactly what that call used.
