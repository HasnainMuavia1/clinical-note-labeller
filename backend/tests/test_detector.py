from pathlib import Path

import pytest

from app.codes.detector import detect_codes
from app.codes.dictionaries import load_dictionaries

REF = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def dicts():
    return load_dictionaries(REF)


CODED_NOTE = """
ASSESSMENT AND PLAN
Diagnosis: E11.9 Type 2 diabetes mellitus without complications
Procedure Code: 99213 - Office/outpatient visit, established patient
Modifier: 99213-25
Drug administered: J1885
"""

UNCODED_NOTE = """
SUBJECTIVE
The patient is a 45 year old male who presents with three days of cough.
He lives at 1420 Oak Street, Beverly Hills, CA 90210. Phone 555 867 5309.
Vitals: BP 128/82, HR 72, Temp 98.6. He was last seen on 04/25 of this year.
Plan: rest, fluids, follow up in one week.
"""

NPI_ONLY_NOTE = """
Signed electronically by the attending physician.
Provider NPI 1234567893. Encounter completed.
The patient tolerated the visit well and was discharged home.
"""


def test_coded_note_is_flagged_with_codes(dicts):
    result = detect_codes(CODED_NOTE, dicts)
    assert result.has_codes is True
    codes = {h.code for h in result.hits}
    assert "E11.9" in codes or "E119" in codes
    assert "99213" in codes
    assert "J1885" in codes


def test_coded_note_detects_the_attached_modifier(dicts):
    result = detect_codes(CODED_NOTE, dicts)
    assert any(h.kind == "modifier" and h.code == "25" for h in result.hits)


def test_narrative_note_with_zip_and_phone_is_not_flagged(dicts):
    result = detect_codes(UNCODED_NOTE, dicts)
    assert result.has_codes is False, [h.code for h in result.hits]


def test_zip_code_after_state_is_rejected(dicts):
    result = detect_codes(UNCODED_NOTE, dicts)
    assert any(h.code == "90210" for h in result.rejected)


def test_npi_alone_does_not_make_a_note_coded(dicts):
    result = detect_codes(NPI_ONLY_NOTE, dicts)
    assert result.has_codes is False
    assert "1234567893" in result.npis


def test_unknown_code_with_explicit_cue_is_still_accepted(dicts):
    # 0042T is a Category III code absent from the PFS RVU file, so only the cue can carry it.
    text = "Billing summary\nCPT: 0042T experimental procedure performed."
    result = detect_codes(text, dicts)
    assert result.has_codes is True
    hit = next(h for h in result.hits if h.code == "0042T")
    assert hit.dictionary_hit is False
    assert hit.rule == "structural+cue"


def test_every_hit_records_auditable_evidence(dicts):
    result = detect_codes(CODED_NOTE, dicts)
    for hit in result.hits:
        assert hit.rule
        assert hit.context
        assert hit.score > 0
