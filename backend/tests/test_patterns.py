from app.codes.patterns import MODIFIERS, extract_candidates, is_valid_npi


def kinds(text):
    return {(c.text, c.kind) for c in extract_candidates(text)}


def test_valid_npi_passes_luhn():
    assert is_valid_npi("1234567893")


def test_invalid_npi_fails_luhn():
    assert not is_valid_npi("1234567890")
    assert not is_valid_npi("123456789")


def test_extracts_cpt_candidates():
    assert ("99213", "cpt") in kinds("Office visit 99213 today")


def test_extracts_category_two_and_three_cpt():
    found = kinds("Codes 0001F and 0042T were captured")
    assert ("0001F", "cpt") in found
    assert ("0042T", "cpt") in found


def test_extracts_hcpcs_candidates():
    assert ("J1885", "hcpcs") in kinds("Administered J1885 30mg IV")


def test_extracts_icd10_candidates_with_and_without_dot():
    found = kinds("Dx: E11.9, secondary I10")
    assert ("E11.9", "icd10") in found
    assert ("I10", "icd10") in found


def test_modifier_only_detected_when_attached_to_a_code():
    attached = kinds("Billed 99213-25 with modifier")
    assert ("25", "modifier") in attached
    assert ("25", "modifier") not in kinds("The patient is 25 years old")


def test_known_alpha_modifiers_are_in_the_bundled_list():
    for m in ["LT", "RT", "XU", "59", "25", "GA"]:
        assert m in MODIFIERS


def test_npi_candidate_extracted():
    assert ("1234567893", "npi") in kinds("NPI 1234567893 signed the note")


def test_offsets_point_at_the_match():
    text = "Procedure 99213 done"
    c = next(c for c in extract_candidates(text) if c.text == "99213")
    assert text[c.start:c.end] == "99213"
