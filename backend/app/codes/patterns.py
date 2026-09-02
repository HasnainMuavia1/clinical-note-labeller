from __future__ import annotations

import json
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

_MODIFIER_FILE = Path(__file__).with_name("modifiers.json")


@lru_cache
def _load_modifiers() -> frozenset[str]:
    data = json.loads(_MODIFIER_FILE.read_text())
    return frozenset(data["numeric"]) | frozenset(data["alpha"])


MODIFIERS = _load_modifiers()

ICD10_RE = re.compile(r"\b[A-TV-Z][0-9][0-9A-Z](?:\.?[0-9A-Z]{1,4})?\b")
CPT_RE = re.compile(r"\b\d{4}[0-9FTUM]\b")
HCPCS_RE = re.compile(r"\b[A-V]\d{4}\b")
NPI_RE = re.compile(r"\b\d{10}\b")
ATTACHED_MODIFIER_RE = re.compile(r"\b(?:\d{4}[0-9FTUM]|[A-V]\d{4})\s*[-–]\s*([A-Z0-9]{2})\b")


@dataclass(frozen=True)
class Candidate:
    text: str
    kind: str
    start: int
    end: int


def is_valid_npi(value: str) -> bool:
    """NPI check digit: Luhn over the constant prefix 80840 plus the first 9 digits."""
    value = value.strip()
    if not re.fullmatch(r"\d{10}", value):
        return False
    digits = [int(d) for d in "80840" + value[:9]]
    total = 0
    for index, digit in enumerate(reversed(digits)):
        if index % 2 == 0:
            digit *= 2
            if digit > 9:
                digit -= 9
        total += digit
    return (total + int(value[9])) % 10 == 0


def extract_candidates(text: str) -> list[Candidate]:
    found: list[Candidate] = []
    seen: set[tuple[int, int, str]] = set()

    def add(match: re.Match[str], kind: str, group: int = 0) -> None:
        key = (match.start(group), match.end(group), kind)
        if key in seen:
            return
        seen.add(key)
        found.append(Candidate(match.group(group), kind, match.start(group), match.end(group)))

    for match in ICD10_RE.finditer(text):
        add(match, "icd10")
    for match in CPT_RE.finditer(text):
        add(match, "cpt")
    for match in HCPCS_RE.finditer(text):
        add(match, "hcpcs")
    for match in NPI_RE.finditer(text):
        if is_valid_npi(match.group(0)):
            add(match, "npi")
    for match in ATTACHED_MODIFIER_RE.finditer(text):
        if match.group(1) in MODIFIERS:
            add(match, "modifier", group=1)

    return sorted(found, key=lambda c: (c.start, c.kind))
