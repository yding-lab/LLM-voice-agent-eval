"""The judge pipeline end to end, with stand-in models. No API keys, no network.

    python tests/test_judge_pipeline.py

Checks that a real call transcript is pseudonymized before it reaches the model, that the
v2 judge is told when the call happened, that the OpenAI and Bedrock providers build the
right requests and parse their replies, and that scripts/eval_judge.py scores the saved
baseline exactly as published in JUDGE_EVAL.md.
"""

import json
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import yaml  # noqa: E402

import analyze_calls  # noqa: E402
import eval_judge  # noqa: E402
from caller.llm_providers import BedrockProvider, OpenAIProvider, parse_json  # noqa: E402
from caller.redact import Redactor  # noqa: E402

CALL = ROOT / "calls" / "07-barge_in"
REPLY = {"summary": "ok", "pass_outcome_met": True, "checks": [{"id": "GEN-01", "result": "pass"}],
         "other_findings": []}


class FakeJudge:
    name = "fake:judge"

    def __init__(self):
        self.seen = None

    def complete_json(self, system, user):
        self.seen = (system, user)
        return json.loads(json.dumps(REPLY))


def check(name, cond):
    print(f"{'PASS' if cond else 'FAIL'}  {name}")
    return cond


def main() -> int:
    ok = True
    judge = FakeJudge()
    redactor = Redactor(["Sophia Williams", "Sophia", "Williams"])
    out = analyze_calls.analyze(judge, CALL, [], "v2", redactor)
    system, user = judge.seen
    ok &= check("v2 rubric sent", "Blame the right side" in system)
    ok &= check("call time given to the judge", user.startswith("Call placed: "))
    ok &= check("no raw date of birth reaches the model", "ninety" not in user.lower() and "[DOB_" in user)
    ok &= check("run metadata recorded", out["_meta"] == {"judge": "fake:judge", "rubric": "v2", "redacted": True})

    analyze_calls.analyze(judge, CALL, [], "v1", None)
    ok &= check("v1 rubric is the original (no clock, no new rules)",
                "Blame the right side" not in judge.seen[0] and not judge.seen[1].startswith("Call placed"))

    # OpenAI request shape
    calls = []
    fake_openai = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(
        create=lambda **kw: calls.append(kw) or SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(REPLY)))]))))
    r = OpenAIProvider("gpt-4.1", client=fake_openai).complete_json("sys", "user")
    ok &= check("openai: json mode, temperature 0",
                calls[0]["response_format"] == {"type": "json_object"} and calls[0]["temperature"] == 0 and r == REPLY)

    # Bedrock Converse request shape; reply wrapped in a code fence, as some models do
    sent = []

    class FakeBedrock:
        def converse(self, **kw):
            sent.append(kw)
            return {"output": {"message": {"content": [{"text": "Here you go:\n```json\n" + json.dumps(REPLY) + "\n```"}]}}}

    r = BedrockProvider("anthropic.claude-test", client=FakeBedrock()).complete_json("sys", "user")
    ok &= check("bedrock: converse request and fenced JSON parsed",
                sent[0]["modelId"] == "anthropic.claude-test"
                and sent[0]["messages"][0]["content"][0]["text"] == "user"
                and sent[0]["inferenceConfig"]["temperature"] == 0 and r == REPLY)
    ok &= check("parse_json tolerates a leading sentence", parse_json('Sure. {"a": 1}') == {"a": 1})

    # The published baseline numbers are reproducible from the saved run.
    labels = yaml.safe_load((ROOT / "evals" / "labels.yaml").read_text())
    rep = eval_judge.evaluate(eval_judge.load_run(ROOT / "evals" / "runs" / "baseline-v1-gpt-4.1"), labels)
    ok &= check(f"baseline: precision {rep.precision:.0%}, recall {rep.recall():.0%}, lenient {len(rep.lenient)}",
                round(rep.precision, 2) == 0.50 and round(rep.recall(), 2) == 0.56 and len(rep.lenient) == 3)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
