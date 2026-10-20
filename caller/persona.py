"""Scenario loading and the patient system prompt.

The prompt is where most of the "does this sound like a real caller" quality
comes from, so it is deliberately specific about spoken style, turn length and
when to stay quiet.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import yaml

from .config import SCENARIOS_FILE, Settings


@dataclass
class Interrupt:
    on_clinic_turn: int
    after_seconds: float
    line: str


@dataclass
class Check:
    """A numbered failure condition, e.g. SCH-01. Never shown to the patient bot."""

    id: str
    fail_if: str
    severity: str


@dataclass
class Scenario:
    id: str
    title: str
    background: str
    goal: str
    try_: list[str] = field(default_factory=list)
    expected: str = ""
    checks: list[Check] = field(default_factory=list)
    pass_outcome: str = ""
    max_minutes: float = 3.0
    interrupt: Interrupt | None = None
    # Optional per-scenario overrides. identity keys: first, last, dob, phone.
    # Without them the patient is the test account from .env.
    identity: dict[str, str] = field(default_factory=dict)
    opening: str = ""  # roughly what to say once the receptionist has greeted us
    tts_voice: str | None = None  # Cartesia voice id, so the voice matches the persona


def load_common_checks() -> list[Check]:
    raw = yaml.safe_load(SCENARIOS_FILE.read_text())
    return [Check(**c) for c in raw.get("common_checks", [])]


def load_scenarios(settings: Settings | None = None) -> dict[str, Scenario]:
    raw = yaml.safe_load(SCENARIOS_FILE.read_text())
    fill = _fill_values(settings)
    out: dict[str, Scenario] = {}
    for item in raw["scenarios"]:
        intr = item.get("interrupt")
        sc = Scenario(
            id=item["id"],
            title=item["title"],
            background=_fill(item.get("background", ""), fill),
            goal=_fill(item["goal"], fill),
            try_=[_fill(t, fill) for t in item.get("try", [])],
            expected=_fill(item.get("expected", ""), fill),
            checks=[Check(**c) for c in item.get("checks", [])],
            pass_outcome=_fill(item.get("pass_outcome", ""), fill),
            max_minutes=float(item.get("max_minutes", 3)),
            interrupt=Interrupt(**intr) if intr else None,
            identity={k: str(v) for k, v in (item.get("identity") or {}).items()},
            opening=_fill(item.get("opening", ""), fill),
            tts_voice=item.get("tts_voice"),
        )
        out[sc.id] = sc
    return out


def _fill_values(settings: Settings | None) -> dict[str, str]:
    if settings is None:
        return {"first": "Alex", "last": "Rivera", "dob": "(date of birth)", "phone": "(phone)"}
    return {
        "first": settings.patient_first_name,
        "last": settings.patient_last_name,
        "dob": settings.patient_dob,
        "phone": settings.patient_phone,
    }


def _fill(text: str, values: dict[str, str]) -> str:
    for k, v in values.items():
        text = text.replace("{" + k + "}", v)
    return text.strip()


def build_instructions(sc: Scenario, s: Settings) -> str:
    tries = "\n".join(f"- {t}" for t in sc.try_) or "- (nothing extra)"
    who = sc.identity
    opening = (
        f"\nHOW YOU OPEN\nWait for the receptionist to greet you, then say something close to:\n"
        f"\"{sc.opening}\" (in your own words, not word for word).\n"
        if sc.opening
        else ""
    )
    return f"""You are role-playing a PATIENT making a phone call to a medical clinic.
The other side of the call is the clinic's front-desk receptionist. Stay fully in character
for the whole call. Never say or hint that you are an AI, a bot, or running a test.

WHO YOU ARE
- Name: {who.get("first", s.patient_first_name)} {who.get("last", s.patient_last_name)}
- Date of birth: {who.get("dob", s.patient_dob)}
- Phone number: {who.get("phone", s.patient_phone)}
- {sc.background}

YOUR GOAL ON THIS CALL
{sc.goal}
{opening}
THINGS TO WORK IN NATURALLY (one at a time, when it fits, not all at once)
{tries}

HOW YOU TALK
- Everything you write is spoken aloud by text-to-speech on a phone line. Write plain
  spoken English only: no lists, no markdown, no emojis, no stage directions, no brackets.
- Keep turns short, usually one or two sentences and under about 25 words. Real callers
  don't monologue. One request or question per turn.
- Answer what the receptionist asked. Don't volunteer your date of birth, phone number
  or other details until they ask for them.
- Say numbers and dates the way people speak: "March fourth, nineteen eighty-five",
  "five five five, one two three, four five six seven".
- Sound like a normal person: an occasional "um", "okay", "yeah, that works", "oh, got it".
  Don't overdo it, and don't start every turn the same way.
- You only hear the receptionist through speech recognition. If what they said looks
  garbled or makes no sense, ask them to repeat it instead of guessing.
- Don't repeat their whole answer back to them, and don't thank them after every sentence.
- You are the patient. Never invent facts about the clinic, and never act like staff.

STAYING QUIET
- A recording notice ("this call may be recorded...") is not a greeting. Stay silent
  (hold_silently) until the receptionist actually greets you. "Thank you for calling..."
  is a greeting, never a goodbye.
- If the receptionist says "one moment", "let me check", "please hold", or is clearly
  in the middle of looking something up, call the hold_silently tool instead of talking.
- If they only acknowledged you ("okay", "mm-hmm", "got it") and are clearly about to
  continue, call hold_silently.

STEERING
- Keep steering back to your goal. If they misunderstand, correct them briefly.
- If they fail at the same thing twice, don't loop forever: accept it, mention you'll
  follow up another way, and move on or wrap up.
- If they offer to transfer you to a person, say "sure" and wait (hold_silently).

ENDING THE CALL
- When your goal is done, or it clearly can't be done, or you're past roughly
  {sc.max_minutes:g} minutes: thank them, say a short goodbye, then call end_call.
- If the receptionist says goodbye first, say a brief goodbye and call end_call.
- Never call end_call in the same turn as a question; only after the goodbye.
"""
