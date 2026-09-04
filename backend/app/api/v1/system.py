from fastapi import APIRouter

from ...config import get_settings
from ...runtime.capacity import resolve_capacity

router = APIRouter(tags=["system"])


@router.get("/health")
def health() -> dict:
    return {"status": "ok"}


@router.get("/readyz")
def readyz() -> dict:
    return {"status": "ready"}


@router.get("/version")
def version() -> dict:
    return {
        "api_version": "v1",
        "service": "clinical-note-labeller",
        "model": get_settings().openai_mini_model_id,
    }


@router.get("/capacity")
def capacity() -> dict:
    plan = resolve_capacity()
    return {
        "source": plan.source,
        "cpu_count": plan.hardware.cpu_count,
        "memory_bytes": plan.hardware.memory_bytes,
        "gpu_count": plan.hardware.gpu_count,
        "gpus": [{"index": g.index, "name": g.name, "backend": g.backend}
                 for g in plan.hardware.gpus],
        "file_concurrency": plan.file_concurrency,
        "celery_concurrency": plan.celery_concurrency,
        "parse_concurrency": plan.parse_concurrency,
        "detect_concurrency": plan.detect_concurrency,
        "llm_sync_concurrency": plan.llm_sync_concurrency,
        "gpu_batch_size": plan.gpu_batch_size,
        "ocr_workers": plan.ocr_workers,
    }
