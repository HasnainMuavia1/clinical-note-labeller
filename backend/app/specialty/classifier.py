from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path

from ..config import get_settings
from .taxonomy import SPECIALTIES, normalize_specialty

log = logging.getLogger(__name__)

MAX_NOTE_CHARS = 12_000

SPECIALTY_SCHEMA: dict = {
    "name": "specialty_label",
    "strict": True,
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "required": ["specialty", "confidence", "rationale"],
        "properties": {
            "specialty": {"type": "string", "enum": list(SPECIALTIES)},
            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
            "rationale": {"type": "string"},
        },
    },
}

SYSTEM_PROMPT = (
    "You label clinical notes with the single clinical specialty that best describes the "
    "care documented. Choose exactly one value from this closed list:\n"
    + ", ".join(SPECIALTIES)
    + "\nUse 'Unclassified' when the note does not clearly belong to one specialty. "
    "Report confidence between 0 and 1 and a one-sentence rationale citing note evidence. "
    "Never invent a specialty outside the list."
)


@dataclass(frozen=True)
class ClassificationRequest:
    file_id: str
    text: str


@dataclass(frozen=True)
class Classification:
    file_id: str
    specialty: str
    confidence: float
    rationale: str
    method: str


def _client():
    from openai import OpenAI

    return OpenAI(api_key=get_settings().openai_api_key)


def _async_client():
    from openai import AsyncOpenAI

    return AsyncOpenAI(api_key=get_settings().openai_api_key)


def build_prompt(text: str) -> list[dict]:
    excerpt = text.strip()[:MAX_NOTE_CHARS]
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": f"Clinical note:\n\n{excerpt}"},
    ]


def _parse_payload(file_id: str, content: str, method: str) -> Classification:
    try:
        data = json.loads(content)
    except (json.JSONDecodeError, TypeError):
        return Classification(file_id, "Unclassified", 0.0, "unparseable model output", method)
    return Classification(
        file_id=file_id,
        specialty=normalize_specialty(data.get("specialty")),
        confidence=float(data.get("confidence") or 0.0),
        rationale=str(data.get("rationale") or "")[:300],
        method=method,
    )


def _unconfigured(requests: list[ClassificationRequest], method: str) -> list[Classification]:
    return [Classification(r.file_id, "Unclassified", 0.0,
                           "OPENAI_API_KEY is not configured", method) for r in requests]


async def classify_sync(requests: list[ClassificationRequest]) -> list[Classification]:
    settings = get_settings()
    if not settings.openai_api_key:
        # Degrade to Unclassified so plan_placement raises a low-confidence approval
        # instead of failing the whole job.
        log.warning("OPENAI_API_KEY is not configured; %d files need manual labelling",
                    len(requests))
        return _unconfigured(requests, "llm_sync")

    client = _async_client()
    model = settings.openai_mini_model_id
    results: list[Classification] = []
    for request in requests:
        try:
            completion = await client.chat.completions.create(
                model=model,
                messages=build_prompt(request.text),
                response_format={"type": "json_schema", "json_schema": SPECIALTY_SCHEMA},
            )
            results.append(
                _parse_payload(request.file_id, completion.choices[0].message.content, "llm_sync")
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("sync classification failed for %s: %s", request.file_id, exc)
            results.append(
                Classification(request.file_id, "Unclassified", 0.0, f"error: {exc}", "llm_sync")
            )
    return results


def submit_batch(requests: list[ClassificationRequest], workdir: Path) -> str:
    model = get_settings().openai_mini_model_id
    payload_path = Path(workdir) / "batch_input.jsonl"
    payload_path.parent.mkdir(parents=True, exist_ok=True)
    with payload_path.open("w", encoding="utf-8") as fh:
        for request in requests:
            fh.write(json.dumps({
                "custom_id": request.file_id,
                "method": "POST",
                "url": "/v1/chat/completions",
                "body": {
                    "model": model,
                    "messages": build_prompt(request.text),
                    "response_format": {"type": "json_schema", "json_schema": SPECIALTY_SCHEMA},
                },
            }) + "\n")

    client = _client()
    with payload_path.open("rb") as fh:
        uploaded = client.files.create(file=fh, purpose="batch")
    batch = client.batches.create(input_file_id=uploaded.id, endpoint="/v1/chat/completions",
                                  completion_window="24h")
    log.info("submitted OpenAI batch %s with %d requests", batch.id, len(requests))
    return batch.id


def poll_batch(batch_id: str) -> str:
    return _client().batches.retrieve(batch_id).status


def fetch_batch_results(batch_id: str) -> list[Classification]:
    client = _client()
    batch = client.batches.retrieve(batch_id)
    if not batch.output_file_id:
        return []
    raw = client.files.content(batch.output_file_id).read()
    results: list[Classification] = []
    for line in raw.decode("utf-8").splitlines():
        if not line.strip():
            continue
        record = json.loads(line)
        custom_id = record.get("custom_id", "")
        response = record.get("response") or {}
        if response.get("status_code") != 200:
            results.append(
                Classification(custom_id, "Unclassified", 0.0, "batch request failed", "llm_batch")
            )
            continue
        content = response["body"]["choices"][0]["message"]["content"]
        results.append(_parse_payload(custom_id, content, "llm_batch"))
    return results


async def classify(requests: list[ClassificationRequest], workdir: Path
                   ) -> tuple[list[Classification] | None, str | None]:
    """Sync for small jobs, Batch API for large ones."""
    import sys

    module = sys.modules[__name__]
    if not requests:
        return [], None
    settings = get_settings()
    if not settings.openai_api_key or len(requests) < settings.llm_batch_min_files:
        return await module.classify_sync(requests), None
    return None, module.submit_batch(requests, workdir)
