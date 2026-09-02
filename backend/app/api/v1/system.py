from fastapi import APIRouter

from ...config import get_settings

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
