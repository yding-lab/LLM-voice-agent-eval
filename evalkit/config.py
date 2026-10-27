"""One place for every evaluation setting.

Precedence, lowest to highest: defaults -> evals/config.yaml -> environment -> explicit
overrides (CLI flags or API request). The resolved config is written into every run's
manifest, so a result can always be traced back to the exact settings that produced it.
"""

from __future__ import annotations

import os
from dataclasses import asdict, dataclass, fields
from pathlib import Path

import yaml

from .paths import CONFIG_FILE

ENV = {  # field -> environment variable
    "provider": "ANALYSIS_PROVIDER",
    "model": "ANALYSIS_MODEL",
    "rubric": "EVAL_RUBRIC",
    "redact": "EVAL_REDACT",
    "timeout_s": "EVAL_TIMEOUT_S",
    "max_retries": "EVAL_MAX_RETRIES",
    "replay_run": "EVAL_REPLAY_RUN",
    "aws_region": "AWS_REGION",
}


@dataclass
class EvalConfig:
    provider: str = "openai"  # openai | bedrock | replay
    model: str = "gpt-4.1"
    rubric: str = "v2"  # v1 | v2 (evalkit/rubrics.py)
    redact: bool = True  # pseudonymize transcripts before any model call
    timeout_s: float = 120.0  # per request
    max_retries: int = 3  # extra attempts after the first, with exponential backoff
    retry_backoff_s: float = 2.0
    replay_run: str = "baseline-v1-gpt-4.1"  # recorded run that the replay provider serves from
    aws_region: str = "us-east-1"

    @classmethod
    def load(cls, path: Path | None = CONFIG_FILE, **overrides) -> EvalConfig:
        values: dict = {}
        if path and Path(path).exists():
            values.update(yaml.safe_load(Path(path).read_text()) or {})
        for name, var in ENV.items():
            if os.getenv(var):
                values[name] = os.environ[var]
        values.update({k: v for k, v in overrides.items() if v is not None})
        cfg = cls()
        for f in fields(cls):
            if f.name in values:
                setattr(cfg, f.name, _coerce(values[f.name], type(getattr(cfg, f.name))))
        cfg.validate()
        return cfg

    def validate(self) -> None:
        from .rubrics import RUBRICS

        if self.provider not in ("openai", "bedrock", "replay"):
            raise ValueError(f"provider must be openai, bedrock or replay, not {self.provider!r}")
        if self.rubric not in RUBRICS:
            raise ValueError(f"rubric must be one of {sorted(RUBRICS)}, not {self.rubric!r}")
        if self.max_retries < 0 or self.timeout_s <= 0:
            raise ValueError("max_retries must be >= 0 and timeout_s > 0")

    def to_dict(self) -> dict:
        return asdict(self)


def _coerce(value, typ):
    if typ is bool and isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on")
    return typ(value)
