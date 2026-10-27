"""PII redaction before transcripts go to the LLM judge.

    python tests/test_redact.py

No API keys. Checks that dates of birth, phone numbers, emails and known names are
replaced with stable tokens, and that appointment dates and times are not.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from caller.redact import Redactor  # noqa: E402

CASES = [
    # (input, must contain, must NOT contain)
    ("My date of birth is August twentieth, nineteen ninety-two.", ["[DOB_1]"], ["ninety-two", "August"]),
    ("Your date of birth is 07/04/2000 for demo purposes.", ["[DOB_1]"], ["07/04/2000"]),
    ("Born March 15, 1992.", ["[DOB_1]"], ["1992"]),
    ("Call me back at +1 212 555 0147 or (212) 555-0147.", ["[PHONE_1]"], ["0147", "[PHONE_2]"]),
    ("Email sarah.m@example.com please.", ["[EMAIL_1]"], ["example.com"]),
    ("This is Sarah Mitchell. Sarah here again.", ["[NAME_1]", "[NAME_2]"], ["Mitchell", "Sarah"]),
    # Must NOT be redacted: appointment dates and times carry no birth year.
    ("I have Tuesday, October 20 at 10AM with the doctor.", ["Tuesday, October 20 at 10AM"], ["[DOB"]),
    ("We close at 12PM on Fridays.", ["12PM"], ["[PHONE"]),
]


def main() -> int:
    ok = True
    for text, must, must_not in CASES:
        out = Redactor(names=["Sarah Mitchell", "Sarah", "Mitchell"]).redact(text)
        passed = all(m in out for m in must) and not any(n in out for n in must_not)
        ok &= passed
        print(f"{'PASS' if passed else 'FAIL'}  {text!r} -> {out!r}")

    # Same value keeps its token; a corrected value gets a new one (the judge needs to see the change).
    r = Redactor()
    a = r.redact("Your date of birth is July fourth, two thousand.")
    b = r.redact("Actually, my real date of birth is August twentieth, nineteen ninety-two.")
    c = r.redact("I'll keep July fourth, two thousand on file.")
    passed = "[DOB_1]" in a and "[DOB_2]" in b and "[DOB_1]" in c
    ok &= passed
    print(f"{'PASS' if passed else 'FAIL'}  stable tokens across a correction: {a!r} / {b!r} / {c!r}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
