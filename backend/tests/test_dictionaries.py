from pathlib import Path

import pytest

from app.codes.dictionaries import load_dictionaries, normalize_icd10

REF = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def dicts():
    return load_dictionaries(REF)


def test_normalize_icd10_strips_dot_and_uppercases():
    assert normalize_icd10("e11.9") == "E119"
    assert normalize_icd10("I10") == "I10"


def test_cpt_dictionary_has_expected_size_and_codes(dicts):
    assert len(dicts.cpt) > 9000
    for code in ["99213", "99490", "80053", "87880", "36415", "0001F"]:
        assert code in dicts.cpt, code


def test_hcpcs_dictionary_loads_level_two_codes(dicts):
    # 16,734 lines but 9,229 distinct codes: the HCPCS file repeats a code per pricing row.
    assert len(dicts.hcpcs) > 9000
    assert "A1001" in dicts.hcpcs
    assert "J1885" in dicts.hcpcs
    assert "V5364" in dicts.hcpcs


def test_icd10_dictionary_loads_and_is_dotless(dicts):
    assert len(dicts.icd10) > 90000
    assert "A00" in dicts.icd10
    assert "E119" in dicts.icd10
    assert "E11.9" not in dicts.icd10


def test_contains_reports_the_source_dictionary(dicts):
    assert dicts.contains("99213") == "cpt"
    assert dicts.contains("J1885") in {"hcpcs", "cpt"}
    assert dicts.contains("E11.9") == "icd10"
    assert dicts.contains("ZZZZZ") is None


def test_descriptions_are_available(dicts):
    assert "cholera" in dicts.descriptions["A00"].lower()
