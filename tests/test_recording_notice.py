"""Checks that the patient bot stays silent on the clinic's recording notice.

    python tests/test_recording_notice.py

Needs no API keys and places no calls: it runs a real LiveKit AgentSession with the LLM
replaced by a fixed reply, so it tests our logic, not audio timing.
"""

import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from livekit.agents import Agent, AgentSession  # noqa: E402
from livekit.plugins import openai  # noqa: E402

import caller.agent as ca  # noqa: E402

NOTICE = "This call may be recorded for quality and training purposes."
GREETING = "Thank you for calling Pivot Point Orthopedics, how can I help you?"


async def _fake_llm_node(agent, chat_ctx, tools, model_settings):
    yield "Hi, I'd like to book an appointment for my knee."


def check_real_transcripts() -> bool:
    """Only the recording notices in calls/ may be treated as notice-only."""
    flagged, total = [], 0
    for path in sorted((ROOT / "calls").glob("*/transcript.json")):
        for turn in json.loads(path.read_text())["turns"]:
            if turn["speaker"] == "CLINIC AGENT":
                total += 1
                if ca.is_only_recording_notice(turn["text"]):
                    flagged.append(turn["text"])
    wrong = [t for t in flagged if "may be recorded" not in t]
    print(f"real clinic lines: {total}, notice-only: {len(flagged)}, wrongly flagged: {len(wrong)}")
    return not wrong


async def run_turns(turns: list[str]) -> list[bool]:
    """Feed clinic turns as typed input (like console --text); return which got a reply."""
    session = AgentSession(llm=openai.LLM(model="gpt-4.1-mini", api_key="unused"))
    await session.start(ca.PatientAgent("You are a patient.", hangup=None))
    answered = []
    for text in turns:
        result = await session.run(user_input=text)
        answered.append(
            any(
                getattr(ev, "type", "") == "message" and ev.item.role == "assistant"
                for ev in result.events
            )
        )
    await session.aclose()
    return answered


async def main() -> int:
    Agent.default.llm_node = staticmethod(_fake_llm_node)  # stand-in LLM only
    cases = {
        "notice, then greeting -> silent, then answer": ([NOTICE, GREETING], [False, True]),
        "greeting with no notice -> answer": ([GREETING], [True]),
        "notice + greeting in one turn -> answer": ([f"{NOTICE} {GREETING}"], [True]),
        "notice again mid-call -> answer (opening only)": ([GREETING, NOTICE], [True, True]),
    }
    ok = check_real_transcripts()
    for name, (turns, expected) in cases.items():
        got = await run_turns(turns)
        passed = got == expected
        ok &= passed
        print(f"{'PASS' if passed else 'FAIL'}  {name}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
