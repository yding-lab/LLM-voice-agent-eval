"""Prompt building and the retry policy."""

from evalkit.config import EvalConfig
from evalkit.data import CALLS_DIR, load_call
from evalkit.judge import build_prompt, judge_call
from evalkit.providers import Completion, ReplayMiss
from evalkit.redact import Redactor

CALL = load_call(CALLS_DIR / "07-barge_in")
REPLY = {"summary": "ok", "pass_outcome_met": True, "checks": [{"id": "GEN-01", "result": "pass"}], "other_findings": []}


class Flaky:
    """Fails `failures` times, then answers."""
    name, model = "fake", "fake-1"

    def __init__(self, failures=0, exc=TimeoutError("read timed out")):
        self.failures, self.exc, self.calls, self.seen = failures, exc, 0, None

    def complete_json(self, system, user, *, key=None):
        self.calls += 1
        self.seen = (system, user)
        if self.calls <= self.failures:
            raise self.exc
        return Completion(dict(REPLY, checks=[dict(c) for c in REPLY["checks"]]), 1000, 200)


def test_v2_prompt_has_clock_and_no_raw_pii():
    system, user = build_prompt(CALL, "v2", Redactor(["Sophia Williams", "Sophia", "Williams"]))
    assert "Blame the right side" in system
    assert user.startswith("Call placed: ")
    assert "ninety" not in user.lower() and "[DOB_" in user


def test_v1_prompt_is_the_original():
    system, user = build_prompt(CALL, "v1", None)
    assert "Blame the right side" not in system and not user.startswith("Call placed")


def test_retries_with_exponential_backoff_then_succeeds():
    waits = []
    prov = Flaky(failures=2)
    out = judge_call(prov, CALL, EvalConfig(max_retries=3, retry_backoff_s=2.0), sleep=waits.append)
    assert out.status == "ok" and out.attempts == 3 and waits == [2.0, 4.0]
    assert (out.input_tokens, out.output_tokens) == (1000, 200)
    assert out.output["checks"][0]["severity"] == "Medium"  # GEN-01 severity attached from scenarios.yaml


def test_gives_up_after_max_retries_and_records_the_error():
    out = judge_call(Flaky(failures=10), CALL, EvalConfig(max_retries=2), sleep=lambda s: None)
    assert out.status == "error" and out.attempts == 3 and "TimeoutError" in out.error


def test_replay_miss_is_not_retried():
    prov = Flaky(failures=10, exc=ReplayMiss("no recording"))
    out = judge_call(prov, CALL, EvalConfig(max_retries=3), sleep=lambda s: None)
    assert out.status == "error" and prov.calls == 1
