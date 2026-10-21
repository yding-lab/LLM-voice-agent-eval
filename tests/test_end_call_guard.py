"""Checks when the patient bot is allowed to hang up.

    python tests/test_end_call_guard.py

Needs no API keys and places no calls. Covers the two early-call failures: hanging up right
after asking a question (calls/01 02:05) and hanging up during a transfer (calls/10 04:25).
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from livekit.agents import llm  # noqa: E402

from caller.agent import end_call_blocker  # noqa: E402


def ctx(*turns: tuple[str, str]) -> llm.ChatContext:
    c = llm.ChatContext.empty()
    for role, text in turns:
        c.add_message(role=role, content=text)
    return c


CASES = {
    "goodbye after the task -> hang up": (
        [("user", "You're all set for Tuesday at 10."), ("assistant", "Great, thanks. Goodbye!")], False),
    "question as last line -> wait (calls/01)": (
        [("user", "I'll note that."), ("assistant", "Can you repeat the date and time of my appointment?")], True),
    "no goodbye yet -> say goodbye first": (
        [("user", "Anything else?"), ("assistant", "No, that's everything.")], True),
    "clinic is transferring -> wait (calls/10)": (
        [("assistant", "Sure, thanks."), ("user", "Transferring you now. Thank you.")], True),
    "clinic says goodbye -> hang up": (
        [("assistant", "Thanks."), ("user", "You've reached the test line. Goodbye.")], False),
    "clinic asks to hold -> wait": (
        [("assistant", "Okay."), ("user", "One moment while I check.")], True),
}


def main() -> int:
    ok = True
    for name, (turns, should_block) in CASES.items():
        blocked = end_call_blocker(ctx(*turns)) is not None
        passed = blocked == should_block
        ok &= passed
        print(f"{'PASS' if passed else 'FAIL'}  {name}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
