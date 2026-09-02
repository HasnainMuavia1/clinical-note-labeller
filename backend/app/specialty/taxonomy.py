from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path

_DIR = Path(__file__).parent
SPECIALTIES: tuple[str, ...] = tuple(json.loads((_DIR / "nucc_specialties.json").read_text()))
UNCLASSIFIED = "Unclassified"


@lru_cache
def _taxonomy_map() -> dict[str, str]:
    return json.loads((_DIR / "nucc_map.json").read_text())


@lru_cache
def _lower_index() -> dict[str, str]:
    return {s.lower(): s for s in SPECIALTIES}


def is_known_specialty(name: str) -> bool:
    return name in SPECIALTIES


def normalize_specialty(name: str | None) -> str:
    if not name:
        return UNCLASSIFIED
    return _lower_index().get(name.strip().lower(), UNCLASSIFIED)


def specialty_for_taxonomy(taxonomy_code: str | None) -> str | None:
    if not taxonomy_code:
        return None
    return _taxonomy_map().get(taxonomy_code.strip().upper())


def folder_name(specialty: str) -> str:
    cleaned = specialty.replace("&", "and")
    cleaned = re.sub(r"[^A-Za-z0-9]+", "-", cleaned).strip("-")
    return cleaned or UNCLASSIFIED
