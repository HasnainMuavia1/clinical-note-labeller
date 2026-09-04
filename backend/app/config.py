from functools import lru_cache
from pathlib import Path
from typing import Annotated

from pydantic import field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    openai_api_key: str | None = None
    openai_mini_model_id: str = "gpt-5.4-mini"
    llama_cloud_api_key: str | None = None
    llama_parse_tier: str = "standard"

    # NoDecode: pydantic-settings would otherwise JSON-decode the env value before
    # the validator below gets to split the comma-separated form.
    api_keys: Annotated[list[str], NoDecode] = []
    database_url: str = "postgresql+psycopg://labeller:labeller@postgres:5432/labeller"
    redis_url: str = "redis://redis:6379/0"

    workspace_root: Path = Path("/data/workspace")
    reference_root: Path = Path("/data/reference")
    sandbox_url: str = "http://parser-sandbox:8081"

    s3_endpoint: str = "http://minio:9000"
    s3_access_key: str = "minioadmin"
    s3_secret_key: str = "minioadmin"
    s3_bucket: str = "uploads"

    code_evidence_threshold: float = 1.0
    specialty_confidence_threshold: float = 0.65
    llm_batch_min_files: int = 10
    file_concurrency: int = 0
    celery_concurrency: int = 0
    max_upload_bytes: int = 5 * 1024**3

    @field_validator("api_keys", mode="before")
    @classmethod
    def _split_keys(cls, v):
        if isinstance(v, str):
            return [k.strip() for k in v.split(",") if k.strip()]
        return v


@lru_cache
def get_settings() -> Settings:
    return Settings()
