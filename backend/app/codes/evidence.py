from __future__ import annotations

import re

from .dictionaries import CodeDictionaries
from .patterns import Candidate

CONTEXT_WINDOW = 60

POSITIVE_CUES = re.compile(
    r"(?i)\b(cpt|hcpcs|icd[-\s]?10(?:[-\s]?cm)?|dx|diagnos[ei]s|procedure\s+code|"
    r"billing|claim|charge|modifier|units?\s+billed|assessment\s+and\s+plan|coding\s+summary|"
    r"e/?m\s+code|revenue\s+code|administered)\b"
)

NEGATIVE_PATTERNS = [
    ("zip", re.compile(r"(?i)\b(?:A[KLRZ]|C[AOT]|D[CE]|FL|GA|HI|I[ADLN]|K[SY]|LA|M[ADEINOST]|"
                       r"N[CDEHJMVY]|O[HKR]|PA|RI|S[CD]|T[NX]|UT|V[AT]|W[AIVY])[,\s]+\d{5}\b")),
    ("phone", re.compile(r"\b\d{3}[-.\s]\d{3}[-.\s]\d{4}\b")),
    ("date", re.compile(r"\b\d{1,2}/\d{1,2}(?:/\d{2,4})?\b")),
    ("vitals", re.compile(r"(?i)\b(?:bp|hr|rr|temp|spo2|weight|height|bmi)\b[^\n]{0,20}$")),
    ("identifier", re.compile(r"(?i)\b(?:mrn|account|acct|policy|member|phone|fax|room|zip)\b[^\n]{0,15}$")),
    ("age", re.compile(r"(?i)\b(?:is|aged?)\s+(?:a\s+)?$")),
]

_CONTEXTUAL = {"vitals", "identifier", "age"}


def context_of(text: str, candidate: Candidate, window: int = CONTEXT_WINDOW) -> str:
    snippet = text[max(0, candidate.start - window): candidate.end + window]
    return snippet.replace("\x00", "")


def has_positive_cue(text: str, candidate: Candidate) -> bool:
    line_start = text.rfind("\n", 0, candidate.start) + 1
    before = text[max(line_start, candidate.start - CONTEXT_WINDOW): candidate.start]
    return bool(POSITIVE_CUES.search(before))


def negative_reason(text: str, candidate: Candidate) -> str | None:
    before_start = max(0, candidate.start - 30)
    before = text[before_start: candidate.start]
    around_start = before_start
    around = text[around_start: candidate.end + 10]
    for name, pattern in NEGATIVE_PATTERNS:
        if name in _CONTEXTUAL:
            if pattern.search(before):
                return name
            continue
        for match in pattern.finditer(around):
            if (match.start() + around_start <= candidate.start
                    and match.end() + around_start >= candidate.end):
                return name
    return None


def score_candidate(text: str, candidate: Candidate, dicts: CodeDictionaries) -> tuple[float, str]:
    """Return (score, rule). A non-positive score means the candidate is rejected."""
    if candidate.kind == "npi":
        return 0.0, "npi-not-a-code"

    if candidate.kind == "modifier":
        return 0.5, "attached-modifier"

    source = dicts.contains(candidate.text)
    cue = has_positive_cue(text, candidate)
    negative = negative_reason(text, candidate)

    if source and cue:
        return 1.5, "dictionary+cue"
    if source and not negative:
        return 1.0, "dictionary"
    if source and negative:
        return 0.0, f"rejected:{negative}"
    if cue and not negative:
        return 1.0, "structural+cue"
    return 0.0, f"rejected:{negative or 'no-evidence'}"
