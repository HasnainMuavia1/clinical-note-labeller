from types import SimpleNamespace

from app.agent.hydrate import hydrate_files, is_retryable


def test_hydrate_reuses_cached_parse_and_skips_done_files(tmp_path, monkeypatch):
    from app.workspace import parse_cache

    sha = "abc123"
    parse_cache.save(tmp_path, sha, {
        "text": "Dx: I10", "parser": "pypdf", "ok": True,
        "parse_trail": [{"parser": "pypdf", "ok": True, "reason": None}],
    })
    prior = SimpleNamespace(
        file_id="old-1", source_path="bundle.zip!/a.pdf", sha256=sha,
        status="parsed", parser="pypdf", parse_trail=[], specialty="Cardiology",
        has_codes=True, code_hits=[{"code": "I10"}], code_rejected=[], npis=[],
        confidence=0.9, method="npi", output_path=None,
    )
    monkeypatch.setattr("app.agent.hydrate.get_repository",
                        lambda: SimpleNamespace(list_files=lambda _id: [prior]))

    files = hydrate_files("job-1", [{
        "file_id": "new-1", "source_path": "bundle.zip!/a.pdf", "sha256": sha,
        "filename": "a.pdf", "ok": False, "text": "", "parser": None,
    }], tmp_path)

    assert files[0]["ok"] is True
    assert files[0]["text"] == "Dx: I10"
    assert files[0]["specialty"] == "Cardiology"
    assert files[0]["file_id"] == "old-1"


def test_hydrate_leaves_failed_files_to_retry(tmp_path, monkeypatch):
    prior = SimpleNamespace(
        file_id="old-2", source_path="bundle.zip!/b.pdf", sha256="dead",
        status="unparsed", parser="none",
        parse_trail=[{"parser": "ocr", "ok": False,
                      "reason": "RemoteProtocolError: Server disconnected"}],
        specialty=None, has_codes=False, code_hits=[], code_rejected=[], npis=[],
        confidence=0.0, method=None, output_path=None,
    )
    monkeypatch.setattr("app.agent.hydrate.get_repository",
                        lambda: SimpleNamespace(list_files=lambda _id: [prior]))

    files = hydrate_files("job-1", [{
        "file_id": "new-2", "source_path": "bundle.zip!/b.pdf", "sha256": "dead",
        "filename": "b.pdf", "ok": False, "text": "",
    }], tmp_path)

    assert files[0]["ok"] is False
    assert is_retryable(files[0]) is True


def test_hydrate_matches_prior_row_by_sha256_when_source_path_differs(tmp_path, monkeypatch):
    prior = SimpleNamespace(
        file_id="old-sha", source_path="old-prefix!/c.pdf", sha256="ccc",
        status="parsed", parser="ocr", parse_trail=[],
        specialty=None, has_codes=False, code_hits=[], code_rejected=[], npis=[],
        confidence=0.0, method=None, output_path=None,
    )
    monkeypatch.setattr("app.agent.hydrate.get_repository",
                        lambda: SimpleNamespace(list_files=lambda _id: [prior]))
    files = hydrate_files("job-1", [{
        "file_id": "new-sha", "source_path": "workspace/c.pdf", "sha256": "ccc",
        "filename": "c.pdf", "ok": False, "text": "",
    }], tmp_path)
    assert files[0]["ok"] is True
    assert files[0].get("_already_parsed") is True
    assert files[0].get("_resume_parser") != "ocr"


def test_hydrate_does_not_replay_ocr_for_already_parsed_scans(tmp_path, monkeypatch):
    prior = SimpleNamespace(
        file_id="old-ocr", source_path="bundle.zip!/scan.pdf", sha256="ocr1",
        status="parsed", parser="ocr", parse_trail=[{"parser": "ocr", "ok": True}],
        specialty=None, has_codes=False, code_hits=[], code_rejected=[], npis=[],
        confidence=0.0, method=None, output_path=None,
    )
    monkeypatch.setattr("app.agent.hydrate.get_repository",
                        lambda: SimpleNamespace(list_files=lambda _id: [prior]))
    files = hydrate_files("job-1", [{
        "file_id": "new-ocr", "source_path": "bundle.zip!/scan.pdf", "sha256": "ocr1",
        "filename": "scan.pdf", "ok": False, "text": "",
    }], tmp_path)
    assert files[0]["ok"] is True
    assert files[0].get("_resume_parser") != "ocr"
    assert files[0].get("_already_parsed") is True


def test_hydrate_marks_parsed_files_to_replay_the_winning_parser(tmp_path, monkeypatch):
    prior = SimpleNamespace(
        file_id="old-3", source_path="bundle.zip!/c.pdf", sha256="ccc",
        status="parsed", parser="pypdf", parse_trail=[],
        specialty=None, has_codes=False, code_hits=[], code_rejected=[], npis=[],
        confidence=0.0, method=None, output_path=None,
    )
    monkeypatch.setattr("app.agent.hydrate.get_repository",
                        lambda: SimpleNamespace(list_files=lambda _id: [prior]))
    files = hydrate_files("job-1", [{
        "file_id": "new-3", "source_path": "bundle.zip!/c.pdf", "sha256": "ccc",
        "filename": "c.pdf", "ok": False, "text": "",
    }], tmp_path)
    assert files[0]["ok"] is False
    assert files[0]["_resume_parser"] == "pypdf"


def test_thread_and_disconnect_errors_are_retryable():
    assert is_retryable({
        "parse_trail": [{"ok": False, "reason": "RuntimeError: can't start new thread"}],
    })
    assert is_retryable({
        "parse_trail": [{"ok": False, "reason": "libgomp: Thread creation failed"}],
    })
    assert not is_retryable({
        "parse_trail": [{"ok": False, "reason": "no extractable text"}],
        "skipped": False,
    })
    assert not is_retryable({"skipped": True, "skip_reason": "note.pdf: not a clinical note"})
