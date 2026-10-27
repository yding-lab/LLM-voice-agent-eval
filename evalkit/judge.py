"""Judge one call: build the prompt, call the provider with retries, record what it cost."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone

from .config import EvalConfig
from .data import Call, common_checks
from .providers import Provider, ReplayMiss
from .redact import Redactor
from .rubrics import RUBRICS

log = logging.getLogger("evalkit.judge")


@dataclass
class Outcome:
    call: str
    input_sha: str
    status: str  # ok | error
    output: dict | None = None
    error: str | None = None
    attempts: int = 0
    latency_s: float | None = None  # wall time of the successful attempt
    input_tokens: int | None = None
    output_tokens: int | None = None
    redactions: dict = field(default_factory=dict)  # e.g. {"DOB": 2, "NAME": 1}


def build_prompt(call: Call, rubric: str, redactor: Redactor | None) -> tuple[str, str]:
    transcript = redactor.redact(call.transcript) if redactor else call.transcript
    sc = call.scenario
    checks = list(sc.get("checks", [])) + common_checks()
    checks_txt = "\n".join(f"{c['id']} [{c['severity']}]: FAIL IF {c['fail_if']}" for c in checks)
    when = ""
    if rubric != "v1" and call.started_at:
        when = f"Call placed: {datetime.fromtimestamp(call.started_at, timezone.utc):%A %Y-%m-%d %H:%M} UTC\n"
    user = (
        f"{when}Scenario: {sc.get('title', '')}\nPatient goal: {sc.get('goal', '')}\n"
        f"Correct behaviour: {sc.get('expected', '')}\nPassing outcome: {sc.get('pass_outcome', '')}\n\n"
        f"CHECKS\n{checks_txt}\n\nTRANSCRIPT\n{transcript}"
    )
    return RUBRICS[rubric], user


def judge_call(provider: Provider, call: Call, cfg: EvalConfig, names: list[str] = (),
               sleep=time.sleep) -> Outcome:
    redactor = Redactor(names) if cfg.redact else None  # fresh tokens per call
    system, user = build_prompt(call, cfg.rubric, redactor)
    out = Outcome(call=call.name, input_sha=call.input_sha, status="error",
                  redactions=redactor.counts if redactor else {})
    for attempt in range(1, cfg.max_retries + 2):
        out.attempts = attempt
        t0 = time.monotonic()
        try:
            res = provider.complete_json(system, user, key=call.input_sha)
        except ReplayMiss as e:  # not transient: retrying can't help
            out.error = str(e)
            log.warning("%s: %s", call.name, e)
            return out
        except Exception as e:  # network, rate limit, timeout, malformed JSON
            out.error = f"{type(e).__name__}: {e}"
            if attempt > cfg.max_retries:
                log.error("%s: giving up after %d attempts: %s", call.name, attempt, out.error)
                return out
            wait = cfg.retry_backoff_s * 2 ** (attempt - 1)
            log.warning("%s: attempt %d failed (%s); retrying in %.1fs", call.name, attempt, out.error, wait)
            sleep(wait)
            continue
        out.latency_s = round(time.monotonic() - t0, 3)
        out.input_tokens, out.output_tokens = res.input_tokens, res.output_tokens
        out.output, out.status, out.error = _attach_severity(res.data, call), "ok", None
        return out
    return out


def _attach_severity(result: dict, call: Call) -> dict:
    severity = {c["id"]: c["severity"] for c in list(call.scenario.get("checks", [])) + common_checks()}
    for c in result.get("checks", []):
        c.setdefault("severity", severity.get(c.get("id"), "?"))
    return result
