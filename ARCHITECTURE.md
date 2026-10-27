# Architecture

## How it works

`scripts/run_calls.py` starts a LiveKit Agents worker and, for each scenario, creates a LiveKit room and dispatches the caller agent into it, passing the scenario id in the job metadata. The agent starts its voice session first, then asks LiveKit SIP to dial the target through a Twilio Elastic SIP trunk (`CreateSIPParticipant` with `wait_until_answered`). When the call connects, the phone line is just another participant in the room.

The bot then runs a classic pipeline. Silero VAD and LiveKit's semantic turn detector decide when the agent under test has finished a turn. Deepgram Nova-3 transcribes it. GPT-4.1-mini, prompted as the scenario's patient, writes a short spoken reply, and Cartesia Sonic-3 voices it back down the line. The bot lets the other side greet first, steers toward its scenario goal, uses a `hold_silently` tool when it hears "one moment", and calls `end_call` after saying goodbye. A watchdog wraps up overlong calls.

Every turn is logged with its start time and the bot's end-to-end latency. LiveKit's session recorder writes both sides onto one timeline as stereo OGG, which is exported along with a mono MP3, so `[01:23]` in a transcript is 1:23 in the audio. Afterwards, `scripts/analyze_calls.py` runs an LLM QA pass over each transcript against the scenario's numbered checks to surface candidate bugs, which are then confirmed by listening.

The design is driven by the first thing anyone does with the output: listen to the calls. That made turn-taking the central problem. A caller that jumps into the receptionist's "let me check… okay, I have Tuesday at" pause sounds like a bot and derails the call. So I used a semantic end-of-turn model on top of VAD, with a minimum endpointing delay of about 0.7 s rather than the 0.5 s default. That trades roughly 200 ms of latency for far fewer talk-overs, because a patient who pauses slightly before answering sounds normal. I also gave the LLM an explicit way to stay silent, and kept persona turns to one or two sentences.

Latency is kept low everywhere else:
- Every stage streams.
- Preemptive generation starts the LLM on the interim transcript.
- The LLM is a small, fast model, since patient lines need little reasoning.
- Cartesia was chosen for its time to first audio (roughly 100 ms) and natural prosody on 8 kHz phone audio.
- Deepgram was chosen for low-latency streaming and accuracy on telephony audio, with smart formatting so dates and phone numbers come back readable.

Scenarios are data (YAML), not code. Each one gives the bot a goal and a few pressure tests to work in naturally, rather than a script. That keeps calls sounding like a real person while still steering into the edge cases: closed days, controlled-substance refills, made-up insurance plans, emergencies, third-party privacy requests, prompt injection and barge-in.

## Key decisions and alternatives

| Decision | Chosen | Alternatives considered | Why |
|---|---|---|---|
| Phone connectivity | LiveKit SIP + Twilio Elastic SIP trunk | Twilio Media Streams over WebSocket; Telnyx | LiveKit handles SIP, jitter and codecs natively. The call is just a room participant, so the same agent code runs in local rehearsal and on real calls. Twilio is the best-documented trunk; Telnyx is cheaper but adds setup risk |
| Pipeline vs. speech-to-speech | Separate STT → LLM → TTS | Realtime speech-to-speech models | Each stage can be inspected, swapped and tuned on its own, and the text in the middle is exactly what the QA analysis needs |
| STT | Deepgram Nova-3 (streaming) | Whisper / gpt-4o-transcribe, AssemblyAI | Lowest streaming latency of the options I've used, robust on 8 kHz audio, and keyterm boosting for the patient's name. Non-streaming Whisper would add a second or more per turn |
| LLM | GPT-4.1-mini | GPT-4.1, Claude Sonnet, Llama on Groq | Short in-character replies don't need a large model; time to first token matters more. Groq would be faster still, but tool-calling reliability (`end_call`, `hold_silently`) mattered more |
| TTS | Cartesia Sonic-3 | ElevenLabs Flash, OpenAI TTS | Fast first audio, natural voice, cheap. ElevenLabs is comparable in quality; OpenAI TTS was noticeably slower to first byte |
| Turn detection | Silero VAD + LiveKit semantic turn detector, 0.7 s min delay | VAD-only silence threshold; STT endpointing | Pure silence thresholds either interrupt mid-thought pauses or feel sluggish everywhere. The semantic model waits longer only when the sentence sounds unfinished |
| Interruptions | The other side may interrupt the bot (min 0.6 s of speech, false-interruption resume). A scripted barge-in in one scenario | Disable interruptions | Realistic behaviour, and it lets us test the agent's own barge-in handling deliberately rather than by accident |
| Recording | LiveKit session recorder (stereo, both sides on one timeline) | LiveKit Egress to S3; Twilio call recording | No extra storage or credentials, stays in sync with our turn timestamps, and per-side channels make analysis easy |
| Safety | One configured target number; the agent refuses any other destination | Free-form dialing | A bot that places real phone calls should never be one typo away from calling a stranger |

## Evaluation pipeline

Calling the agent is half the system. The other half decides what the calls mean, and that part has its own failure modes, so it's built to be measured:

1. **Pseudonymize** the transcript (`caller/redact.py`). This is healthcare-shaped data, and the judge doesn't need real values, only whether they changed. Values become stable tokens per call: `[DOB_1]`, then `[DOB_2]` after a correction.
2. **Judge** each call against its scenario's checks (`scripts/analyze_calls.py`). The rubric is versioned, and the provider is pluggable: OpenAI, or AWS Bedrock via the Converse API (`caller/llm_providers.py`). Each result records which judge, rubric and redaction setting produced it.
3. **Score the judge** against human labels (`scripts/eval_judge.py`, `evals/labels.yaml`): precision, recall on confirmed bugs, and "lenient" passes where the judge quoted the bug and still passed it.
4. **Diff runs** check by check (`scripts/compare_runs.py`), for a new judge on the same calls or a new agent version with the same judge. CI scores the committed judge output and fails below a floor.

| Decision | Chosen | Alternatives considered | Why |
|---|---|---|---|
| Grading | LLM judge with numbered, severity-tagged checks per scenario | Keyword/regex rules; free-form "rate this call 1–10" | Rules can't read intent ("did it actually defer to a pharmacist?"). A single score hides *which* behaviour broke. Numbered checks make every verdict citable and diffable |
| Trusting the judge | Measure it against human labels and publish the error rates | Spot-check a few outputs | Spot checks don't show a pattern. Measuring showed 5 of 7 wrong fails had one shared cause (blaming the wrong side), which is a fixable prompt problem rather than noise |
| Judge output order | Evidence → "does this meet the fail condition?" → verdict | Verdict first | Verdict-first produced passes whose own evidence showed the failure |
| PII handling | Stable pseudonymization before any model call | Send raw; drop all PII | Raw sends health-adjacent personal data to a third party. Dropping it entirely hides corrections, a real bug class here |
| Model provider | Thin interface over OpenAI and Bedrock | LangChain or a similar framework | Two providers and one call shape (system + user → JSON) don't justify a framework dependency. The interface is about 60 lines and easy to test with fakes |

## Measured results

From the first 11 live calls (110 measured bot turns):

- **Response latency** (other side stops speaking → bot audio starts): median **1.31 s**, p90 **1.73 s**, max 3.29 s. About 0.7 s of that is the deliberate endpointing wait; the rest is STT finalization, LLM time to first token and TTS first audio.
- **Call length:** 96–323 s, mean 2.8 min. Every call ended with the bot's own goodbye.
- **Turn-taking:** 33 of 113 bot turns were cut off by overlap, 30 of them in the first 35 s of a call. The cause, and the fix, are in [ITERATION_LOG.md](ITERATION_LOG.md) #7.
