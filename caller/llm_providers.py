"""A small provider layer for the offline LLM work (the QA judge), so the same prompts run on
OpenAI or on AWS Bedrock.

    ANALYSIS_PROVIDER=openai   ANALYSIS_MODEL=gpt-4.1                       (default)
    ANALYSIS_PROVIDER=bedrock  ANALYSIS_MODEL=anthropic.claude-sonnet-4-...  AWS_REGION=us-east-1

Bedrock uses the Converse API, which has one request shape for every model family on
Bedrock, and boto3's normal credential chain (env vars, ~/.aws, or an IAM role).
The live call path is unchanged: the voice pipeline still uses LiveKit's OpenAI plugin.
"""

from __future__ import annotations

import json
import os
import re
from typing import Protocol


class JSONCompleter(Protocol):
    name: str

    def complete_json(self, system: str, user: str) -> dict: ...


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
    def __init__(self, model: str, client=None) -> None:
        if client is None:
            from openai import OpenAI

            client = OpenAI()
        self.client, self.model = client, model
        self.name = f"openai:{model}"

    def complete_json(self, system: str, user: str) -> dict:
        res = self.client.chat.completions.create(
            model=self.model,
            temperature=0,
            response_format={"type": "json_object"},
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
        )
        return parse_json(res.choices[0].message.content)


class BedrockProvider:
    def __init__(self, model: str, region: str | None = None, client=None) -> None:
        if client is None:
            import boto3  # optional dependency: pip install boto3

            client = boto3.client("bedrock-runtime", region_name=region or os.getenv("AWS_REGION", "us-east-1"))
        self.client, self.model = client, model
        self.name = f"bedrock:{model}"

    def complete_json(self, system: str, user: str) -> dict:
        res = self.client.converse(
            modelId=self.model,
            system=[{"text": system + "\n\nRespond with the JSON object only."}],
            messages=[{"role": "user", "content": [{"text": user}]}],
            inferenceConfig={"temperature": 0, "maxTokens": 4096},
        )
        parts = res["output"]["message"]["content"]
        return parse_json("".join(p.get("text", "") for p in parts))


def from_env() -> JSONCompleter:
    provider = os.getenv("ANALYSIS_PROVIDER", "openai").lower()
    if provider == "openai":
        return OpenAIProvider(os.getenv("ANALYSIS_MODEL", "gpt-4.1"))
    if provider == "bedrock":
        model = os.getenv("ANALYSIS_MODEL")
        if not model:
            raise RuntimeError("Set ANALYSIS_MODEL to a Bedrock model id (or inference profile) for ANALYSIS_PROVIDER=bedrock")
        return BedrockProvider(model)
    raise RuntimeError(f"Unknown ANALYSIS_PROVIDER={provider!r}; use openai or bedrock")
