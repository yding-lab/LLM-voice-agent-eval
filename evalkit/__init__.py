"""evalkit: the evaluation half of llm-voice-agent-eval.

One pipeline from a call transcript to a scored, tracked, comparable result:

    load call -> pseudonymize -> LLM judge (retries, timeouts, usage) -> save run + manifest
              -> score against human labels (splits, CIs, severity, error types, kappa)
              -> compare runs -> report

Entry points: ``python -m evalkit`` (CLI) and ``evalkit.api:app`` (FastAPI).
Nothing here depends on LiveKit or telephony, so it runs anywhere, including a container.
"""

__version__ = "0.3.0"
