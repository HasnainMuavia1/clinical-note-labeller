from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from .taxonomy import SPECIALTIES, UNCLASSIFIED, normalize_specialty

GENERIC_FOLDERS = frozenset({
    "clinical note", "clinical notes", "new notes", "old notes", "notes",
    "new folder", "demand", "data", "imaging", "extracted", "input", "output",
    "clinicl", "bro",
})

# Longer aliases are checked first so "orthopedic surgery" wins over "surgery".
ALIASES = {
    "orthopedic surgery": "Orthopedic Surgery",
    "orthopaedic surgery": "Orthopedic Surgery",
    "orthopedics": "Orthopedic Surgery",
    "orthopaedics": "Orthopedic Surgery",
    "orthopedic": "Orthopedic Surgery",
    "orthopaedic": "Orthopedic Surgery",
    "ortho": "Orthopedic Surgery",
    "cardiothoracic surgery": "Cardiothoracic Surgery",
    "cardiothoracic": "Cardiothoracic Surgery",
    "thoracic surgery": "Cardiothoracic Surgery",
    "vascular surgery": "Vascular Surgery",
    "vascular": "Vascular Surgery",
    "plastic surgery": "Plastic Surgery",
    "plastics": "Plastic Surgery",
    "neurosurgery": "Neurosurgery",
    "neurosurg": "Neurosurgery",
    "oral and maxillofacial surgery": "Oral and Maxillofacial Surgery",
    "oral surgery": "Oral and Maxillofacial Surgery",
    "maxillofacial": "Oral and Maxillofacial Surgery",
    "general surgery": "General Surgery",
    "surgery": "General Surgery",
    "cardiology": "Cardiology",
    "cardio": "Cardiology",
    "dermatology": "Dermatology",
    "derm": "Dermatology",
    "neurology": "Neurology",
    "neuro": "Neurology",
    "obstetrics and gynecology": "Obstetrics & Gynecology",
    "obstetrics & gynecology": "Obstetrics & Gynecology",
    "obstetrics": "Obstetrics & Gynecology",
    "gynecology": "Obstetrics & Gynecology",
    "obgyn": "Obstetrics & Gynecology",
    "ob/gyn": "Obstetrics & Gynecology",
    "ob-gyn": "Obstetrics & Gynecology",
    "gyn": "Obstetrics & Gynecology",
    "otolaryngology": "Otolaryngology",
    "ent": "Otolaryngology",
    "gastroenterology": "Gastroenterology",
    "family medicine": "Family Medicine",
    "family practice": "Family Medicine",
    "internal medicine": "Internal Medicine",
    "pediatrics": "Pediatrics",
    "pediatric": "Pediatrics",
    "peds": "Pediatrics",
    "psychiatry": "Psychiatry",
    "psychology": "Psychology",
    "physical therapy": "Physical Therapy",
    "occupational therapy": "Occupational Therapy",
    "physical medicine and rehabilitation": "Physical Medicine & Rehabilitation",
    "physical medicine": "Physical Medicine & Rehabilitation",
    "pm&r": "Physical Medicine & Rehabilitation",
    "pmr": "Physical Medicine & Rehabilitation",
    "urology": "Urology",
    "pulmonology": "Pulmonology",
    "pulmonary": "Pulmonology",
    "endocrinology": "Endocrinology",
    "nephrology": "Nephrology",
    "hematology": "Hematology",
    "oncology": "Oncology",
    "rheumatology": "Rheumatology",
    "pain medicine": "Pain Medicine",
    "emergency medicine": "Emergency Medicine",
    "critical care": "Critical Care",
    "anesthesiology": "Anesthesiology",
    "anesthesia": "Anesthesiology",
    "radiology": "Radiology",
    "podiatry": "Podiatry",
    "optometry": "Optometry",
    "ophthalmology": "Ophthalmology",
    "dentistry": "Dentistry",
    "dental": "Dentistry",
    "sleep medicine": "Sleep Medicine",
    "sports medicine": "Sports Medicine",
    "allergy and immunology": "Allergy and Immunology",
    "allergy": "Allergy and Immunology",
    "pathology": "Pathology",
    "geriatrics": "Geriatrics",
    "hospice and palliative medicine": "Hospice and Palliative Medicine",
    "palliative": "Hospice and Palliative Medicine",
    "infectious disease": "Infectious Disease",
    "speech language pathology": "Speech Language Pathology",
}

_ALIAS_ORDER = tuple(sorted(ALIASES.items(), key=lambda item: -len(item[0])))

_SURGERY_MODIFIERS = (
    (frozenset({"orthopedic", "orthopaedic", "ortho", "orthopedics", "orthopaedics"}),
     "Orthopedic Surgery"),
    (frozenset({"cardio", "cardiothoracic", "thoracic"}), "Cardiothoracic Surgery"),
    (frozenset({"neuro", "neurosurg", "neurosurgery"}), "Neurosurgery"),
    (frozenset({"plastic", "plastics"}), "Plastic Surgery"),
    (frozenset({"vascular"}), "Vascular Surgery"),
    (frozenset({"oral", "maxillofacial"}), "Oral and Maxillofacial Surgery"),
)


@dataclass(frozen=True)
class FolderHint:
    folder: str
    expected_specialty: str


def _folded(name: str) -> str:
    return re.sub(r"\s+", " ", name.strip().lower())


def _tokens(name: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", name.lower()))


def map_folder_to_specialty(part: str) -> str | None:
    raw = part.strip()
    if not raw:
        return None
    key = _folded(raw)
    if key in GENERIC_FOLDERS or key.endswith(".zip"):
        return None
    exact = normalize_specialty(raw)
    if exact != UNCLASSIFIED:
        return exact
    if key in ALIASES:
        return ALIASES[key]

    tokens = _tokens(key)
    if not tokens:
        return None

    if "surgery" in tokens:
        for modifiers, specialty in _SURGERY_MODIFIERS:
            if tokens & modifiers:
                return specialty
        return "General Surgery"

    for alias, specialty in _ALIAS_ORDER:
        if alias == "surgery":
            continue
        alias_tokens = _tokens(alias)
        if alias_tokens and alias_tokens <= tokens:
            return specialty
        if len(alias) >= 4 and re.search(rf"\b{re.escape(alias)}\b", key):
            return specialty

    for specialty in SPECIALTIES:
        if specialty == UNCLASSIFIED:
            continue
        lowered = specialty.lower()
        if lowered in key:
            return specialty
        spec_tokens = _tokens(specialty) - {"and", "the"}
        if spec_tokens and spec_tokens <= tokens:
            return specialty
    return None


def hint_from_source_path(source_path: str | None) -> FolderHint | None:
    if not source_path:
        return None
    path = source_path.replace("!/", "/")
    parts = Path(path).parts
    folders = parts[:-1] if len(parts) > 1 else ()
    for part in reversed(folders):
        mapped = map_folder_to_specialty(part)
        if mapped:
            return FolderHint(folder=part, expected_specialty=mapped)
    return None


def folder_compare(source_path: str | None, assigned: str | None) -> dict:
    hint = hint_from_source_path(source_path)
    if hint is None:
        return {"folder_label": None, "expected_specialty": None, "label_match": None}
    match = None
    if assigned:
        match = normalize_specialty(assigned) == hint.expected_specialty
    return {
        "folder_label": hint.folder,
        "expected_specialty": hint.expected_specialty,
        "label_match": match,
    }


def with_folder_compare(record: dict) -> dict:
    return {**record, **folder_compare(record.get("source_path"), record.get("specialty"))}
