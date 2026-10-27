"""Calls, scenarios and checks: the inputs to the judge."""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from .paths import CALLS_DIR, SCENARIOS_FILE

# Fictional console persona from caller/config.py; kept here so evalkit needs no LiveKit imports.
CONSOLE_NAMES = ("Sarah", "Mitchell")


def sha256(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


@dataclass
class Call:
    name: str
    transcript: str
    scenario: dict  # title, goal, expected, pass_outcome, checks[{id, fail_if, severity}]
    started_at: float | None = None  # unix time; given to rubric v2 as the call's clock
    meta: dict = field(default_factory=dict)

    @property
    def input_sha(self) -> str:
        return sha256(self.transcript)


def load_call(call_dir: Path) -> Call:
    meta = {}
    if (call_dir / "transcript.json").exists():
        meta = json.loads((call_dir / "transcript.json").read_text())
    return Call(
        name=call_dir.name,
        transcript=(call_dir / "transcript.txt").read_text(),
        scenario=json.loads((call_dir / "scenario.json").read_text()),
        started_at=meta.get("started_at"),
        meta={k: meta.get(k) for k in ("duration_s", "end_reason", "latency") if k in meta},
    )


def iter_calls(calls_dir: Path = CALLS_DIR, only: list[str] | None = None) -> list[Call]:
    dirs = sorted(p for p in calls_dir.iterdir()
                  if p.is_dir() and (p / "transcript.txt").exists() and (p / "scenario.json").exists())
    return [load_call(p) for p in dirs if not only or p.name in only]


def _scenario_file() -> dict:
    return yaml.safe_load(SCENARIOS_FILE.read_text()) or {}


def common_checks() -> list[dict]:
    return [dict(c) for c in _scenario_file().get("common_checks", [])]


def scenarios() -> dict[str, dict]:
    return {s["id"]: s for s in _scenario_file().get("scenarios", [])}


def scenario_set_sha() -> str:
    return sha256(SCENARIOS_FILE.read_text())[:12]


def known_names() -> list[str]:
    """Names to pseudonymize: the .env test patient, the console persona and scenario personas."""
    names = [os.getenv("PATIENT_FIRST_NAME", ""), os.getenv("PATIENT_LAST_NAME", ""), *CONSOLE_NAMES]
    for sc in scenarios().values():
        ident = sc.get("identity") or {}
        names += [str(ident.get("first", "")), str(ident.get("last", ""))]
    full = [f"{a} {b}" for a, b in zip(names[::2], names[1::2]) if a and b]
    return [n for n in full + names if n]
