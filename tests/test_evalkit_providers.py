"""Provider request shapes and parsing, with stand-in clients (no network)."""

import json
from types import SimpleNamespace

import pytest

from evalkit.paths import RUNS_DIR
from evalkit.providers import BedrockProvider, OpenAIProvider, ReplayMiss, ReplayProvider, parse_json

REPLY = {"summary": "ok", "pass_outcome_met": True, "checks": [], "other_findings": []}


def test_openai_uses_json_mode_temperature_zero_and_reports_usage():
    sent = []
    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(
        create=lambda **kw: sent.append(kw) or SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(REPLY)))],
            usage=SimpleNamespace(prompt_tokens=1200, completion_tokens=300)))))
    res = OpenAIProvider("gpt-4.1", client=client).complete_json("sys", "user")
    assert sent[0]["response_format"] == {"type": "json_object"} and sent[0]["temperature"] == 0
    assert res.data == REPLY and (res.input_tokens, res.output_tokens) == (1200, 300)


def test_bedrock_converse_request_and_fenced_reply():
    sent = []

    class Fake:
        def converse(self, **kw):
            sent.append(kw)
            return {"output": {"message": {"content": [{"text": "Here:\n```json\n" + json.dumps(REPLY) + "\n```"}]}},
                    "usage": {"inputTokens": 900, "outputTokens": 250}}

    res = BedrockProvider("anthropic.claude-test", client=Fake()).complete_json("sys", "user")
    assert sent[0]["modelId"] == "anthropic.claude-test"
    assert sent[0]["messages"][0]["content"][0]["text"] == "user"
    assert sent[0]["inferenceConfig"]["temperature"] == 0
    assert res.data == REPLY and (res.input_tokens, res.output_tokens) == (900, 250)


def test_parse_json_tolerates_a_leading_sentence():
    assert parse_json('Sure. {"a": 1}') == {"a": 1}


def test_replay_serves_recorded_output_by_transcript_hash_only():
    rp = ReplayProvider(RUNS_DIR / "baseline-v1-gpt-4.1")
    manifest = json.loads((RUNS_DIR / "baseline-v1-gpt-4.1" / "manifest.json").read_text())
    key = manifest["calls"]["02-new_patient_booking"]["input_sha"]
    assert rp.complete_json("s", "u", key=key).data["checks"]
    with pytest.raises(ReplayMiss):
        rp.complete_json("s", "u", key="not-a-recorded-transcript")
