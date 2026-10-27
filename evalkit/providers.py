"""Model providers behind one interface: (system, user) -> JSON object + token usage.

    openai   OpenAI chat completions, JSON mode
    bedrock  AWS Bedrock Converse API (one request shape for every model family on Bedrock)
    replay   serves a recorded run's outputs by transcript hash: no key, no network, no cost.
             This is what the keyless demo and the Docker image use by default.

Retries and backoff live in evalkit.judge, not here, so every provider gets the same policy.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from .config import EvalConfig
from .paths import RUNS_DIR


@dataclass
class Completion:
    data: dict
    input_tokens: int | None = None
    output_tokens: int | None = None


class Provider(Protocol):
    name: str
    model: str

    def complete_json(self, system: str, user: str, *, key: str | None = None) -> Completion: ...


class ReplayMiss(LookupError):
    """The replay provider has no recorded output for this input."""


def parse_json(text: str) -> dict:
    """Models without a JSON mode sometimes wrap the object in ``` fences or add a sentence."""
    text = text.strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.S)
    if fenced:
        text = fenced.group(1)
    elif not text.startswith("{"):
        start, end = text.find("{"), text.rfind("}")
        if start != -1 and end > start:
            text = text[start : end + 1]
    return json.loads(text)


class OpenAIProvider:
    def __init__(self, model: str, timeout_s: float = 120.0, client=None) -> None:
        if client is None:
            from openai import OpenAI

            client = OpenAI(timeout=timeout_s, max_retries=0)  # evalkit.judge owns retries
        self.client, self.model, self.name = client, model, "openai"

    def complete_json(self, system: str, user: str, *, key: str | None = None) -> Completion:
        res = self.client.chat.completions.create(
            model=self.model,
            temperature=0,
            response_format={"type": "json_object"},
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
        )
        usage = getattr(res, "usage", None)
        return Completion(parse_json(res.choices[0].message.content),
                          getattr(usage, "prompt_tokens", None), getattr(usage, "completion_tokens", None))


class BedrockProvider:
    def __init__(self, model: str, region: str = "us-east-1", timeout_s: float = 120.0, client=None) -> None:
        if client is None:
            import boto3
            from botocore.config import Config

            client = boto3.client("bedrock-runtime", region_name=region,
                                  config=Config(read_timeout=timeout_s, retries={"max_attempts": 1}))
        self.client, self.model, self.name = client, model, "bedrock"

    def complete_json(self, system: str, user: str, *, key: str | None = None) -> Completion:
        res = self.client.converse(
            modelId=self.model,
            system=[{"text": system + "\n\nRespond with the JSON object only."}],
            messages=[{"role": "user", "content": [{"text": user}]}],
            inferenceConfig={"temperature": 0, "maxTokens": 4096},
        )
        text = "".join(p.get("text", "") for p in res["output"]["message"]["content"])
        usage = res.get("usage", {})
        return Completion(parse_json(text), usage.get("inputTokens"), usage.get("outputTokens"))


class ReplayProvider:
    """Answers from a recorded run, keyed by the SHA-256 of the raw transcript."""

    def __init__(self, run_dir: Path) -> None:
        from .runs import load_run

        outputs, manifest = load_run(run_dir)
        if not manifest:
            raise FileNotFoundError(f"replay needs a run with a manifest.json: {run_dir}")
        self._by_hash = {meta["input_sha"]: outputs[call]
                         for call, meta in manifest["calls"].items() if call in outputs}
        self.model = run_dir.name
        self.recorded_model = manifest["config"].get("model")
        self.recorded_rubric = manifest["config"].get("rubric")
        self.name = "replay"

    def complete_json(self, system: str, user: str, *, key: str | None = None) -> Completion:
        if key not in self._by_hash:
            raise ReplayMiss("replay mode only has recorded results for the bundled sample calls; "
                             "use provider openai or bedrock for new transcripts")
        return Completion(json.loads(json.dumps(self._by_hash[key])), 0, 0)

    def has(self, key: str) -> bool:
        return key in self._by_hash


def make_provider(cfg: EvalConfig) -> Provider:
    if cfg.provider == "openai":
        return OpenAIProvider(cfg.model, cfg.timeout_s)
    if cfg.provider == "bedrock":
        return BedrockProvider(cfg.model, cfg.aws_region, cfg.timeout_s)
    return ReplayProvider(RUNS_DIR / cfg.replay_run)
