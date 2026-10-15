# Iteration log

These are changes made after listening to early calls. Each entry records what I heard, what I changed, and the result on the next calls.

| Round | Calls | What I heard | Change | Result |
|---|---|---|---|---|
| 0 | n/a | Baseline before any real call | Endpointing min delay 0.7 s, `hold_silently` tool, short-turn persona prompt | |
| 1 | 01-03 | | | |
| 2 | | | | |

Ideas to try if needed:
- The bot talks into the clinic's "let me check" pauses → raise `MIN_ENDPOINTING_DELAY` and strengthen the `hold_silently` guidance.
- The bot feels slow → lower the endpointing delay, or try a faster LLM via `LLM_MODEL`.
- The bot rambles or lists → tighten the turn-length rule and lower the temperature.
- Goodbye loops (both sides keep saying bye) → `end_call` after the first goodbye.
- The clinic mishears the DOB or phone → change how `PATIENT_DOB` / `PATIENT_PHONE` are phrased in `.env`.
