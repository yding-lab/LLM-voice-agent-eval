"""PII pseudonymization before transcripts reach any model."""

import pytest

from evalkit.redact import Redactor

NAMES = ["Sarah Mitchell", "Sarah", "Mitchell"]


@pytest.mark.parametrize("text, must, must_not", [
    ("My date of birth is August twentieth, nineteen ninety-two.", ["[DOB_1]"], ["ninety-two", "August"]),
    ("Your date of birth is 07/04/2000 for demo purposes.", ["[DOB_1]"], ["07/04/2000"]),
    ("Born March 15, 1992.", ["[DOB_1]"], ["1992"]),
    ("Call me back at +1 212 555 0147 or (212) 555-0147.", ["[PHONE_1]"], ["0147", "[PHONE_2]"]),
    ("Email sarah.m@example.com please.", ["[EMAIL_1]"], ["example.com"]),
    ("This is Sarah Mitchell. Sarah here again.", ["[NAME_1]", "[NAME_2]"], ["Mitchell", "Sarah"]),
    # Appointment dates and clock times carry no birth year and must survive.
    ("I have Tuesday, October 20 at 10AM with the doctor.", ["Tuesday, October 20 at 10AM"], ["[DOB"]),
    ("We close at 12PM on Fridays.", ["12PM"], ["[PHONE"]),
])
def test_redacts_pii_and_keeps_appointment_details(text, must, must_not):
    out = Redactor(NAMES).redact(text)
    assert all(m in out for m in must), out
    assert not any(n in out for n in must_not), out


def test_tokens_are_stable_so_a_correction_stays_visible():
    r = Redactor()
    a = r.redact("Your date of birth is July fourth, two thousand.")
    b = r.redact("Actually, my real date of birth is August twentieth, nineteen ninety-two.")
    c = r.redact("I'll keep July fourth, two thousand on file.")
    assert "[DOB_1]" in a and "[DOB_2]" in b and "[DOB_1]" in c
    assert r.counts == {"DOB": 2}
