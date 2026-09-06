from app.specialty.folder_hint import folder_compare, hint_from_source_path


def test_exact_specialty_folder_is_a_hint():
    hint = hint_from_source_path("bundle.zip!/Cardiology/188.pdf")
    assert hint is not None
    assert hint.folder == "Cardiology"
    assert hint.expected_specialty == "Cardiology"


def test_nested_practice_folder_maps_to_specialty():
    hint = hint_from_source_path(
        "clinical note.zip!/clinical note/new notes/Star Orthopedics/Notes/188.pdf"
    )
    assert hint is not None
    assert hint.folder == "Star Orthopedics"
    assert hint.expected_specialty == "Orthopedic Surgery"


def test_surgery_practice_folder_maps_to_general_surgery():
    hint = hint_from_source_path(
        "clinical note.zip!/clinical note/FLOYD SURGERY/IMAGING 0154.pdf"
    )
    assert hint is not None
    assert hint.folder == "FLOYD SURGERY"
    assert hint.expected_specialty == "General Surgery"


def test_abbreviated_specialty_folder_is_a_hint():
    hint = hint_from_source_path("bundle.zip!/cardio/a.txt")
    assert hint is not None
    assert hint.expected_specialty == "Cardiology"


def test_modified_surgery_folder_is_not_general_surgery():
    hint = hint_from_source_path("bundle.zip!/Neuro Surgery/a.pdf")
    assert hint is not None
    assert hint.expected_specialty == "Neurosurgery"


def test_patient_or_generic_folders_are_not_hints():
    assert hint_from_source_path(
        "clinical note.zip!/clinical note/old notes/Ashli gobert/1.pdf"
    ) is None
    assert hint_from_source_path(
        "clinical note.zip!/clinical note/new notes/ahmed/New folder/2.pdf"
    ) is None
    assert hint_from_source_path("note.pdf") is None


def test_compare_after_assign_agrees_and_disagrees():
    match = folder_compare(
        "bundle.zip!/Star Orthopedics/Notes/a.pdf", "Orthopedic Surgery"
    )
    assert match["folder_label"] == "Star Orthopedics"
    assert match["expected_specialty"] == "Orthopedic Surgery"
    assert match["label_match"] is True

    miss = folder_compare("bundle.zip!/Cardiology/a.pdf", "Dermatology")
    assert miss["expected_specialty"] == "Cardiology"
    assert miss["label_match"] is False

    pending = folder_compare("bundle.zip!/Cardiology/a.pdf", None)
    assert pending["expected_specialty"] == "Cardiology"
    assert pending["label_match"] is None

    none = folder_compare("bundle.zip!/ahmed/a.pdf", "Family Medicine")
    assert none["expected_specialty"] is None
    assert none["label_match"] is None
