"""Central configuration, read from the environment (.env).

There are two modes:

* console  (`python -m caller.agent console`): a local rehearsal where you play the
  receptionist. Needs only the speech/LLM provider keys. Telephony settings are not
  used, and the patient falls back to a fictional identity if PATIENT_* is unset.
* outbound (`scripts/run_calls.py`): real phone calls. Everything is validated strictly,
  including the SIP trunk, LiveKit credentials and an explicit test patient that
  matches the account created on the clinic's signup page.
"""

from __future__ import annotations

import os
import ssl
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")


def _ensure_ca_certificates() -> None:
    """Python from the python.org macOS installer has no CA certificates until its
    'Install Certificates.command' is run, so every HTTPS call (Deepgram, Cartesia,
    LiveKit) fails with CERTIFICATE_VERIFY_FAILED. In that case only, use the certifi
    bundle that livekit-agents already installs. Never overrides an explicit setting."""
    if os.getenv("SSL_CERT_FILE") or os.getenv("SSL_CERT_DIR"):
        return
    paths = ssl.get_default_verify_paths()
    if paths.cafile or (paths.capath and os.path.isdir(paths.capath) and any(os.scandir(paths.capath))):
        return  # the system already has certificates
    try:
        import certifi
    except ImportError:
        return
    os.environ["SSL_CERT_FILE"] = certifi.where()


_ensure_ca_certificates()

# The ONLY number this bot is ever allowed to dial. Hard-coded on purpose:
# the agent refuses to place a call anywhere else, whatever the job metadata says.
TARGET_NUMBER = "+18054398008"

AGENT_NAME = "patient-caller"
CALLS_DIR = ROOT / "calls"
SCENARIOS_FILE = ROOT / "caller" / "scenarios.yaml"

# Needed in both modes: the STT -> LLM -> TTS pipeline.
PROVIDER_KEYS = ("DEEPGRAM_API_KEY", "OPENAI_API_KEY", "CARTESIA_API_KEY")
# Needed only to place real calls.
OUTBOUND_KEYS = ("LIVEKIT_URL", "LIVEKIT_API_KEY", "LIVEKIT_API_SECRET", "SIP_OUTBOUND_TRUNK_ID")
PATIENT_KEYS = ("PATIENT_FIRST_NAME", "PATIENT_LAST_NAME", "PATIENT_DOB", "PATIENT_PHONE")

# Fictional identity for console rehearsals only (555-01xx numbers are reserved for fiction).
# Real calls never fall back to this: they must use the test account you created.
CONSOLE_PATIENT_DEFAULTS = {
    "PATIENT_FIRST_NAME": "Sarah",
    "PATIENT_LAST_NAME": "Mitchell",
    "PATIENT_DOB": "March 15, 1992",
    "PATIENT_PHONE": "+12125550147",
}


def _value(name: str) -> str:
    """The variable's value, or "" if it is unset or still a .env.example placeholder."""
    value = os.getenv(name, "").strip()
    lowered = value.lower()
    if "xxxx" in lowered or lowered.startswith("your-") or "your-project" in lowered:
        return ""
    return value


def prepare_console_env() -> None:
    """Console mode never connects to LiveKit Cloud. If LIVEKIT_URL is set but the key or
    secret is missing (e.g. the .env.example placeholder), livekit-agents crashes at startup
    with "either token, or api_key and api_secret, must be set". Drop the URL in that case."""
    if not all(_value(n) for n in ("LIVEKIT_URL", "LIVEKIT_API_KEY", "LIVEKIT_API_SECRET")):
        os.environ.pop("LIVEKIT_URL", None)


def missing_settings(*, outbound: bool) -> list[str]:
    """Names of required variables that are empty or placeholders for this mode."""
    names = list(PROVIDER_KEYS)
    if outbound:
        names += [*OUTBOUND_KEYS, *PATIENT_KEYS]
    return [n for n in names if not _value(n)]


@dataclass(frozen=True)
class Settings:
    outbound: bool
    sip_trunk_id: str  # "" in console mode
    llm_model: str
    stt_model: str
    tts_model: str
    tts_voice: str
    patient_first_name: str
    patient_last_name: str
    patient_dob: str  # written the way it should be spoken
    patient_phone: str
    min_endpointing_delay: float
    max_endpointing_delay: float
    using_default_patient: bool = False


def load_settings(*, outbound: bool = False) -> Settings:
    """Load settings for console (default) or outbound-call mode.

    Raises RuntimeError listing every missing variable, so you can fix them in one go.
    """
    missing = missing_settings(outbound=outbound)
    if missing:
        mode = "real calls" if outbound else "console mode"
        raise RuntimeError(
            f"Missing required environment variable(s) for {mode}: {', '.join(missing)}. "
            "Set them in .env (see .env.example)."
        )

    def patient(name: str) -> str:
        return _value(name) or ("" if outbound else CONSOLE_PATIENT_DEFAULTS[name])

    return Settings(
        outbound=outbound,
        sip_trunk_id=_value("SIP_OUTBOUND_TRUNK_ID") if outbound else "",
        llm_model=os.getenv("LLM_MODEL", "gpt-4.1-mini"),
        stt_model=os.getenv("STT_MODEL", "nova-3"),
        tts_model=os.getenv("TTS_MODEL", "sonic-3"),
        tts_voice=os.getenv("TTS_VOICE_ID", "f786b574-daa5-4673-aa0c-cbe3e8534c02"),
        patient_first_name=patient("PATIENT_FIRST_NAME"),
        patient_last_name=patient("PATIENT_LAST_NAME"),
        patient_dob=patient("PATIENT_DOB"),
        patient_phone=patient("PATIENT_PHONE"),
        min_endpointing_delay=float(os.getenv("MIN_ENDPOINTING_DELAY", "0.7")),
        max_endpointing_delay=float(os.getenv("MAX_ENDPOINTING_DELAY", "3.0")),
        using_default_patient=not outbound and not all(_value(n) for n in PATIENT_KEYS),
    )
