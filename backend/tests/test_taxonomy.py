from app.specialty.taxonomy import (
    SPECIALTIES,
    folder_name,
    is_known_specialty,
    normalize_specialty,
    specialty_for_taxonomy,
)


def test_specialty_list_is_closed_and_includes_unclassified():
    assert "Unclassified" in SPECIALTIES
    assert "Cardiology" in SPECIALTIES
    assert len(SPECIALTIES) >= 30
    assert len(set(SPECIALTIES)) == len(SPECIALTIES)


def test_is_known_specialty():
    assert is_known_specialty("Cardiology")
    assert not is_known_specialty("Cardio")


def test_normalize_maps_unknown_to_unclassified():
    assert normalize_specialty("Cardiology") == "Cardiology"
    assert normalize_specialty("cardiology") == "Cardiology"
    assert normalize_specialty("Wizardry") == "Unclassified"


def test_taxonomy_code_maps_to_specialty():
    assert specialty_for_taxonomy("207RC0000X") == "Cardiology"
    assert specialty_for_taxonomy("207N00000X") == "Dermatology"
    assert specialty_for_taxonomy("000000000X") is None


def test_folder_name_is_filesystem_safe():
    assert folder_name("Cardiology") == "Cardiology"
    assert "/" not in folder_name("Obstetrics & Gynecology")
    assert " " not in folder_name("Internal Medicine")
    assert folder_name("Obstetrics & Gynecology") == "Obstetrics-and-Gynecology"
