"""Central configuration. Everything comes from the environment (.env), with
sane defaults so a fresh clone only needs API keys + trunk id."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

# The ONLY number this bot is ever allowed to dial. Hard-coded on purpose:
# the agent refuses to place a call anywhere else, whatever the job metadata says.
TARGET_NUMBER = "+18054398008"

AGENT_NAME = "patient-caller"
CALLS_DIR = ROOT / "calls"
SCENARIOS_FILE = ROOT / "caller" / "scenarios.yaml"


def _env(name: str, default: str | None = None) -> str:
    value = os.getenv(name, default)
    if value is None or value == "":
        raise RuntimeError(f"Missing required environment variable: {name} (see .env.example)")
    return value


@dataclass(frozen=True)
class Settings:
    sip_trunk_id: str
    llm_model: str
    stt_model: str
    tts_model: str
    tts_voice: str
    patient_first_name: str
    patient_last_name: str
    patient_dob: str  # spoken form, e.g. "March fourth, nineteen eighty-five"
    patient_phone: str
    min_endpointing_delay: float
    max_endpointing_delay: float


def load_settings() -> Settings:
    return Settings(
        sip_trunk_id=_env("SIP_OUTBOUND_TRUNK_ID"),
        llm_model=os.getenv("LLM_MODEL", "gpt-4.1-mini"),
        stt_model=os.getenv("STT_MODEL", "nova-3"),
        tts_model=os.getenv("TTS_MODEL", "sonic-3"),
        tts_voice=os.getenv("TTS_VOICE_ID", "f786b574-daa5-4673-aa0c-cbe3e8534c02"),
        patient_first_name=_env("PATIENT_FIRST_NAME"),
        patient_last_name=_env("PATIENT_LAST_NAME"),
        patient_dob=_env("PATIENT_DOB"),
        patient_phone=_env("PATIENT_PHONE"),
        min_endpointing_delay=float(os.getenv("MIN_ENDPOINTING_DELAY", "0.7")),
        max_endpointing_delay=float(os.getenv("MAX_ENDPOINTING_DELAY", "3.0")),
    )
