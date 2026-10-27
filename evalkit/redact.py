"""Pseudonymize personal data in transcripts before they leave the machine (e.g. to an
LLM judge or a third-party API).

Each distinct value gets a stable token within one transcript: the first date of birth
becomes [DOB_1], a different one [DOB_2], and the same value keeps its token. That keeps
what the judge needs ("the caller corrected their DOB and the agent kept the old one")
while removing the values themselves.

Appointment dates ("Tuesday, October 20 at 10AM") are left alone: only dates that carry a
birth-year-like year are treated as a date of birth.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

MONTHS = (r"(?:january|february|march|april|may|june|july|august|september|october|"
          r"november|december|jan|feb|mar|apr|jun|jul|aug|sep|sept|oct|nov|dec)\.?")
ORDINAL_WORDS = (r"(?:first|second|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth|eleventh|"
                 r"twelfth|thirteenth|fourteenth|fifteenth|sixteenth|seventeenth|eighteenth|"
                 r"nineteenth|twentieth|twenty[- ](?:first|second|third|fourth|fifth|sixth|seventh|"
                 r"eighth|ninth)|thirtieth|thirty[- ]first)")
DAY = rf"(?:\d{{1,2}}(?:st|nd|rd|th)?|{ORDINAL_WORDS})"
SPOKEN_YEAR = (r"(?:nineteen[- ](?:hundred|oh[- ]\w+|ten|eleven|twelve|thirteen|fourteen|fifteen|"
               r"sixteen|seventeen|eighteen|nineteen|(?:twenty|thirty|forty|fifty|sixty|seventy|"
               r"eighty|ninety)(?:[- ](?:one|two|three|four|five|six|seven|eight|nine))?)|"
               r"two thousand(?:[- ](?:and[- ])?(?:one|two|three|four|five|six|seven|eight|nine|ten|"
               r"eleven|twelve|thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen))?)")
YEAR = rf"(?:19\d\d|20[01]\d|{SPOKEN_YEAR})"

DOB_PATTERNS = [
    re.compile(rf"\b{MONTHS}\s+{DAY}(?:,|\.)?\s+{YEAR}\b", re.I),            # August 20, 1992 / August twentieth nineteen ninety-two
    re.compile(rf"\b{DAY}\s+(?:of\s+)?{MONTHS},?\s+{YEAR}\b", re.I),        # 20th of August 1992
    re.compile(r"\b(?:0?[1-9]|1[0-2])[/-](?:0?[1-9]|[12]\d|3[01])[/-](?:19\d\d|20[01]\d)\b"),  # 08/20/1992
]
PHONE = re.compile(r"(?<!\w)(?:\+?1[\s.-]?)?\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}(?!\w)")
EMAIL = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")


class Redactor:
    def __init__(self, names: Iterable[str] = ()) -> None:
        # Longest first so "Sarah Mitchell" wins over "Sarah".
        names = sorted({n.strip() for n in names if n and len(n.strip()) > 1}, key=len, reverse=True)
        self._name_re = re.compile(r"\b(?:" + "|".join(map(re.escape, names)) + r")\b", re.I) if names else None
        self._seen: dict[tuple[str, str], str] = {}
        self._counts: dict[str, int] = {}

    def _token(self, kind: str, value: str) -> str:
        key = (kind, re.sub(r"[\W_]+", " ", value.lower()).strip())
        if key not in self._seen:
            self._counts[kind] = self._counts.get(kind, 0) + 1
            self._seen[key] = f"[{kind}_{self._counts[kind]}]"
        return self._seen[key]

    def redact(self, text: str) -> str:
        for pat in DOB_PATTERNS:
            text = pat.sub(lambda m: self._token("DOB", m.group(0)), text)
        text = EMAIL.sub(lambda m: self._token("EMAIL", m.group(0)), text)
        text = PHONE.sub(lambda m: self._token("PHONE", re.sub(r"\D", "", m.group(0))[-10:]), text)
        if self._name_re:
            text = self._name_re.sub(lambda m: self._token("NAME", m.group(0)), text)
        return text

    @property
    def counts(self) -> dict[str, int]:
        return dict(self._counts)


def redact(text: str, names: Iterable[str] = ()) -> str:
    return Redactor(names).redact(text)
