import json
from types import SimpleNamespace

import pytest

from app.specialty import classifier as mod
from app.specialty.classifier import (
    SPECIALTY_SCHEMA,
    Classification,
    ClassificationRequest,
    build_prompt,
    classify,
    classify_sync,
    fetch_batch_results,
)


def test_prompt_constrains_the_model_to_the_closed_list():
    messages = build_prompt("Patient with chest pain and elevated troponin.")
    system = messages[0]["content"]
    assert "Cardiology" in system
    assert "Unclassified" in system
    enum = SPECIALTY_SCHEMA["schema"]["properties"]["specialty"]["enum"]
    assert "Cardiology" in enum and "Wizardry" not in enum


def test_prompt_truncates_very_long_notes():
    messages = build_prompt("x" * 100_000)
    assert len(messages[1]["content"]) < 20_000


async def test_sync_classification_parses_structured_output(monkeypatch):
    payload = {"specialty": "Cardiology", "confidence": 0.92, "rationale": "troponin, ECG"}

    class FakeCompletions:
        async def create(self, **kwargs):
            assert kwargs["response_format"]["json_schema"]["name"] == "specialty_label"
            message = SimpleNamespace(content=json.dumps(payload))
            return SimpleNamespace(choices=[SimpleNamespace(message=message)])

    monkeypatch.setattr(mod, "_async_client", lambda: SimpleNamespace(
        chat=SimpleNamespace(completions=FakeCompletions())))

    results = await classify_sync([ClassificationRequest("f1", "chest pain")])
    assert results[0] == Classification("f1", "Cardiology", 0.92, "troponin, ECG", "llm_sync")


async def test_sync_classification_runs_requests_in_parallel(monkeypatch):
    import asyncio
    import time

    inflight = 0
    peak = 0

    class FakeCompletions:
        async def create(self, **kwargs):
            nonlocal inflight, peak
            inflight += 1
            peak = max(peak, inflight)
            await asyncio.sleep(0.08)
            inflight -= 1
            message = SimpleNamespace(content=json.dumps(
                {"specialty": "Cardiology", "confidence": 0.9, "rationale": "r"}))
            return SimpleNamespace(choices=[SimpleNamespace(message=message)])

    monkeypatch.setattr(mod, "_async_client", lambda: SimpleNamespace(
        chat=SimpleNamespace(completions=FakeCompletions())))
    monkeypatch.setattr(mod, "resolve_llm_sync_concurrency", lambda: 8)

    started = time.monotonic()
    reqs = [ClassificationRequest(f"f{i}", "note") for i in range(6)]
    results = await classify_sync(reqs)
    elapsed = time.monotonic() - started

    assert [r.file_id for r in results] == [f"f{i}" for i in range(6)]
    assert peak >= 4
    assert elapsed < 0.35


async def test_unknown_specialty_from_the_model_is_normalized(monkeypatch):
    payload = {"specialty": "Cardio Stuff", "confidence": 0.9, "rationale": "r"}

    class FakeCompletions:
        async def create(self, **kwargs):
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(payload)))])

    monkeypatch.setattr(mod, "_async_client", lambda: SimpleNamespace(
        chat=SimpleNamespace(completions=FakeCompletions())))

    results = await classify_sync([ClassificationRequest("f1", "text")])
    assert results[0].specialty == "Unclassified"


def test_fetch_batch_results_parses_jsonl(monkeypatch):
    line = {
        "custom_id": "f7",
        "response": {"status_code": 200, "body": {"choices": [
            {"message": {"content": json.dumps(
                {"specialty": "Dermatology", "confidence": 0.81, "rationale": "rash"})}}
        ]}},
    }
    content = json.dumps(line).encode()

    class FakeFiles:
        def content(self, file_id):
            return SimpleNamespace(read=lambda: content)

    class FakeBatches:
        def retrieve(self, batch_id):
            return SimpleNamespace(status="completed", output_file_id="out-1", error_file_id=None)

    monkeypatch.setattr(mod, "_client", lambda: SimpleNamespace(files=FakeFiles(), batches=FakeBatches()))

    results = fetch_batch_results("batch-1")
    assert results == [Classification("f7", "Dermatology", 0.81, "rash", "llm_batch")]


def test_fetch_batch_results_skips_a_bad_line_and_keeps_the_rest(monkeypatch):
    good = {
        "custom_id": "f7",
        "response": {"status_code": 200, "body": {"choices": [
            {"message": {"content": json.dumps(
                {"specialty": "Dermatology", "confidence": 0.81, "rationale": "rash"})}}
        ]}},
    }
    content = b"not-json\n" + json.dumps(good).encode() + b"\n"

    class FakeFiles:
        def content(self, file_id):
            return SimpleNamespace(read=lambda: content)

    class FakeBatches:
        def retrieve(self, batch_id):
            return SimpleNamespace(status="completed", output_file_id="out-1", error_file_id=None)

    monkeypatch.setattr(mod, "_client", lambda: SimpleNamespace(files=FakeFiles(), batches=FakeBatches()))

    results = fetch_batch_results("batch-1")
    assert results == [Classification("f7", "Dermatology", 0.81, "rash", "llm_batch")]


async def test_classify_uses_sync_below_the_batch_threshold(monkeypatch, tmp_path):
    async def fake_sync(reqs):
        return [Classification(r.file_id, "Cardiology", 0.9, "r", "llm_sync") for r in reqs]

    monkeypatch.setattr(mod, "classify_sync", fake_sync)
    results, batch_id = await classify([ClassificationRequest("f1", "t")], tmp_path)
    assert batch_id is None and results[0].method == "llm_sync"


async def test_classify_uses_batch_at_or_above_the_threshold(monkeypatch, tmp_path):
    monkeypatch.setattr(mod, "submit_batch", lambda reqs, workdir: "batch-42")
    reqs = [ClassificationRequest(f"f{i}", "t") for i in range(10)]
    results, batch_id = await classify(reqs, tmp_path)
    assert results is None and batch_id == "batch-42"


async def test_missing_api_key_degrades_instead_of_raising(monkeypatch):
    from app.config import get_settings

    get_settings.cache_clear()
    monkeypatch.setenv("OPENAI_API_KEY", "")

    def explode():
        raise AssertionError("the OpenAI client must not be constructed without a key")

    monkeypatch.setattr(mod, "_async_client", explode)

    results = await classify_sync([ClassificationRequest("f1", "chest pain")])
    assert results[0].specialty == "Unclassified"
    assert results[0].confidence == 0.0
    assert "OPENAI_API_KEY" in results[0].rationale
    get_settings.cache_clear()


async def test_missing_api_key_skips_the_batch_path(monkeypatch, tmp_path):
    from app.config import get_settings

    get_settings.cache_clear()
    monkeypatch.setenv("OPENAI_API_KEY", "")
    monkeypatch.setattr(mod, "submit_batch",
                        lambda reqs, workdir: pytest.fail("batch must not be submitted"))

    reqs = [ClassificationRequest(f"f{i}", "t") for i in range(20)]
    results, batch_id = await classify(reqs, tmp_path)
    assert batch_id is None
    assert all(r.specialty == "Unclassified" for r in results)
    get_settings.cache_clear()
