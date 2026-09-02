# Clinical Note Labeller Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a dockerized, agentic service that ingests clinical notes (PDF/DOCX/text/ZIP), detects medical codes, classifies specialty, and files each note into `output/{with-codes|without-codes}/<Specialty>/`.

**Architecture:** FastAPI `/api/v1` accepts uploads and hands jobs to a Celery worker. The worker runs a LangGraph `StateGraph` checkpointed in Postgres, so long-running OpenAI Batch calls and human approvals park on `interrupt()` and resume without losing work. Document parsing happens in a separate no-egress sandbox container; only the worker holds API keys. All filesystem writes go through a trusted-root-scoped LangChain `FileManagementToolkit`.

**Tech Stack:** Python 3.12, FastAPI, Pydantic v2, SQLAlchemy 2 + Alembic, Postgres 16, Celery 5 + Redis 7, LangGraph + `langgraph-checkpoint-postgres`, LangChain `FileManagementToolkit`, pypdf, python-docx, openpyxl, LlamaParse, Tesseract, OpenAI Batch API, MinIO, React 18 + Vite + TypeScript, Docker Compose.

**Spec:** `docs/superpowers/specs/2026-09-02-clinical-note-labeller-design.md`

## Global Constraints

- **No git.** The user has explicitly asked that this project not be initialized as a git repository. Tasks end with a verification step, not a commit.
- **Python 3.12**, FastAPI, Pydantic v2 (`pydantic-settings` for config). Type hints on every public function.
- **Secrets only from `.env`**, read via `app.config.Settings`. Never hardcode `OPENAI_API_KEY`, `LLAMA_CLOUD_API_KEY`, or `API_KEYS`. `.env` is listed in `.gitignore` and `.dockerignore`.
- **Model id** comes from `OPENAI_MINI_MODEL_ID` (default `gpt-5.4-mini`). Never hardcode a model string in application code.
- **API versioning:** every route lives under `/api/v1`. The v1 router is self-contained in `backend/app/api/v1/` so a future v2 can co-exist.
- **Errors:** RFC 7807 `application/problem+json` for every non-2xx response.
- **Trusted root:** the agent may only read/write under `WORKSPACE_ROOT` (default `/data/workspace`). Any path escaping it is a hard denial with an audit entry — never an approvable action.
- **NPI is not a code.** A note containing only an NPI is `without-codes`.
- **Specialty folder names** come only from `backend/app/specialty/nucc_specialties.json`, plus the literal `Unclassified`.
- **Reference data paths** (mounted read-only into the worker at `/data/reference`):
  - `cpt-codes/cpt.xlsx` — sheet `PPRRVU2026_Jan_nonQPP`, columns `HCPCS`, `DESCRIPTION`, `CODE`; 16,978 codes; header on row 1.
  - `cpt-codes/cpt-codes.txt` — HCPCS Level II, 16,734 lines, fixed width, variable leading whitespace; code is `line.strip()[:5]`.
  - `ict-10-codes/icd10cm_order_2026.txt` — 98,186 lines; order number, code (no decimal point), valid flag, short desc, long desc.
- **Testing:** pytest, TDD. Every task writes a failing test first. Network calls are mocked with `respx`; no test may hit OpenAI, LlamaParse, or the NPI Registry.

---

## File Structure

```
backend/
  pyproject.toml
  Dockerfile
  alembic.ini
  app/
    main.py               FastAPI app factory, middleware wiring
    config.py             Settings (pydantic-settings)
    logging_setup.py      structured JSON logs + request-id contextvar
    errors.py             RFC 7807 problem responses + exception handlers
    security.py           X-API-Key dependency + per-key rate limiter
    storage.py            MinIO/S3 client
    tasks.py              Celery app + run_job task
    db/
      base.py             DeclarativeBase
      session.py          engine + session factories
      models.py           Job, JobFile, Approval, AuditEntry, NpiCache
      repository.py       data access used by API and agent
    api/v1/
      router.py           APIRouter aggregation
      schemas.py          Pydantic request/response models
      jobs.py  approvals.py  codes.py  specialties.py  system.py
    codes/
      dictionaries.py     loaders -> CodeDictionaries
      patterns.py         compiled regexes + candidate extraction
      modifiers.json      bundled CPT/HCPCS modifier list
      evidence.py         positive/negative context scoring
      detector.py         detect_codes() -> DocumentCodeResult
    specialty/
      nucc_specialties.json   closed specialty list
      nucc_map.json           NUCC taxonomy code -> specialty
      taxonomy.py         load + lookup
      npi.py              Luhn validation + NPI Registry client
      classifier.py       OpenAI batch + sync specialty classification
    parsing/
      sandbox_client.py   HTTP client for the parser sandbox
      llamaparse.py       LlamaParse adapter (egress, worker only)
      chain.py            4-hop extraction chain
    workspace/
      paths.py            trusted-root validation
      archive.py          safe recursive ZIP extraction
      filetools.py        FileManagementToolkit wrapper + guarded ops
      manifest.py         manifest.jsonl + labels.csv writers
    agent/
      state.py            JobState TypedDict
      graph.py            StateGraph assembly + checkpointer
      nodes.py            node implementations
      approvals.py        interrupt payloads + resume helpers
  tests/
sandbox/
  Dockerfile
  app.py                  no-egress FastAPI: POST /parse
frontend/
  package.json  vite.config.ts  index.html
  src/
    main.tsx  App.tsx  api/client.ts
    pages/ UploadPage.tsx JobsPage.tsx JobDetailPage.tsx ApprovalsPage.tsx
    components/ FileTree.tsx StageProgress.tsx CodeEvidence.tsx ApprovalCard.tsx
docker-compose.yml
.env.example
```

---

### Task 1: Project skeleton, config, and the v1 system endpoints

**Files:**
- Create: `backend/pyproject.toml`, `backend/app/__init__.py`, `backend/app/config.py`, `backend/app/logging_setup.py`, `backend/app/errors.py`, `backend/app/security.py`, `backend/app/api/v1/router.py`, `backend/app/api/v1/system.py`, `backend/app/main.py`, `.env.example`, `.gitignore`
- Test: `backend/tests/test_system_api.py`, `backend/tests/conftest.py`

**Interfaces:**
- Consumes: nothing
- Produces:
  - `app.config.Settings` with fields `openai_api_key: str | None`, `openai_mini_model_id: str`, `llama_cloud_api_key: str | None`, `api_keys: list[str]`, `database_url: str`, `redis_url: str`, `workspace_root: Path`, `reference_root: Path`, `code_evidence_threshold: float`, `specialty_confidence_threshold: float`, `llm_batch_min_files: int`, `sandbox_url: str`, `s3_endpoint: str`, `s3_access_key: str`, `s3_secret_key: str`, `s3_bucket: str`
  - `app.config.get_settings() -> Settings` (lru_cached)
  - `app.errors.problem(status: int, title: str, detail: str, type_: str = "about:blank", **extra) -> JSONResponse`
  - `app.errors.ProblemException(status, title, detail, type_="about:blank")`
  - `app.security.require_api_key` — FastAPI dependency returning the caller's key id
  - `app.main.create_app() -> FastAPI`
  - Routes: `GET /api/v1/health`, `GET /api/v1/readyz`, `GET /api/v1/version`

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/conftest.py
import os
import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("API_KEYS", "test-key")
os.environ.setdefault("DATABASE_URL", "postgresql+psycopg://u:p@localhost/db")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")

@pytest.fixture()
def client():
    from app.main import create_app
    return TestClient(create_app(), raise_server_exceptions=False)

@pytest.fixture()
def auth():
    return {"X-API-Key": "test-key"}
```

```python
# backend/tests/test_system_api.py
def test_health_is_public_and_ok(client):
    r = client.get("/api/v1/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"

def test_version_reports_v1(client):
    r = client.get("/api/v1/version")
    assert r.json()["api_version"] == "v1"

def test_protected_route_without_key_returns_problem_json(client):
    r = client.get("/api/v1/specialties")
    assert r.status_code == 401
    assert r.headers["content-type"].startswith("application/problem+json")
    body = r.json()
    assert body["title"] == "Unauthorized"
    assert body["status"] == 401

def test_request_id_header_is_echoed(client):
    r = client.get("/api/v1/health", headers={"X-Request-ID": "abc-123"})
    assert r.headers["X-Request-ID"] == "abc-123"

def test_unknown_route_returns_problem_json(client):
    r = client.get("/api/v1/nope")
    assert r.headers["content-type"].startswith("application/problem+json")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && pytest tests/test_system_api.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app'`

- [ ] **Step 3: Write `pyproject.toml`**

```toml
[project]
name = "clinical-note-labeller"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = [
  "fastapi>=0.115", "uvicorn[standard]>=0.32", "pydantic>=2.9", "pydantic-settings>=2.6",
  "sqlalchemy>=2.0", "alembic>=1.13", "psycopg[binary]>=3.2",
  "celery>=5.4", "redis>=5.2",
  "langgraph>=0.2.45", "langgraph-checkpoint-postgres>=2.0", "langchain-community>=0.3", "langchain-core>=0.3",
  "openai>=1.54", "httpx>=0.27", "tenacity>=9.0",
  "pypdf>=5.1", "python-docx>=1.1", "openpyxl>=3.1", "charset-normalizer>=3.4",
  "llama-parse>=0.5", "boto3>=1.35", "python-multipart>=0.0.12", "sse-starlette>=2.1",
  "prometheus-client>=0.21", "python-json-logger>=2.0",
]

[project.optional-dependencies]
dev = ["pytest>=8.3", "pytest-asyncio>=0.24", "respx>=0.21", "testcontainers[postgres]>=4.8", "ruff>=0.7"]

[tool.pytest.ini_options]
pythonpath = ["."]
asyncio_mode = "auto"

[tool.ruff]
line-length = 100
```

- [ ] **Step 4: Write `app/config.py`**

```python
from functools import lru_cache
from pathlib import Path
from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    openai_api_key: str | None = None
    openai_mini_model_id: str = "gpt-5.4-mini"
    llama_cloud_api_key: str | None = None

    api_keys: list[str] = []
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
```

- [ ] **Step 5: Write `app/errors.py`**

```python
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

PROBLEM_JSON = "application/problem+json"


class ProblemException(Exception):
    def __init__(self, status: int, title: str, detail: str, type_: str = "about:blank"):
        self.status, self.title, self.detail, self.type_ = status, title, detail, type_


def problem(status: int, title: str, detail: str, type_: str = "about:blank", **extra) -> JSONResponse:
    body = {"type": type_, "title": title, "status": status, "detail": detail, **extra}
    return JSONResponse(body, status_code=status, media_type=PROBLEM_JSON)


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ProblemException)
    async def _problem(_: Request, exc: ProblemException):
        return problem(exc.status, exc.title, exc.detail, exc.type_)

    @app.exception_handler(StarletteHTTPException)
    async def _http(_: Request, exc: StarletteHTTPException):
        titles = {401: "Unauthorized", 403: "Forbidden", 404: "Not Found", 429: "Too Many Requests"}
        return problem(exc.status_code, titles.get(exc.status_code, "Error"), str(exc.detail))

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, exc: RequestValidationError):
        return problem(422, "Unprocessable Entity", "Request validation failed", errors=exc.errors())

    @app.exception_handler(Exception)
    async def _unhandled(_: Request, exc: Exception):
        return problem(500, "Internal Server Error", "An unexpected error occurred")
```

- [ ] **Step 6: Write `app/logging_setup.py`**

```python
import logging
import uuid
from contextvars import ContextVar
from pythonjsonlogger import jsonlogger

request_id_var: ContextVar[str] = ContextVar("request_id", default="-")


class RequestIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_var.get()
        return True


def configure_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(jsonlogger.JsonFormatter("%(asctime)s %(levelname)s %(name)s %(request_id)s %(message)s"))
    handler.addFilter(RequestIdFilter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level)


def new_request_id() -> str:
    return str(uuid.uuid4())
```

- [ ] **Step 7: Write `app/security.py`**

```python
import time
from collections import defaultdict, deque
from fastapi import Header
from .config import get_settings
from .errors import ProblemException

_WINDOW_SECONDS = 60
_MAX_REQUESTS = 120
_hits: dict[str, deque[float]] = defaultdict(deque)


def require_api_key(x_api_key: str | None = Header(default=None)) -> str:
    settings = get_settings()
    if not x_api_key or x_api_key not in settings.api_keys:
        raise ProblemException(401, "Unauthorized", "A valid X-API-Key header is required.")
    now = time.monotonic()
    bucket = _hits[x_api_key]
    while bucket and now - bucket[0] > _WINDOW_SECONDS:
        bucket.popleft()
    if len(bucket) >= _MAX_REQUESTS:
        raise ProblemException(429, "Too Many Requests", "Rate limit exceeded for this API key.")
    bucket.append(now)
    return x_api_key
```

- [ ] **Step 8: Write `app/api/v1/system.py` and `app/api/v1/router.py`**

```python
# app/api/v1/system.py
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
    return {"api_version": "v1", "service": "clinical-note-labeller", "model": get_settings().openai_mini_model_id}
```

```python
# app/api/v1/router.py
from fastapi import APIRouter
from . import system

api_v1 = APIRouter(prefix="/api/v1")
api_v1.include_router(system.router)
```

- [ ] **Step 9: Write `app/main.py`**

```python
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from starlette.responses import Response

from .api.v1.router import api_v1
from .errors import install_error_handlers
from .logging_setup import configure_logging, new_request_id, request_id_var


def create_app() -> FastAPI:
    configure_logging()
    app = FastAPI(title="Clinical Note Labeller", version="1.0.0", openapi_url="/api/v1/openapi.json")
    app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

    @app.middleware("http")
    async def request_context(request: Request, call_next):
        rid = request.headers.get("X-Request-ID") or new_request_id()
        token = request_id_var.set(rid)
        try:
            response = await call_next(request)
        finally:
            request_id_var.reset(token)
        response.headers["X-Request-ID"] = rid
        response.headers["X-API-Version"] = "v1"
        return response

    install_error_handlers(app)
    app.include_router(api_v1)

    @app.get("/api/v1/metrics")
    def metrics() -> Response:
        return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

    return app


app = create_app()
```

- [ ] **Step 10: Add a placeholder protected route so the 401 test has a target**

Create `app/api/v1/specialties.py`:

```python
from fastapi import APIRouter, Depends
from ...security import require_api_key

router = APIRouter(tags=["specialties"], dependencies=[Depends(require_api_key)])


@router.get("/specialties")
def list_specialties() -> dict:
    return {"items": [], "count": 0}
```

Register it in `router.py`: `api_v1.include_router(specialties.router)`.

- [ ] **Step 11: Write `.env.example` and `.gitignore`**

```bash
cat > .env.example <<'ENV'
OPENAI_API_KEY=
OPENAI_MINI_MODEL_ID=gpt-5.4-mini
LLAMA_CLOUD_API_KEY=
API_KEYS=dev-key
DATABASE_URL=postgresql+psycopg://labeller:labeller@postgres:5432/labeller
REDIS_URL=redis://redis:6379/0
WORKSPACE_ROOT=/data/workspace
REFERENCE_ROOT=/data/reference
SANDBOX_URL=http://parser-sandbox:8081
S3_ENDPOINT=http://minio:9000
S3_ACCESS_KEY=minioadmin
S3_SECRET_KEY=minioadmin
S3_BUCKET=uploads
CODE_EVIDENCE_THRESHOLD=1.0
SPECIALTY_CONFIDENCE_THRESHOLD=0.65
LLM_BATCH_MIN_FILES=10
ENV

cat > .gitignore <<'IGN'
.env
__pycache__/
*.pyc
.venv/
node_modules/
dist/
data/
IGN
```

- [ ] **Step 12: Run tests to verify they pass**

Run: `cd backend && pytest tests/test_system_api.py -v`
Expected: 5 passed

---

### Task 2: Code dictionaries loader

**Files:**
- Create: `backend/app/codes/__init__.py`, `backend/app/codes/dictionaries.py`
- Test: `backend/tests/test_dictionaries.py`

**Interfaces:**
- Consumes: `app.config.Settings.reference_root`
- Produces:
  - `@dataclass(frozen=True) CodeDictionaries` with `cpt: frozenset[str]`, `hcpcs: frozenset[str]`, `icd10: frozenset[str]`, `descriptions: dict[str, str]`
  - `CodeDictionaries.contains(code: str) -> str | None` — returns `"cpt" | "hcpcs" | "icd10" | None`
  - `load_dictionaries(reference_root: Path) -> CodeDictionaries` (module-level `lru_cache`d wrapper `get_dictionaries()`)
  - `normalize_icd10(code: str) -> str` — uppercase, strip `.`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_dictionaries.py
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
    assert len(dicts.hcpcs) > 16000
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && pytest tests/test_dictionaries.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.codes'`

- [ ] **Step 3: Implement `app/codes/dictionaries.py`**

```python
from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import openpyxl

_ICD_LINE = re.compile(r"^\s*\d{5}\s+(?P<code>[A-Z0-9]{3,7})\s+(?P<valid>[01])\s+(?P<short>.{1,60})\s{2,}(?P<long>.+?)\s*$")


def normalize_icd10(code: str) -> str:
    return code.strip().upper().replace(".", "")


@dataclass(frozen=True)
class CodeDictionaries:
    cpt: frozenset[str]
    hcpcs: frozenset[str]
    icd10: frozenset[str]
    descriptions: dict[str, str]

    def contains(self, code: str) -> str | None:
        raw = code.strip().upper()
        if raw in self.cpt:
            return "cpt"
        if raw in self.hcpcs:
            return "hcpcs"
        if normalize_icd10(raw) in self.icd10:
            return "icd10"
        return None


def _load_cpt_xlsx(path: Path) -> tuple[set[str], dict[str, str]]:
    codes: set[str] = set()
    descriptions: dict[str, str] = {}
    ws = openpyxl.load_workbook(path, read_only=True, data_only=True).active
    for row in ws.iter_rows(min_row=2, values_only=True):
        if not row or not row[0]:
            continue
        code = str(row[0]).strip().upper()
        if not code:
            continue
        codes.add(code)
        if row[1]:
            descriptions.setdefault(code, str(row[1]).strip())
    return codes, descriptions


def _load_hcpcs_txt(path: Path) -> tuple[set[str], dict[str, str]]:
    codes: set[str] = set()
    descriptions: dict[str, str] = {}
    with path.open(encoding="latin-1") as fh:
        for line in fh:
            stripped = line.strip()
            if len(stripped) < 5:
                continue
            code = stripped[:5].upper()
            if not re.fullmatch(r"[A-Z0-9]{5}", code):
                continue
            codes.add(code)
            desc = stripped[8:].strip()
            if desc:
                descriptions.setdefault(code, desc[:120].strip())
    return codes, descriptions


def _load_icd10_order(path: Path) -> tuple[set[str], dict[str, str]]:
    codes: set[str] = set()
    descriptions: dict[str, str] = {}
    with path.open(encoding="latin-1") as fh:
        for line in fh:
            if len(line) < 20:
                continue
            code = line[6:13].strip().upper()
            if not re.fullmatch(r"[A-Z][0-9A-Z]{2,6}", code):
                continue
            codes.add(code)
            long_desc = line[77:].strip() or line[16:77].strip()
            if long_desc:
                descriptions.setdefault(code, long_desc)
    return codes, descriptions


def load_dictionaries(reference_root: Path) -> CodeDictionaries:
    cpt, cpt_desc = _load_cpt_xlsx(reference_root / "cpt-codes" / "cpt.xlsx")
    hcpcs, hcpcs_desc = _load_hcpcs_txt(reference_root / "cpt-codes" / "cpt-codes.txt")
    icd, icd_desc = _load_icd10_order(reference_root / "ict-10-codes" / "icd10cm_order_2026.txt")
    descriptions = {**hcpcs_desc, **cpt_desc, **icd_desc}
    return CodeDictionaries(frozenset(cpt), frozenset(hcpcs), frozenset(icd), descriptions)


@lru_cache
def get_dictionaries() -> CodeDictionaries:
    from ..config import get_settings
    return load_dictionaries(get_settings().reference_root)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && pytest tests/test_dictionaries.py -v`
Expected: 6 passed. If the ICD fixed-width offsets are wrong, print `repr(line[:30])` for the first line and adjust the `line[6:13]` slice — the code field starts after the 5-digit order number and one space.

---

### Task 3: Candidate patterns and NPI validation

**Files:**
- Create: `backend/app/codes/patterns.py`, `backend/app/codes/modifiers.json`
- Test: `backend/tests/test_patterns.py`

**Interfaces:**
- Consumes: nothing
- Produces:
  - `@dataclass(frozen=True) Candidate` with `text: str`, `kind: str` (`"cpt"|"hcpcs"|"icd10"|"modifier"|"npi"`), `start: int`, `end: int`
  - `extract_candidates(text: str) -> list[Candidate]`
  - `is_valid_npi(value: str) -> bool` — Luhn over `80840` + 9 digits, check digit last
  - `MODIFIERS: frozenset[str]` loaded from `modifiers.json`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_patterns.py
from app.codes.patterns import Candidate, MODIFIERS, extract_candidates, is_valid_npi


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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && pytest tests/test_patterns.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.codes.patterns'`

- [ ] **Step 3: Write `app/codes/modifiers.json`**

```json
{
  "numeric": ["22","23","24","25","26","27","32","33","47","50","51","52","53","54","55","56","57","58","59","62","63","66","73","74","76","77","78","79","80","81","82","90","91","92","93","95","96","97","99"],
  "alpha": ["AA","AD","AS","CR","CS","EA","EB","EC","ET","FA","F1","F2","F3","F4","F5","F6","F7","F8","F9","GA","GC","GE","GG","GJ","GN","GO","GP","GQ","GT","GV","GW","GX","GY","GZ","JW","JZ","KX","LC","LD","LM","LT","P1","P2","P3","P4","P5","P6","PA","PB","PC","PD","PN","PO","PT","Q0","Q1","Q5","Q6","QJ","QK","QW","QX","QY","QZ","RC","RD","RI","RT","SA","SG","TA","T1","T2","T3","T4","T5","T6","T7","T8","T9","TC","U1","U2","XE","XP","XS","XU"]
}
```

- [ ] **Step 4: Implement `app/codes/patterns.py`**

```python
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

_MODIFIER_FILE = Path(__file__).with_name("modifiers.json")


@lru_cache
def _load_modifiers() -> frozenset[str]:
    data = json.loads(_MODIFIER_FILE.read_text())
    return frozenset(data["numeric"]) | frozenset(data["alpha"])


MODIFIERS = _load_modifiers()

ICD10_RE = re.compile(r"\b[A-TV-Z][0-9][0-9A-Z](?:\.?[0-9A-Z]{1,4})?\b")
CPT_RE = re.compile(r"\b\d{4}[0-9FTUM]\b")
HCPCS_RE = re.compile(r"\b[A-V]\d{4}\b")
NPI_RE = re.compile(r"\b\d{10}\b")
ATTACHED_MODIFIER_RE = re.compile(r"\b(?:\d{4}[0-9FTUM]|[A-V]\d{4})\s*[-–]\s*([A-Z0-9]{2})\b")


@dataclass(frozen=True)
class Candidate:
    text: str
    kind: str
    start: int
    end: int


def is_valid_npi(value: str) -> bool:
    """NPI check digit: Luhn over the constant prefix 80840 plus the first 9 digits."""
    value = value.strip()
    if not re.fullmatch(r"\d{10}", value):
        return False
    digits = [int(d) for d in "80840" + value[:9]]
    total = 0
    for index, digit in enumerate(reversed(digits)):
        if index % 2 == 0:
            digit *= 2
            if digit > 9:
                digit -= 9
        total += digit
    return (total + int(value[9])) % 10 == 0


def extract_candidates(text: str) -> list[Candidate]:
    found: list[Candidate] = []
    seen: set[tuple[int, int, str]] = set()

    def add(match: re.Match[str], kind: str, group: int = 0) -> None:
        key = (match.start(group), match.end(group), kind)
        if key in seen:
            return
        seen.add(key)
        found.append(Candidate(match.group(group), kind, match.start(group), match.end(group)))

    for match in ICD10_RE.finditer(text):
        add(match, "icd10")
    for match in CPT_RE.finditer(text):
        add(match, "cpt")
    for match in HCPCS_RE.finditer(text):
        add(match, "hcpcs")
    for match in NPI_RE.finditer(text):
        if is_valid_npi(match.group(0)):
            add(match, "npi")
    for match in ATTACHED_MODIFIER_RE.finditer(text):
        if match.group(1) in MODIFIERS:
            add(match, "modifier", group=1)

    return sorted(found, key=lambda c: (c.start, c.kind))
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd backend && pytest tests/test_patterns.py -v`
Expected: 10 passed

---

### Task 4: Evidence scoring and the document code detector

**Files:**
- Create: `backend/app/codes/evidence.py`, `backend/app/codes/detector.py`
- Test: `backend/tests/test_detector.py`, `backend/tests/fixtures/notes/*.txt`

**Interfaces:**
- Consumes: `app.codes.patterns.Candidate`/`extract_candidates`, `app.codes.dictionaries.CodeDictionaries`
- Produces:
  - `@dataclass(frozen=True) CodeHit` with `code: str`, `kind: str`, `start: int`, `end: int`, `dictionary_hit: bool`, `rule: str`, `score: float`, `context: str`
  - `@dataclass(frozen=True) DocumentCodeResult` with `has_codes: bool`, `total_score: float`, `hits: list[CodeHit]`, `rejected: list[CodeHit]`, `npis: list[str]`
  - `detect_codes(text: str, dicts: CodeDictionaries, threshold: float = 1.0) -> DocumentCodeResult`
  - `score_candidate(text: str, candidate: Candidate, dicts: CodeDictionaries) -> tuple[float, str]`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_detector.py
from pathlib import Path
import pytest
from app.codes.detector import detect_codes
from app.codes.dictionaries import load_dictionaries

REF = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def dicts():
    return load_dictionaries(REF)


CODED_NOTE = """
ASSESSMENT AND PLAN
Diagnosis: E11.9 Type 2 diabetes mellitus without complications
Procedure Code: 99213 - Office/outpatient visit, established patient
Modifier: 99213-25
Drug administered: J1885
"""

UNCODED_NOTE = """
SUBJECTIVE
The patient is a 45 year old male who presents with three days of cough.
He lives at 1420 Oak Street, Beverly Hills, CA 90210. Phone 555 867 5309.
Vitals: BP 128/82, HR 72, Temp 98.6. He was last seen on 04/25 of this year.
Plan: rest, fluids, follow up in one week.
"""

NPI_ONLY_NOTE = """
Signed electronically by the attending physician.
Provider NPI 1234567893. Encounter completed.
The patient tolerated the visit well and was discharged home.
"""


def test_coded_note_is_flagged_with_codes(dicts):
    result = detect_codes(CODED_NOTE, dicts)
    assert result.has_codes is True
    codes = {h.code for h in result.hits}
    assert "E11.9" in codes or "E119" in codes
    assert "99213" in codes
    assert "J1885" in codes


def test_coded_note_detects_the_attached_modifier(dicts):
    result = detect_codes(CODED_NOTE, dicts)
    assert any(h.kind == "modifier" and h.code == "25" for h in result.hits)


def test_narrative_note_with_zip_and_phone_is_not_flagged(dicts):
    result = detect_codes(UNCODED_NOTE, dicts)
    assert result.has_codes is False, [h.code for h in result.hits]


def test_zip_code_after_state_is_rejected(dicts):
    result = detect_codes(UNCODED_NOTE, dicts)
    assert any(h.code == "90210" for h in result.rejected)


def test_npi_alone_does_not_make_a_note_coded(dicts):
    result = detect_codes(NPI_ONLY_NOTE, dicts)
    assert result.has_codes is False
    assert "1234567893" in result.npis


def test_unknown_code_with_explicit_cue_is_still_accepted(dicts):
    text = "Billing summary\nCPT: 0777T experimental procedure performed."
    result = detect_codes(text, dicts)
    assert result.has_codes is True
    hit = next(h for h in result.hits if h.code == "0777T")
    assert hit.dictionary_hit is False
    assert hit.rule == "structural+cue"


def test_every_hit_records_auditable_evidence(dicts):
    result = detect_codes(CODED_NOTE, dicts)
    for hit in result.hits:
        assert hit.rule
        assert hit.context
        assert hit.score > 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && pytest tests/test_detector.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.codes.detector'`

- [ ] **Step 3: Implement `app/codes/evidence.py`**

```python
from __future__ import annotations

import re

from .dictionaries import CodeDictionaries
from .patterns import Candidate

CONTEXT_WINDOW = 60

POSITIVE_CUES = re.compile(
    r"(?i)\b(cpt|hcpcs|icd[-\s]?10(?:[-\s]?cm)?|dx|diagnos[ei]s\s+code|procedure\s+code|"
    r"billing|claim|charge|modifier|units?\s+billed|assessment\s+and\s+plan|coding\s+summary|"
    r"e/?m\s+code|revenue\s+code)\b"
)

NEGATIVE_PATTERNS = [
    ("zip", re.compile(r"(?i)\b(?:A[KLRZ]|C[AOT]|D[CE]|FL|GA|HI|I[ADLN]|K[SY]|LA|M[ADEINOST]|"
                       r"N[CDEHJMVY]|O[HKR]|PA|RI|S[CD]|T[NX]|UT|V[AT]|W[AIVY])[,\s]+\d{5}\b")),
    ("phone", re.compile(r"\b\d{3}[-.\s]\d{3}[-.\s]\d{4}\b")),
    ("date", re.compile(r"\b\d{1,2}/\d{1,2}(?:/\d{2,4})?\b")),
    ("vitals", re.compile(r"(?i)\b(?:bp|hr|rr|temp|spo2|weight|height|bmi)\b[^\n]{0,20}$")),
    ("identifier", re.compile(r"(?i)\b(?:mrn|account|acct|policy|member|phone|fax|room|zip)\b[^\n]{0,15}$")),
    ("age", re.compile(r"(?i)\b(?:is|aged?)\s*$")),
]


def context_of(text: str, candidate: Candidate, window: int = CONTEXT_WINDOW) -> str:
    return text[max(0, candidate.start - window): candidate.end + window]


def has_positive_cue(text: str, candidate: Candidate) -> bool:
    line_start = text.rfind("\n", 0, candidate.start) + 1
    before = text[max(line_start, candidate.start - CONTEXT_WINDOW): candidate.start]
    return bool(POSITIVE_CUES.search(before))


def negative_reason(text: str, candidate: Candidate) -> str | None:
    before = text[max(0, candidate.start - 30): candidate.start]
    around = text[max(0, candidate.start - 30): candidate.end + 10]
    for name, pattern in NEGATIVE_PATTERNS:
        target = before if name in {"vitals", "identifier", "age"} else around
        for match in pattern.finditer(target):
            if name in {"vitals", "identifier", "age"}:
                return name
            offset = max(0, candidate.start - 30)
            if match.start() + offset <= candidate.start and match.end() + offset >= candidate.end:
                return name
    return None


def score_candidate(text: str, candidate: Candidate, dicts: CodeDictionaries) -> tuple[float, str]:
    """Return (score, rule). A non-positive score means the candidate is rejected."""
    if candidate.kind == "npi":
        return 0.0, "npi-not-a-code"

    if candidate.kind == "modifier":
        return 0.5, "attached-modifier"

    source = dicts.contains(candidate.text)
    cue = has_positive_cue(text, candidate)
    negative = negative_reason(text, candidate)

    if source and cue:
        return 1.5, "dictionary+cue"
    if source and not negative:
        return 1.0, "dictionary"
    if source and negative:
        return 0.0, f"rejected:{negative}"
    if cue and not negative:
        return 1.0, "structural+cue"
    return 0.0, f"rejected:{negative or 'no-evidence'}"
```

- [ ] **Step 4: Implement `app/codes/detector.py`**

```python
from __future__ import annotations

from dataclasses import dataclass, field

from .dictionaries import CodeDictionaries
from .evidence import context_of, score_candidate
from .patterns import extract_candidates


@dataclass(frozen=True)
class CodeHit:
    code: str
    kind: str
    start: int
    end: int
    dictionary_hit: bool
    rule: str
    score: float
    context: str


@dataclass(frozen=True)
class DocumentCodeResult:
    has_codes: bool
    total_score: float
    hits: list[CodeHit] = field(default_factory=list)
    rejected: list[CodeHit] = field(default_factory=list)
    npis: list[str] = field(default_factory=list)


def detect_codes(text: str, dicts: CodeDictionaries, threshold: float = 1.0) -> DocumentCodeResult:
    hits: list[CodeHit] = []
    rejected: list[CodeHit] = []
    npis: list[str] = []
    total = 0.0

    for candidate in extract_candidates(text):
        if candidate.kind == "npi":
            if candidate.text not in npis:
                npis.append(candidate.text)
            continue

        score, rule = score_candidate(text, candidate, dicts)
        hit = CodeHit(
            code=candidate.text,
            kind=candidate.kind,
            start=candidate.start,
            end=candidate.end,
            dictionary_hit=dicts.contains(candidate.text) is not None,
            rule=rule,
            score=score,
            context=context_of(text, candidate).replace("\n", " ").strip(),
        )
        if score > 0:
            hits.append(hit)
            total += score
        else:
            rejected.append(hit)

    substantive = sum(h.score for h in hits if h.kind != "modifier")
    return DocumentCodeResult(
        has_codes=substantive >= threshold,
        total_score=round(total, 3),
        hits=hits,
        rejected=rejected,
        npis=npis,
    )
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd backend && pytest tests/test_detector.py -v`
Expected: 7 passed. If `test_narrative_note_with_zip_and_phone_is_not_flagged` fails, print the surviving hits and extend `NEGATIVE_PATTERNS` — do not lower the threshold to make it pass.

- [ ] **Step 6: Run the full suite**

Run: `cd backend && pytest -v`
Expected: all tests from Tasks 1–4 pass.

---

### Task 5: Trusted-root path validation and safe archive extraction

**Files:**
- Create: `backend/app/workspace/__init__.py`, `backend/app/workspace/paths.py`, `backend/app/workspace/archive.py`
- Test: `backend/tests/test_paths.py`, `backend/tests/test_archive.py`

**Interfaces:**
- Consumes: nothing
- Produces:
  - `class PathEscapeError(Exception)`
  - `resolve_within(root: Path, candidate: str | Path) -> Path` — raises `PathEscapeError` on escape
  - `is_within(root: Path, candidate: Path) -> bool`
  - `@dataclass(frozen=True) ExtractedEntry` with `path: Path`, `source_path: str`, `size: int`
  - `class ArchiveError(Exception)`
  - `extract_archive(archive: Path, dest: Path, *, max_total_bytes: int = 5 * 1024**3, max_entries: int = 50_000, max_depth: int = 5) -> list[ExtractedEntry]` — recursive, refuses zip-slip and zip-bombs

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/test_paths.py
from pathlib import Path
import pytest
from app.workspace.paths import PathEscapeError, is_within, resolve_within


def test_resolves_a_child_path(tmp_path):
    assert resolve_within(tmp_path, "a/b.txt") == (tmp_path / "a" / "b.txt").resolve()


def test_rejects_parent_traversal(tmp_path):
    with pytest.raises(PathEscapeError):
        resolve_within(tmp_path, "../outside.txt")


def test_rejects_absolute_path_outside_root(tmp_path):
    with pytest.raises(PathEscapeError):
        resolve_within(tmp_path, "/etc/passwd")


def test_rejects_symlink_escape(tmp_path):
    outside = tmp_path.parent / "outside_dir"
    outside.mkdir(exist_ok=True)
    root = tmp_path / "root"
    root.mkdir()
    (root / "link").symlink_to(outside, target_is_directory=True)
    with pytest.raises(PathEscapeError):
        resolve_within(root, "link/secret.txt")


def test_is_within_reports_boolean(tmp_path):
    assert is_within(tmp_path, tmp_path / "x")
    assert not is_within(tmp_path, Path("/etc"))
```

```python
# backend/tests/test_archive.py
import zipfile
import pytest
from app.workspace.archive import ArchiveError, extract_archive


def make_zip(path, entries):
    with zipfile.ZipFile(path, "w") as zf:
        for name, data in entries.items():
            zf.writestr(name, data)
    return path


def test_extracts_flat_entries(tmp_path):
    archive = make_zip(tmp_path / "a.zip", {"note1.txt": "hello", "note2.txt": "world"})
    dest = tmp_path / "out"
    entries = extract_archive(archive, dest)
    assert {e.path.name for e in entries} == {"note1.txt", "note2.txt"}
    assert (dest / "note1.txt").read_text() == "hello"


def test_preserves_source_path_for_nested_folders(tmp_path):
    archive = make_zip(tmp_path / "a.zip", {"cardio/note1.txt": "x", "derm/sub/note2.txt": "y"})
    entries = extract_archive(archive, tmp_path / "out")
    sources = {e.source_path for e in entries}
    assert "cardio/note1.txt" in sources
    assert "derm/sub/note2.txt" in sources


def test_extracts_nested_archives_recursively(tmp_path):
    inner = make_zip(tmp_path / "inner.zip", {"deep.txt": "deep"})
    outer = tmp_path / "outer.zip"
    with __import__("zipfile").ZipFile(outer, "w") as zf:
        zf.write(inner, "nested/inner.zip")
    entries = extract_archive(outer, tmp_path / "out")
    assert any(e.path.name == "deep.txt" for e in entries)
    assert any("inner.zip" in e.source_path for e in entries)


def test_rejects_zip_slip(tmp_path):
    archive = tmp_path / "evil.zip"
    with __import__("zipfile").ZipFile(archive, "w") as zf:
        zf.writestr("../escaped.txt", "bad")
    with pytest.raises(ArchiveError, match="outside"):
        extract_archive(archive, tmp_path / "out")


def test_rejects_zip_bomb_over_byte_budget(tmp_path):
    archive = make_zip(tmp_path / "bomb.zip", {"big.txt": "A" * 100_000})
    with pytest.raises(ArchiveError, match="budget"):
        extract_archive(archive, tmp_path / "out", max_total_bytes=1000)


def test_rejects_too_many_entries(tmp_path):
    archive = make_zip(tmp_path / "many.zip", {f"n{i}.txt": "x" for i in range(20)})
    with pytest.raises(ArchiveError, match="entries"):
        extract_archive(archive, tmp_path / "out", max_entries=5)


def test_rejects_excessive_nesting_depth(tmp_path):
    current = make_zip(tmp_path / "l0.zip", {"leaf.txt": "x"})
    for level in range(1, 4):
        nxt = tmp_path / f"l{level}.zip"
        with __import__("zipfile").ZipFile(nxt, "w") as zf:
            zf.write(current, f"l{level - 1}.zip")
        current = nxt
    with pytest.raises(ArchiveError, match="depth"):
        extract_archive(current, tmp_path / "out", max_depth=2)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && pytest tests/test_paths.py tests/test_archive.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.workspace'`

- [ ] **Step 3: Implement `app/workspace/paths.py`**

```python
from __future__ import annotations

from pathlib import Path


class PathEscapeError(Exception):
    """Raised when a path would resolve outside the trusted root."""


def _resolved_root(root: Path) -> Path:
    return Path(root).resolve()


def is_within(root: Path, candidate: Path) -> bool:
    try:
        Path(candidate).resolve().relative_to(_resolved_root(root))
    except (ValueError, OSError):
        return False
    return True


def resolve_within(root: Path, candidate: str | Path) -> Path:
    root_resolved = _resolved_root(root)
    raw = Path(candidate)
    target = raw if raw.is_absolute() else root_resolved / raw

    resolved = target.resolve()
    if not is_within(root_resolved, resolved):
        raise PathEscapeError(f"{candidate!r} resolves outside the trusted root {root_resolved}")

    # Reject a symlinked ancestor that points outside the root.
    probe = target
    while probe != root_resolved and probe.parent != probe:
        if probe.is_symlink() and not is_within(root_resolved, probe.resolve()):
            raise PathEscapeError(f"{candidate!r} traverses a symlink leaving {root_resolved}")
        probe = probe.parent
    return resolved
```

- [ ] **Step 4: Implement `app/workspace/archive.py`**

```python
from __future__ import annotations

import zipfile
from dataclasses import dataclass
from pathlib import Path

from .paths import PathEscapeError, resolve_within

ARCHIVE_SUFFIXES = {".zip"}


class ArchiveError(Exception):
    """Raised when an archive is unsafe or exceeds its resource budget."""


@dataclass(frozen=True)
class ExtractedEntry:
    path: Path
    source_path: str
    size: int


class _Budget:
    def __init__(self, max_total_bytes: int, max_entries: int) -> None:
        self.remaining_bytes = max_total_bytes
        self.remaining_entries = max_entries

    def take(self, size: int) -> None:
        self.remaining_entries -= 1
        if self.remaining_entries < 0:
            raise ArchiveError("archive exceeds the maximum number of entries")
        self.remaining_bytes -= size
        if self.remaining_bytes < 0:
            raise ArchiveError("archive exceeds the uncompressed byte budget")


def _extract(archive: Path, dest: Path, prefix: str, depth: int, max_depth: int, budget: _Budget,
             out: list[ExtractedEntry]) -> None:
    if depth > max_depth:
        raise ArchiveError(f"archive nesting exceeds max depth {max_depth}")

    dest.mkdir(parents=True, exist_ok=True)
    try:
        zf = zipfile.ZipFile(archive)
    except zipfile.BadZipFile as exc:
        raise ArchiveError(f"corrupt archive {archive.name}") from exc

    with zf:
        for info in zf.infolist():
            if info.is_dir():
                continue
            try:
                target = resolve_within(dest, info.filename)
            except PathEscapeError as exc:
                raise ArchiveError(f"entry {info.filename!r} resolves outside the extraction root") from exc

            budget.take(info.file_size)
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info) as src, target.open("wb") as dst:
                written = 0
                while chunk := src.read(1 << 20):
                    written += len(chunk)
                    if written > info.file_size + (1 << 20):
                        raise ArchiveError("declared size mismatch; refusing to continue")
                    dst.write(chunk)

            source_path = f"{prefix}{info.filename}"
            if target.suffix.lower() in ARCHIVE_SUFFIXES:
                nested_dest = target.parent / f"{target.stem}__unpacked"
                _extract(target, nested_dest, f"{source_path}!/", depth + 1, max_depth, budget, out)
                target.unlink(missing_ok=True)
            else:
                out.append(ExtractedEntry(target, source_path, info.file_size))


def extract_archive(archive: Path, dest: Path, *, max_total_bytes: int = 5 * 1024**3,
                    max_entries: int = 50_000, max_depth: int = 5) -> list[ExtractedEntry]:
    out: list[ExtractedEntry] = []
    _extract(Path(archive), Path(dest), "", 1, max_depth, _Budget(max_total_bytes, max_entries), out)
    return out
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd backend && pytest tests/test_paths.py tests/test_archive.py -v`
Expected: 12 passed

---

### Task 6: The no-egress parser sandbox service

**Files:**
- Create: `sandbox/app.py`, `sandbox/requirements.txt`, `sandbox/Dockerfile`
- Test: `backend/tests/test_sandbox_app.py`

**Interfaces:**
- Consumes: nothing (standalone service; it shares no code with `backend/app`)
- Produces: `POST /parse` accepting `multipart/form-data` field `file`, returning
  `{"text": str, "pages": int, "parser": "pypdf"|"python-docx"|"text"|"ocr", "ok": bool, "reason": str | None}`;
  plus `GET /health`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_sandbox_app.py
import io
import sys
from pathlib import Path
import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "sandbox"))


@pytest.fixture()
def sandbox_client():
    from app import create_app  # sandbox/app.py
    return TestClient(create_app(), raise_server_exceptions=False)


def test_health(sandbox_client):
    assert sandbox_client.get("/health").json()["status"] == "ok"


def test_parses_plain_text(sandbox_client):
    files = {"file": ("note.txt", io.BytesIO(b"Patient presents with cough."), "text/plain")}
    body = sandbox_client.post("/parse", files=files).json()
    assert body["ok"] is True
    assert body["parser"] == "text"
    assert "cough" in body["text"]


def test_parses_latin1_text_without_crashing(sandbox_client):
    files = {"file": ("note.txt", io.BytesIO("Café visit".encode("latin-1")), "text/plain")}
    body = sandbox_client.post("/parse", files=files).json()
    assert body["ok"] is True
    assert "visit" in body["text"]


def test_reports_failure_for_unreadable_pdf(sandbox_client):
    files = {"file": ("broken.pdf", io.BytesIO(b"%PDF-1.4 not really a pdf"), "application/pdf")}
    body = sandbox_client.post("/parse", files=files).json()
    assert body["ok"] is False
    assert body["reason"]


def test_empty_file_is_reported_as_failure(sandbox_client):
    files = {"file": ("empty.txt", io.BytesIO(b"   \n  "), "text/plain")}
    body = sandbox_client.post("/parse", files=files).json()
    assert body["ok"] is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && pytest tests/test_sandbox_app.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app'` resolving to `sandbox/app.py`

- [ ] **Step 3: Implement `sandbox/app.py`**

```python
"""Hardened document parser. Runs with no network egress and no API keys."""
from __future__ import annotations

import io
import shutil
import subprocess
import tempfile
from pathlib import Path

from charset_normalizer import from_bytes
from fastapi import FastAPI, File, UploadFile

MAX_BYTES = 512 * 1024 * 1024
TEXT_SUFFIXES = {".txt", ".md", ".rtf", ".csv", ".json", ".log", ".text", ""}


def _parse_pdf(data: bytes) -> tuple[str, int]:
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(data))
    pages = [page.extract_text() or "" for page in reader.pages]
    return "\n".join(pages), len(pages)


def _parse_docx(data: bytes) -> tuple[str, int]:
    import docx

    document = docx.Document(io.BytesIO(data))
    parts = [p.text for p in document.paragraphs]
    for table in document.tables:
        for row in table.rows:
            parts.append("\t".join(cell.text for cell in row.cells))
    return "\n".join(parts), 1


def _parse_text(data: bytes) -> tuple[str, int]:
    best = from_bytes(data).best()
    return (str(best) if best else data.decode("utf-8", errors="replace")), 1


def _parse_ocr(data: bytes, suffix: str) -> tuple[str, int]:
    if not shutil.which("tesseract"):
        raise RuntimeError("tesseract is not installed in this image")
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / f"input{suffix or '.pdf'}"
        src.write_bytes(data)
        if suffix.lower() == ".pdf":
            if not shutil.which("pdftoppm"):
                raise RuntimeError("pdftoppm is not installed in this image")
            subprocess.run(["pdftoppm", "-r", "200", "-png", str(src), f"{tmp}/page"],
                           check=True, timeout=600, capture_output=True)
            images = sorted(Path(tmp).glob("page*.png"))
        else:
            images = [src]
        chunks = []
        for image in images:
            proc = subprocess.run(["tesseract", str(image), "stdout"],
                                  check=True, timeout=600, capture_output=True)
            chunks.append(proc.stdout.decode("utf-8", errors="replace"))
        return "\n".join(chunks), len(images)


def create_app() -> FastAPI:
    app = FastAPI(title="Parser Sandbox", version="1.0.0")

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok"}

    @app.post("/parse")
    async def parse(file: UploadFile = File(...), ocr: bool = False) -> dict:
        data = await file.read()
        if len(data) > MAX_BYTES:
            return {"text": "", "pages": 0, "parser": "none", "ok": False, "reason": "file exceeds size limit"}

        suffix = Path(file.filename or "").suffix.lower()
        try:
            if ocr:
                text, pages, parser = *_parse_ocr(data, suffix), "ocr"
            elif suffix == ".pdf":
                text, pages, parser = *_parse_pdf(data), "pypdf"
            elif suffix in {".docx", ".doc"}:
                text, pages, parser = *_parse_docx(data), "python-docx"
            elif suffix in TEXT_SUFFIXES:
                text, pages, parser = *_parse_text(data), "text"
            else:
                return {"text": "", "pages": 0, "parser": "none", "ok": False,
                        "reason": f"unsupported extension {suffix!r}"}
        except Exception as exc:  # noqa: BLE001 - the sandbox reports, never raises
            return {"text": "", "pages": 0, "parser": "none", "ok": False, "reason": f"{type(exc).__name__}: {exc}"}

        if not text.strip():
            return {"text": "", "pages": pages, "parser": parser, "ok": False, "reason": "no extractable text"}
        return {"text": text, "pages": pages, "parser": parser, "ok": True, "reason": None}

    return app


app = create_app()
```

- [ ] **Step 4: Write `sandbox/requirements.txt` and `sandbox/Dockerfile`**

```text
fastapi>=0.115
uvicorn[standard]>=0.32
python-multipart>=0.0.12
pypdf>=5.1
python-docx>=1.1
charset-normalizer>=3.4
```

```dockerfile
FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
      tesseract-ocr poppler-utils \
    && rm -rf /var/lib/apt/lists/*

RUN useradd --create-home --uid 10001 sandbox
WORKDIR /srv
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app.py .

USER sandbox
EXPOSE 8081
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8081"]
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd backend && pytest tests/test_sandbox_app.py -v`
Expected: 5 passed

---

### Task 7: The four-hop extraction chain

**Files:**
- Create: `backend/app/parsing/__init__.py`, `backend/app/parsing/sandbox_client.py`, `backend/app/parsing/llamaparse.py`, `backend/app/parsing/chain.py`
- Test: `backend/tests/test_parse_chain.py`

**Interfaces:**
- Consumes: `app.config.Settings.sandbox_url`, `Settings.llama_cloud_api_key`
- Produces:
  - `@dataclass(frozen=True) ParseAttempt` with `parser: str`, `ok: bool`, `reason: str | None`
  - `@dataclass(frozen=True) ParseResult` with `text: str`, `parser: str`, `pages: int`, `ok: bool`, `trail: list[ParseAttempt]`
  - `async parse_via_sandbox(path: Path, *, ocr: bool = False) -> ParseResult`
  - `async parse_via_llamaparse(path: Path) -> ParseResult`
  - `async parse_document(path: Path) -> ParseResult` — the full chain

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_parse_chain.py
import httpx
import pytest
import respx
from app.parsing import chain as chain_module
from app.parsing.chain import parse_document

SANDBOX = "http://parser-sandbox:8081/parse"


@pytest.fixture()
def note(tmp_path):
    p = tmp_path / "note.pdf"
    p.write_bytes(b"%PDF-1.4 stub")
    return p


@respx.mock
async def test_sandbox_success_short_circuits_the_chain(note, monkeypatch):
    route = respx.post(SANDBOX).mock(return_value=httpx.Response(
        200, json={"text": "Dx: E11.9", "pages": 1, "parser": "pypdf", "ok": True, "reason": None}))
    called = {"llama": False}
    async def never(_): called["llama"] = True
    monkeypatch.setattr(chain_module, "parse_via_llamaparse", never)

    result = await parse_document(note)
    assert result.ok and result.parser == "pypdf"
    assert called["llama"] is False
    assert route.called
    assert [a.parser for a in result.trail] == ["pypdf"]


@respx.mock
async def test_falls_through_to_llamaparse_when_sandbox_fails(note, monkeypatch):
    respx.post(SANDBOX).mock(return_value=httpx.Response(
        200, json={"text": "", "pages": 0, "parser": "none", "ok": False, "reason": "no extractable text"}))
    async def fake_llama(path):
        from app.parsing.chain import ParseAttempt, ParseResult
        return ParseResult("parsed by llama", "llamaparse", 1, True,
                           [ParseAttempt("llamaparse", True, None)])
    monkeypatch.setattr(chain_module, "parse_via_llamaparse", fake_llama)

    result = await parse_document(note)
    assert result.ok and result.parser == "llamaparse"
    assert [a.parser for a in result.trail][:2] == ["pypdf", "llamaparse"]


@respx.mock
async def test_falls_through_to_ocr_when_llamaparse_fails(note, monkeypatch):
    def sandbox_response(request):
        if b'name="ocr"' in request.content or b"ocr" in request.url.query:
            return httpx.Response(200, json={"text": "ocr text", "pages": 1, "parser": "ocr", "ok": True, "reason": None})
        return httpx.Response(200, json={"text": "", "pages": 0, "parser": "none", "ok": False, "reason": "empty"})
    respx.post(SANDBOX).mock(side_effect=sandbox_response)

    async def failing_llama(path):
        from app.parsing.chain import ParseAttempt, ParseResult
        return ParseResult("", "llamaparse", 0, False, [ParseAttempt("llamaparse", False, "no key")])
    monkeypatch.setattr(chain_module, "parse_via_llamaparse", failing_llama)

    result = await parse_document(note)
    assert result.parser == "ocr" and result.ok
    assert [a.parser for a in result.trail] == ["pypdf", "llamaparse", "ocr"]


@respx.mock
async def test_all_hops_failing_returns_not_ok_with_full_trail(note, monkeypatch):
    respx.post(SANDBOX).mock(return_value=httpx.Response(
        200, json={"text": "", "pages": 0, "parser": "none", "ok": False, "reason": "empty"}))
    async def failing_llama(path):
        from app.parsing.chain import ParseAttempt, ParseResult
        return ParseResult("", "llamaparse", 0, False, [ParseAttempt("llamaparse", False, "no key")])
    monkeypatch.setattr(chain_module, "parse_via_llamaparse", failing_llama)

    result = await parse_document(note)
    assert result.ok is False
    assert len(result.trail) == 3
    assert all(not a.ok for a in result.trail)


async def test_llamaparse_without_key_fails_fast(note, monkeypatch):
    from app.config import get_settings
    from app.parsing.chain import parse_via_llamaparse
    get_settings.cache_clear()
    monkeypatch.setenv("LLAMA_CLOUD_API_KEY", "")
    result = await parse_via_llamaparse(note)
    assert result.ok is False
    assert "key" in (result.trail[0].reason or "").lower()
    get_settings.cache_clear()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && pytest tests/test_parse_chain.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.parsing'`

- [ ] **Step 3: Implement `app/parsing/sandbox_client.py`**

```python
from __future__ import annotations

from pathlib import Path

import httpx

from ..config import get_settings

TIMEOUT = httpx.Timeout(600.0, connect=10.0)


async def call_sandbox(path: Path, *, ocr: bool = False) -> dict:
    url = f"{get_settings().sandbox_url.rstrip('/')}/parse"
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        with path.open("rb") as fh:
            response = await client.post(url, files={"file": (path.name, fh)}, params={"ocr": str(ocr).lower()})
    response.raise_for_status()
    return response.json()
```

- [ ] **Step 4: Implement `app/parsing/llamaparse.py`**

```python
from __future__ import annotations

import asyncio
from pathlib import Path

from ..config import get_settings


async def llamaparse_text(path: Path) -> str:
    """Parse a document with LlamaParse. Requires egress; worker-only."""
    from llama_parse import LlamaParse

    settings = get_settings()
    parser = LlamaParse(api_key=settings.llama_cloud_api_key, result_type="text", verbose=False)
    documents = await asyncio.to_thread(parser.load_data, str(path))
    return "\n".join(doc.text for doc in documents)
```

- [ ] **Step 5: Implement `app/parsing/chain.py`**

```python
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

from ..config import get_settings
from .llamaparse import llamaparse_text
from .sandbox_client import call_sandbox

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class ParseAttempt:
    parser: str
    ok: bool
    reason: str | None


@dataclass(frozen=True)
class ParseResult:
    text: str
    parser: str
    pages: int
    ok: bool
    trail: list[ParseAttempt] = field(default_factory=list)


async def parse_via_sandbox(path: Path, *, ocr: bool = False) -> ParseResult:
    try:
        payload = await call_sandbox(path, ocr=ocr)
    except Exception as exc:  # noqa: BLE001
        parser = "ocr" if ocr else "sandbox"
        return ParseResult("", parser, 0, False, [ParseAttempt(parser, False, f"{type(exc).__name__}: {exc}")])
    parser = payload.get("parser") or ("ocr" if ocr else "sandbox")
    if ocr:
        parser = "ocr"
    attempt = ParseAttempt(parser, bool(payload.get("ok")), payload.get("reason"))
    return ParseResult(payload.get("text", ""), parser, int(payload.get("pages", 0)), attempt.ok, [attempt])


async def parse_via_llamaparse(path: Path) -> ParseResult:
    if not get_settings().llama_cloud_api_key:
        return ParseResult("", "llamaparse", 0, False,
                           [ParseAttempt("llamaparse", False, "LLAMA_CLOUD_API_KEY is not configured")])
    try:
        text = await llamaparse_text(path)
    except Exception as exc:  # noqa: BLE001
        return ParseResult("", "llamaparse", 0, False,
                           [ParseAttempt("llamaparse", False, f"{type(exc).__name__}: {exc}")])
    ok = bool(text.strip())
    return ParseResult(text, "llamaparse", 1, ok,
                       [ParseAttempt("llamaparse", ok, None if ok else "no extractable text")])


async def parse_document(path: Path) -> ParseResult:
    """pypdf/docx/text -> LlamaParse -> OCR -> failure. Records every hop."""
    trail: list[ParseAttempt] = []

    primary = await parse_via_sandbox(path)
    trail.extend(primary.trail)
    if primary.ok:
        return ParseResult(primary.text, primary.parser, primary.pages, True, trail)

    secondary = await parse_via_llamaparse(path)
    trail.extend(secondary.trail)
    if secondary.ok:
        return ParseResult(secondary.text, secondary.parser, secondary.pages, True, trail)

    tertiary = await parse_via_sandbox(path, ocr=True)
    trail.extend(tertiary.trail)
    if tertiary.ok:
        return ParseResult(tertiary.text, "ocr", tertiary.pages, True, trail)

    log.warning("all parsers failed for %s", path.name)
    return ParseResult("", "none", 0, False, trail)
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `cd backend && pytest tests/test_parse_chain.py -v`
Expected: 5 passed

---

### Task 8: NUCC taxonomy and the NPI Registry client

**Files:**
- Create: `backend/app/specialty/__init__.py`, `backend/app/specialty/nucc_specialties.json`, `backend/app/specialty/nucc_map.json`, `backend/app/specialty/taxonomy.py`, `backend/app/specialty/npi.py`
- Modify: `backend/app/api/v1/specialties.py` (return the real list)
- Test: `backend/tests/test_taxonomy.py`, `backend/tests/test_npi.py`

**Interfaces:**
- Consumes: `app.codes.patterns.is_valid_npi`
- Produces:
  - `SPECIALTIES: tuple[str, ...]` — the closed list, including `"Unclassified"`
  - `is_known_specialty(name: str) -> bool`
  - `normalize_specialty(name: str) -> str` — returns a member of `SPECIALTIES`, else `"Unclassified"`
  - `specialty_for_taxonomy(taxonomy_code: str) -> str | None`
  - `folder_name(specialty: str) -> str` — filesystem-safe (`"Physical Medicine & Rehabilitation"` -> `"Physical-Medicine-and-Rehabilitation"`)
  - `@dataclass(frozen=True) NpiResult` with `npi: str`, `specialty: str | None`, `taxonomy_code: str | None`, `is_individual: bool`, `found: bool`
  - `async lookup_npi(npi: str, client: httpx.AsyncClient | None = None) -> NpiResult`
  - `async resolve_specialty_from_npis(npis: list[str]) -> NpiResult | None` — prefers an individual clinical taxonomy

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/test_taxonomy.py
from app.specialty.taxonomy import (SPECIALTIES, folder_name, is_known_specialty,
                                    normalize_specialty, specialty_for_taxonomy)


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
```

```python
# backend/tests/test_npi.py
import httpx
import pytest
import respx
from app.specialty.npi import lookup_npi, resolve_specialty_from_npis

API = "https://npiregistry.cms.hhs.gov/api/"


def registry_payload(taxonomy_code, desc, enumeration_type="NPI-1"):
    return {"result_count": 1, "results": [{
        "enumeration_type": enumeration_type,
        "taxonomies": [{"code": taxonomy_code, "desc": desc, "primary": True}],
    }]}


@respx.mock
async def test_lookup_maps_taxonomy_to_specialty():
    respx.get(API).mock(return_value=httpx.Response(200, json=registry_payload("207RC0000X", "Cardiovascular Disease")))
    result = await lookup_npi("1234567893")
    assert result.found is True
    assert result.specialty == "Cardiology"
    assert result.is_individual is True


@respx.mock
async def test_lookup_of_unknown_npi_reports_not_found():
    respx.get(API).mock(return_value=httpx.Response(200, json={"result_count": 0, "results": []}))
    result = await lookup_npi("1234567893")
    assert result.found is False
    assert result.specialty is None


async def test_lookup_rejects_an_invalid_npi_without_calling_the_api():
    result = await lookup_npi("1234567890")
    assert result.found is False


@respx.mock
async def test_resolver_prefers_an_individual_over_an_organization():
    def handler(request):
        number = request.url.params["number"]
        if number == "1234567893":
            return httpx.Response(200, json=registry_payload("261QP2300X", "Primary Care Clinic", "NPI-2"))
        return httpx.Response(200, json=registry_payload("207N00000X", "Dermatology", "NPI-1"))
    respx.get(API).mock(side_effect=handler)

    result = await resolve_specialty_from_npis(["1234567893", "1023011178"])
    assert result is not None
    assert result.specialty == "Dermatology"


@respx.mock
async def test_resolver_returns_none_when_nothing_resolves():
    respx.get(API).mock(return_value=httpx.Response(200, json={"result_count": 0, "results": []}))
    assert await resolve_specialty_from_npis(["1234567893"]) is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && pytest tests/test_taxonomy.py tests/test_npi.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.specialty'`

- [ ] **Step 3: Write `app/specialty/nucc_specialties.json`**

```json
["Allergy and Immunology","Anesthesiology","Cardiology","Cardiothoracic Surgery","Critical Care","Dentistry","Dermatology","Emergency Medicine","Endocrinology","Family Medicine","Gastroenterology","General Surgery","Geriatrics","Hematology","Hospice and Palliative Medicine","Infectious Disease","Internal Medicine","Nephrology","Neurology","Neurosurgery","Obstetrics & Gynecology","Occupational Therapy","Oncology","Ophthalmology","Optometry","Oral and Maxillofacial Surgery","Orthopedic Surgery","Otolaryngology","Pain Medicine","Pathology","Pediatrics","Physical Medicine & Rehabilitation","Physical Therapy","Plastic Surgery","Podiatry","Psychiatry","Psychology","Pulmonology","Radiology","Rheumatology","Sleep Medicine","Speech Language Pathology","Sports Medicine","Urology","Vascular Surgery","Unclassified"]
```

- [ ] **Step 4: Write `app/specialty/nucc_map.json`**

Keys are NUCC taxonomy codes; values must be members of `nucc_specialties.json`.

```json
{
  "207K00000X": "Allergy and Immunology",
  "207KA0200X": "Allergy and Immunology",
  "207L00000X": "Anesthesiology",
  "207LP2900X": "Pain Medicine",
  "207RC0000X": "Cardiology",
  "207RI0011X": "Cardiology",
  "208G00000X": "Cardiothoracic Surgery",
  "207RC0200X": "Critical Care",
  "1223G0001X": "Dentistry",
  "207N00000X": "Dermatology",
  "207NS0135X": "Dermatology",
  "207P00000X": "Emergency Medicine",
  "207RE0101X": "Endocrinology",
  "207Q00000X": "Family Medicine",
  "207RG0100X": "Gastroenterology",
  "208600000X": "General Surgery",
  "207QG0300X": "Geriatrics",
  "207RH0000X": "Hematology",
  "207RH0003X": "Oncology",
  "207QH0002X": "Hospice and Palliative Medicine",
  "207RI0200X": "Infectious Disease",
  "207R00000X": "Internal Medicine",
  "207RN0300X": "Nephrology",
  "2084N0400X": "Neurology",
  "207T00000X": "Neurosurgery",
  "207V00000X": "Obstetrics & Gynecology",
  "225X00000X": "Occupational Therapy",
  "207RX0202X": "Oncology",
  "207W00000X": "Ophthalmology",
  "152W00000X": "Optometry",
  "1223S0112X": "Oral and Maxillofacial Surgery",
  "207X00000X": "Orthopedic Surgery",
  "207Y00000X": "Otolaryngology",
  "207ZP0102X": "Pathology",
  "208000000X": "Pediatrics",
  "208100000X": "Physical Medicine & Rehabilitation",
  "225100000X": "Physical Therapy",
  "208200000X": "Plastic Surgery",
  "213E00000X": "Podiatry",
  "2084P0800X": "Psychiatry",
  "103T00000X": "Psychology",
  "207RP1001X": "Pulmonology",
  "2085R0202X": "Radiology",
  "207RR0500X": "Rheumatology",
  "207RS0012X": "Sleep Medicine",
  "235Z00000X": "Speech Language Pathology",
  "207QS0010X": "Sports Medicine",
  "208800000X": "Urology",
  "2086S0129X": "Vascular Surgery",
  "261QP2300X": "Family Medicine"
}
```

- [ ] **Step 5: Implement `app/specialty/taxonomy.py`**

```python
from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path

_DIR = Path(__file__).parent
SPECIALTIES: tuple[str, ...] = tuple(json.loads((_DIR / "nucc_specialties.json").read_text()))
UNCLASSIFIED = "Unclassified"


@lru_cache
def _taxonomy_map() -> dict[str, str]:
    return json.loads((_DIR / "nucc_map.json").read_text())


@lru_cache
def _lower_index() -> dict[str, str]:
    return {s.lower(): s for s in SPECIALTIES}


def is_known_specialty(name: str) -> bool:
    return name in SPECIALTIES


def normalize_specialty(name: str | None) -> str:
    if not name:
        return UNCLASSIFIED
    return _lower_index().get(name.strip().lower(), UNCLASSIFIED)


def specialty_for_taxonomy(taxonomy_code: str | None) -> str | None:
    if not taxonomy_code:
        return None
    return _taxonomy_map().get(taxonomy_code.strip().upper())


def folder_name(specialty: str) -> str:
    cleaned = specialty.replace("&", "and")
    cleaned = re.sub(r"[^A-Za-z0-9]+", "-", cleaned).strip("-")
    return cleaned or UNCLASSIFIED
```

- [ ] **Step 6: Implement `app/specialty/npi.py`**

```python
from __future__ import annotations

import asyncio
from dataclasses import dataclass

import httpx

from ..codes.patterns import is_valid_npi
from .taxonomy import specialty_for_taxonomy

REGISTRY_URL = "https://npiregistry.cms.hhs.gov/api/"
TIMEOUT = httpx.Timeout(20.0, connect=10.0)


@dataclass(frozen=True)
class NpiResult:
    npi: str
    specialty: str | None
    taxonomy_code: str | None
    is_individual: bool
    found: bool


async def lookup_npi(npi: str, client: httpx.AsyncClient | None = None) -> NpiResult:
    if not is_valid_npi(npi):
        return NpiResult(npi, None, None, False, False)

    owns_client = client is None
    client = client or httpx.AsyncClient(timeout=TIMEOUT)
    try:
        response = await client.get(REGISTRY_URL, params={"version": "2.1", "number": npi})
        response.raise_for_status()
        payload = response.json()
    except Exception:  # noqa: BLE001 - registry failure must not fail the job
        return NpiResult(npi, None, None, False, False)
    finally:
        if owns_client:
            await client.aclose()

    results = payload.get("results") or []
    if not results:
        return NpiResult(npi, None, None, False, False)

    record = results[0]
    taxonomies = record.get("taxonomies") or []
    primary = next((t for t in taxonomies if t.get("primary")), taxonomies[0] if taxonomies else None)
    code = (primary or {}).get("code")
    return NpiResult(
        npi=npi,
        specialty=specialty_for_taxonomy(code),
        taxonomy_code=code,
        is_individual=record.get("enumeration_type") == "NPI-1",
        found=True,
    )


async def resolve_specialty_from_npis(npis: list[str]) -> NpiResult | None:
    """Prefer an individual clinician's primary taxonomy over an organization's."""
    if not npis:
        return None
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        results = await asyncio.gather(*(lookup_npi(n, client) for n in npis[:5]))

    resolved = [r for r in results if r.found and r.specialty]
    if not resolved:
        return None
    individual = next((r for r in resolved if r.is_individual), None)
    return individual or resolved[0]
```

- [ ] **Step 7: Make `/api/v1/specialties` return the real list**

```python
# app/api/v1/specialties.py
from fastapi import APIRouter, Depends
from ...security import require_api_key
from ...specialty.taxonomy import SPECIALTIES, folder_name

router = APIRouter(tags=["specialties"], dependencies=[Depends(require_api_key)])


@router.get("/specialties")
def list_specialties() -> dict:
    items = [{"name": s, "folder": folder_name(s)} for s in SPECIALTIES]
    return {"items": items, "count": len(items)}
```

- [ ] **Step 8: Run tests to verify they pass**

Run: `cd backend && pytest tests/test_taxonomy.py tests/test_npi.py tests/test_system_api.py -v`
Expected: 10 passed

---

### Task 9: OpenAI specialty classifier (batch + sync)

**Files:**
- Create: `backend/app/specialty/classifier.py`
- Test: `backend/tests/test_classifier.py`

**Interfaces:**
- Consumes: `app.config.Settings.openai_api_key`, `Settings.openai_mini_model_id`, `Settings.llm_batch_min_files`, `app.specialty.taxonomy`
- Produces:
  - `@dataclass(frozen=True) ClassificationRequest` with `file_id: str`, `text: str`
  - `@dataclass(frozen=True) Classification` with `file_id: str`, `specialty: str`, `confidence: float`, `rationale: str`, `method: str`
  - `build_prompt(text: str) -> list[dict]`
  - `SPECIALTY_SCHEMA: dict` — the JSON schema passed as a structured output
  - `async classify_sync(requests: list[ClassificationRequest]) -> list[Classification]`
  - `submit_batch(requests: list[ClassificationRequest], workdir: Path) -> str` — returns an OpenAI batch id
  - `poll_batch(batch_id: str) -> str` — returns `"validating"|"in_progress"|"completed"|"failed"|"expired"|"cancelled"`
  - `fetch_batch_results(batch_id: str) -> list[Classification]`
  - `async classify(requests: list[ClassificationRequest], workdir: Path) -> tuple[list[Classification] | None, str | None]` — returns `(results, None)` for the sync path or `(None, batch_id)` for the batch path

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_classifier.py
import json
from types import SimpleNamespace
import pytest
from app.specialty import classifier as mod
from app.specialty.classifier import (Classification, ClassificationRequest, SPECIALTY_SCHEMA,
                                      build_prompt, classify, classify_sync, fetch_batch_results)


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


async def test_unknown_specialty_from_the_model_is_normalized(monkeypatch):
    payload = {"specialty": "Cardio Stuff", "confidence": 0.9, "rationale": "r"}

    class FakeCompletions:
        async def create(self, **kwargs):
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(payload)))])

    monkeypatch.setattr(mod, "_async_client", lambda: SimpleNamespace(
        chat=SimpleNamespace(completions=FakeCompletions())))

    results = await classify_sync([ClassificationRequest("f1", "text")])
    assert results[0].specialty == "Unclassified"


def test_fetch_batch_results_parses_jsonl(monkeypatch, tmp_path):
    line = {
        "custom_id": "f7",
        "response": {"status_code": 200, "body": {"choices": [
            {"message": {"content": json.dumps({"specialty": "Dermatology", "confidence": 0.81, "rationale": "rash"})}}
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


async def test_classify_uses_sync_below_the_batch_threshold(monkeypatch, tmp_path):
    monkeypatch.setattr(mod, "classify_sync", lambda reqs: _fake_results(reqs))
    results, batch_id = await classify([ClassificationRequest("f1", "t")], tmp_path)
    assert batch_id is None and results[0].method == "llm_sync"


async def test_classify_uses_batch_at_or_above_the_threshold(monkeypatch, tmp_path):
    monkeypatch.setattr(mod, "submit_batch", lambda reqs, workdir: "batch-42")
    reqs = [ClassificationRequest(f"f{i}", "t") for i in range(10)]
    results, batch_id = await classify(reqs, tmp_path)
    assert results is None and batch_id == "batch-42"


async def _fake_results(reqs):
    return [Classification(r.file_id, "Cardiology", 0.9, "r", "llm_sync") for r in reqs]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && pytest tests/test_classifier.py -v`
Expected: FAIL — `ImportError: cannot import name 'classify' from 'app.specialty.classifier'`

- [ ] **Step 3: Implement `app/specialty/classifier.py`**

```python
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from functools import lru_cache
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
            "rationale": {"type": "string", "maxLength": 300},
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
    except json.JSONDecodeError:
        return Classification(file_id, "Unclassified", 0.0, "unparseable model output", method)
    return Classification(
        file_id=file_id,
        specialty=normalize_specialty(data.get("specialty")),
        confidence=float(data.get("confidence") or 0.0),
        rationale=str(data.get("rationale") or "")[:300],
        method=method,
    )


async def classify_sync(requests: list[ClassificationRequest]) -> list[Classification]:
    client = _async_client()
    model = get_settings().openai_mini_model_id
    results: list[Classification] = []
    for request in requests:
        try:
            completion = await client.chat.completions.create(
                model=model,
                messages=build_prompt(request.text),
                response_format={"type": "json_schema", "json_schema": SPECIALTY_SCHEMA},
            )
            results.append(_parse_payload(request.file_id, completion.choices[0].message.content, "llm_sync"))
        except Exception as exc:  # noqa: BLE001
            log.warning("sync classification failed for %s: %s", request.file_id, exc)
            results.append(Classification(request.file_id, "Unclassified", 0.0, f"error: {exc}", "llm_sync"))
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
            results.append(Classification(custom_id, "Unclassified", 0.0, "batch request failed", "llm_batch"))
            continue
        content = response["body"]["choices"][0]["message"]["content"]
        results.append(_parse_payload(custom_id, content, "llm_batch"))
    return results


async def classify(requests: list[ClassificationRequest], workdir: Path
                   ) -> tuple[list[Classification] | None, str | None]:
    """Sync for small jobs, Batch API for large ones."""
    if not requests:
        return [], None
    if len(requests) < get_settings().llm_batch_min_files:
        return await classify_sync(requests), None
    return None, submit_batch(requests, workdir)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && pytest tests/test_classifier.py -v`
Expected: 7 passed

- [ ] **Step 5: Run the full suite**

Run: `cd backend && pytest -v`
Expected: all tests from Tasks 1–9 pass.

---

### Task 10: Database models and repository

**Files:**
- Create: `backend/app/db/__init__.py`, `backend/app/db/base.py`, `backend/app/db/models.py`, `backend/app/db/session.py`, `backend/app/db/repository.py`, `backend/alembic.ini`, `backend/alembic/env.py`, `backend/alembic/versions/0001_initial.py`
- Test: `backend/tests/test_repository.py`

**Interfaces:**
- Consumes: `app.config.Settings.database_url`
- Produces:
  - Models `Job`, `JobFile`, `Approval`, `AuditEntry`, `NpiCache` (all on `Base`)
  - `JobStatus` / `ApprovalStatus` / `FileStatus` string enums
  - `session_scope() -> Iterator[Session]` context manager
  - `Repository` with: `create_job(job_id, api_key_id, original_filenames, idempotency_key) -> Job`, `get_job(job_id) -> Job | None`, `find_by_idempotency_key(key) -> Job | None`, `list_jobs(status=None, limit=50, cursor=None) -> tuple[list[Job], str | None]`, `update_job(job_id, **fields) -> Job`, `upsert_file(job_id, file_id, **fields) -> JobFile`, `list_files(job_id) -> list[JobFile]`, `create_approval(job_id, kind, payload) -> Approval`, `list_approvals(job_id, status=None) -> list[Approval]`, `decide_approval(approval_id, decision, note) -> Approval`, `audit(job_id, action, detail) -> AuditEntry`, `get_npi(npi) -> NpiCache | None`, `put_npi(npi, specialty, taxonomy_code, is_individual) -> NpiCache`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_repository.py
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app.db.base import Base
from app.db.models import ApprovalStatus, JobStatus
from app.db.repository import Repository


@pytest.fixture()
def repo():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    return Repository(sessionmaker(bind=engine, expire_on_commit=False))


def test_create_and_get_job(repo):
    job = repo.create_job("job-1", "key-a", ["a.pdf"], None)
    assert job.status == JobStatus.PENDING
    assert repo.get_job("job-1").id == "job-1"


def test_idempotency_key_lookup(repo):
    repo.create_job("job-1", "key-a", ["a.pdf"], "idem-1")
    assert repo.find_by_idempotency_key("idem-1").id == "job-1"
    assert repo.find_by_idempotency_key("nope") is None


def test_update_job_status_and_stage(repo):
    repo.create_job("job-1", "key-a", [], None)
    updated = repo.update_job("job-1", status=JobStatus.RUNNING, stage="parse", progress=0.25)
    assert updated.status == JobStatus.RUNNING
    assert updated.stage == "parse"


def test_upsert_file_is_idempotent(repo):
    repo.create_job("job-1", "key-a", [], None)
    repo.upsert_file("job-1", "f1", filename="a.pdf", specialty="Cardiology")
    repo.upsert_file("job-1", "f1", has_codes=True)
    files = repo.list_files("job-1")
    assert len(files) == 1
    assert files[0].specialty == "Cardiology"
    assert files[0].has_codes is True


def test_approval_lifecycle(repo):
    repo.create_job("job-1", "key-a", [], None)
    approval = repo.create_approval("job-1", "delete", {"path": "output/x.pdf"})
    assert approval.status == ApprovalStatus.PENDING
    pending = repo.list_approvals("job-1", status=ApprovalStatus.PENDING)
    assert len(pending) == 1
    decided = repo.decide_approval(approval.id, "approve", "looks fine")
    assert decided.status == ApprovalStatus.APPROVED
    assert repo.list_approvals("job-1", status=ApprovalStatus.PENDING) == []


def test_audit_entries_are_appended(repo):
    repo.create_job("job-1", "key-a", [], None)
    repo.audit("job-1", "path_escape_denied", {"path": "../etc/passwd"})
    repo.audit("job-1", "approval_granted", {"approval_id": "x"})
    job = repo.get_job("job-1")
    assert len(job.audit_entries) == 2


def test_npi_cache_roundtrip(repo):
    assert repo.get_npi("1234567893") is None
    repo.put_npi("1234567893", "Cardiology", "207RC0000X", True)
    assert repo.get_npi("1234567893").specialty == "Cardiology"


def test_list_jobs_paginates(repo):
    for i in range(5):
        repo.create_job(f"job-{i}", "key-a", [], None)
    page, cursor = repo.list_jobs(limit=2)
    assert len(page) == 2 and cursor is not None
    page2, _ = repo.list_jobs(limit=2, cursor=cursor)
    assert {j.id for j in page} & {j.id for j in page2} == set()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && pytest tests/test_repository.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.db'`

- [ ] **Step 3: Implement `app/db/base.py` and `app/db/models.py`**

```python
# app/db/base.py
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass
```

```python
# app/db/models.py
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import StrEnum

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import JSON

from .base import Base

JSONType = JSON().with_variant(JSONB(), "postgresql")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _uuid() -> str:
    return str(uuid.uuid4())


class JobStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    AWAITING_APPROVAL = "awaiting_approval"
    AWAITING_BATCH = "awaiting_batch"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class FileStatus(StrEnum):
    PENDING = "pending"
    PARSED = "parsed"
    UNPARSED = "unparsed"
    FILED = "filed"


class ApprovalStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    api_key_id: Mapped[str] = mapped_column(String(128))
    idempotency_key: Mapped[str | None] = mapped_column(String(128), unique=True, nullable=True)
    status: Mapped[str] = mapped_column(String(32), default=JobStatus.PENDING)
    stage: Mapped[str] = mapped_column(String(32), default="intake")
    progress: Mapped[float] = mapped_column(Float, default=0.0)
    original_filenames: Mapped[list] = mapped_column(JSONType, default=list)
    batch_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)

    files: Mapped[list["JobFile"]] = relationship(back_populates="job", cascade="all, delete-orphan")
    approvals: Mapped[list["Approval"]] = relationship(back_populates="job", cascade="all, delete-orphan")
    audit_entries: Mapped[list["AuditEntry"]] = relationship(back_populates="job", cascade="all, delete-orphan")


class JobFile(Base):
    __tablename__ = "job_files"
    __table_args__ = (UniqueConstraint("job_id", "file_id", name="uq_job_file"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), index=True)
    file_id: Mapped[str] = mapped_column(String(64), index=True)
    filename: Mapped[str] = mapped_column(String(512), default="")
    source_path: Mapped[str] = mapped_column(String(1024), default="")
    sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(32), default=FileStatus.PENDING)
    parser: Mapped[str | None] = mapped_column(String(32), nullable=True)
    parse_trail: Mapped[list] = mapped_column(JSONType, default=list)
    has_codes: Mapped[bool] = mapped_column(Boolean, default=False)
    code_hits: Mapped[list] = mapped_column(JSONType, default=list)
    code_rejected: Mapped[list] = mapped_column(JSONType, default=list)
    npis: Mapped[list] = mapped_column(JSONType, default=list)
    specialty: Mapped[str | None] = mapped_column(String(128), nullable=True)
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    method: Mapped[str | None] = mapped_column(String(32), nullable=True)
    output_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)

    job: Mapped[Job] = relationship(back_populates="files")


class Approval(Base):
    __tablename__ = "approvals"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(32))  # delete | overwrite | low_confidence
    payload: Mapped[dict] = mapped_column(JSONType, default=dict)
    status: Mapped[str] = mapped_column(String(32), default=ApprovalStatus.PENDING)
    decision_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    job: Mapped[Job] = relationship(back_populates="approvals")


class AuditEntry(Base):
    __tablename__ = "audit_entries"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), index=True)
    action: Mapped[str] = mapped_column(String(64))
    detail: Mapped[dict] = mapped_column(JSONType, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    job: Mapped[Job] = relationship(back_populates="audit_entries")


class NpiCache(Base):
    __tablename__ = "npi_cache"

    npi: Mapped[str] = mapped_column(String(10), primary_key=True)
    specialty: Mapped[str | None] = mapped_column(String(128), nullable=True)
    taxonomy_code: Mapped[str | None] = mapped_column(String(16), nullable=True)
    is_individual: Mapped[bool] = mapped_column(Boolean, default=False)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
```

- [ ] **Step 4: Implement `app/db/session.py` and `app/db/repository.py`**

```python
# app/db/session.py
from collections.abc import Iterator
from contextlib import contextmanager
from functools import lru_cache

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from ..config import get_settings


@lru_cache
def get_sessionmaker() -> sessionmaker:
    engine = create_engine(get_settings().database_url, pool_pre_ping=True, future=True)
    return sessionmaker(bind=engine, expire_on_commit=False)


@contextmanager
def session_scope() -> Iterator[Session]:
    session = get_sessionmaker()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
```

```python
# app/db/repository.py
from __future__ import annotations

import base64
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import selectinload, sessionmaker

from .models import Approval, ApprovalStatus, AuditEntry, Job, JobFile, JobStatus, NpiCache


def _encode_cursor(value: str) -> str:
    return base64.urlsafe_b64encode(value.encode()).decode()


def _decode_cursor(cursor: str) -> str:
    return base64.urlsafe_b64decode(cursor.encode()).decode()


class Repository:
    def __init__(self, session_factory: sessionmaker) -> None:
        self._sf = session_factory

    # jobs -----------------------------------------------------------------
    def create_job(self, job_id: str, api_key_id: str, original_filenames: list[str],
                   idempotency_key: str | None) -> Job:
        with self._sf() as s:
            job = Job(id=job_id, api_key_id=api_key_id, original_filenames=original_filenames,
                      idempotency_key=idempotency_key, status=JobStatus.PENDING)
            s.add(job)
            s.commit()
            return job

    def get_job(self, job_id: str) -> Job | None:
        with self._sf() as s:
            return s.execute(
                select(Job).where(Job.id == job_id).options(
                    selectinload(Job.files), selectinload(Job.approvals), selectinload(Job.audit_entries))
            ).scalar_one_or_none()

    def find_by_idempotency_key(self, key: str) -> Job | None:
        with self._sf() as s:
            return s.execute(select(Job).where(Job.idempotency_key == key)).scalar_one_or_none()

    def list_jobs(self, status: str | None = None, limit: int = 50, cursor: str | None = None
                  ) -> tuple[list[Job], str | None]:
        with self._sf() as s:
            stmt = select(Job).order_by(Job.created_at.desc(), Job.id.desc())
            if status:
                stmt = stmt.where(Job.status == status)
            if cursor:
                stmt = stmt.where(Job.id < _decode_cursor(cursor))
            rows = list(s.execute(stmt.limit(limit + 1)).scalars())
            next_cursor = _encode_cursor(rows[limit - 1].id) if len(rows) > limit else None
            return rows[:limit], next_cursor

    def update_job(self, job_id: str, **fields) -> Job:
        with self._sf() as s:
            job = s.get(Job, job_id)
            for key, value in fields.items():
                setattr(job, key, value)
            s.commit()
            return job

    # files ----------------------------------------------------------------
    def upsert_file(self, job_id: str, file_id: str, **fields) -> JobFile:
        with self._sf() as s:
            row = s.execute(select(JobFile).where(JobFile.job_id == job_id, JobFile.file_id == file_id)
                            ).scalar_one_or_none()
            if row is None:
                row = JobFile(job_id=job_id, file_id=file_id)
                s.add(row)
            for key, value in fields.items():
                setattr(row, key, value)
            s.commit()
            return row

    def list_files(self, job_id: str) -> list[JobFile]:
        with self._sf() as s:
            return list(s.execute(select(JobFile).where(JobFile.job_id == job_id)
                                  .order_by(JobFile.filename)).scalars())

    # approvals ------------------------------------------------------------
    def create_approval(self, job_id: str, kind: str, payload: dict) -> Approval:
        with self._sf() as s:
            approval = Approval(job_id=job_id, kind=kind, payload=payload)
            s.add(approval)
            s.commit()
            return approval

    def list_approvals(self, job_id: str, status: str | None = None) -> list[Approval]:
        with self._sf() as s:
            stmt = select(Approval).where(Approval.job_id == job_id).order_by(Approval.created_at)
            if status:
                stmt = stmt.where(Approval.status == status)
            return list(s.execute(stmt).scalars())

    def decide_approval(self, approval_id: str, decision: str, note: str | None) -> Approval:
        with self._sf() as s:
            approval = s.get(Approval, approval_id)
            approval.status = ApprovalStatus.APPROVED if decision == "approve" else ApprovalStatus.REJECTED
            approval.decision_note = note
            approval.decided_at = datetime.now(timezone.utc)
            s.commit()
            return approval

    # audit ----------------------------------------------------------------
    def audit(self, job_id: str, action: str, detail: dict) -> AuditEntry:
        with self._sf() as s:
            entry = AuditEntry(job_id=job_id, action=action, detail=detail)
            s.add(entry)
            s.commit()
            return entry

    # npi cache ------------------------------------------------------------
    def get_npi(self, npi: str) -> NpiCache | None:
        with self._sf() as s:
            return s.get(NpiCache, npi)

    def put_npi(self, npi: str, specialty: str | None, taxonomy_code: str | None,
                is_individual: bool) -> NpiCache:
        with self._sf() as s:
            row = s.get(NpiCache, npi) or NpiCache(npi=npi)
            row.specialty, row.taxonomy_code, row.is_individual = specialty, taxonomy_code, is_individual
            s.add(row)
            s.commit()
            return row


def get_repository() -> Repository:
    from .session import get_sessionmaker
    return Repository(get_sessionmaker())
```

- [ ] **Step 5: Create the Alembic migration**

Run: `cd backend && alembic init -t async alembic` is **not** used — configure sync Alembic:

```bash
cd backend
cat > alembic.ini <<'INI'
[alembic]
script_location = alembic
prepend_sys_path = .
[loggers]
keys = root
[handlers]
keys = console
[formatters]
keys = generic
[logger_root]
level = WARN
handlers = console
[handler_console]
class = StreamHandler
args = (sys.stderr,)
formatter = generic
[formatter_generic]
format = %(levelname)-5.5s [%(name)s] %(message)s
INI
mkdir -p alembic/versions
```

`alembic/env.py`:

```python
from alembic import context
from sqlalchemy import create_engine
from app.config import get_settings
from app.db.base import Base
from app.db import models  # noqa: F401  -- registers the tables

target_metadata = Base.metadata


def run_migrations_online() -> None:
    engine = create_engine(get_settings().database_url)
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


run_migrations_online()
```

Then generate the first revision:

```bash
cd backend && alembic revision --autogenerate -m "initial schema"
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `cd backend && pytest tests/test_repository.py -v`
Expected: 8 passed

---

### Task 11: Guarded file operations and manifest writers

**Files:**
- Create: `backend/app/workspace/filetools.py`, `backend/app/workspace/manifest.py`
- Test: `backend/tests/test_filetools.py`, `backend/tests/test_manifest.py`

**Interfaces:**
- Consumes: `app.workspace.paths.resolve_within`, `PathEscapeError`
- Produces:
  - `@dataclass(frozen=True) FileOp` with `op: str` (`"copy"|"delete"|"overwrite"|"mkdir"`), `source: str | None`, `target: str`, `reason: str`
  - `NEEDS_APPROVAL: frozenset[str]` = `{"delete", "overwrite"}`
  - `class GuardedFileTools` — constructed with `(root: Path, audit: Callable[[str, dict], None])`
    - `.toolkit() -> FileManagementToolkit` — LangChain toolkit scoped to `root`
    - `.plan_copy(source: Path, target_rel: str, reason: str) -> FileOp` — returns a `copy` op, or an `overwrite` op if the target exists
    - `.execute(op: FileOp) -> Path` — raises `PathEscapeError` (audited) if `op.target` escapes root; raises `PermissionError` if `op.op in NEEDS_APPROVAL` and `approved` was not passed
    - `.execute(op: FileOp, approved: bool = False) -> Path`
    - `.unique_target(target_rel: str) -> str` — `note.pdf` -> `note__2.pdf`
  - `write_manifest(path: Path, records: list[dict]) -> None` — JSONL
  - `write_labels_csv(path: Path, records: list[dict]) -> None` — columns `file_id,filename,source_path,codes_branch,specialty,confidence,method,parser,output_path,code_count`

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/test_filetools.py
import pytest
from app.workspace.filetools import FileOp, GuardedFileTools
from app.workspace.paths import PathEscapeError


@pytest.fixture()
def tools(tmp_path):
    events = []
    root = tmp_path / "root"
    root.mkdir()
    return GuardedFileTools(root, lambda a, d: events.append((a, d))), root, events


def test_plan_copy_returns_a_copy_op_when_target_is_free(tools):
    t, root, _ = tools
    src = root / "in.pdf"
    src.write_text("x")
    op = t.plan_copy(src, "output/with-codes/Cardiology/in.pdf", "coded cardiology note")
    assert op.op == "copy"


def test_plan_copy_returns_an_overwrite_op_when_target_exists(tools):
    t, root, _ = tools
    src = root / "in.pdf"
    src.write_text("x")
    target = root / "output" / "a.pdf"
    target.parent.mkdir(parents=True)
    target.write_text("old")
    op = t.plan_copy(src, "output/a.pdf", "collision")
    assert op.op == "overwrite"


def test_copy_executes_and_creates_parent_directories(tools):
    t, root, _ = tools
    src = root / "in.pdf"
    src.write_text("hello")
    op = t.plan_copy(src, "output/without-codes/Neurology/in.pdf", "r")
    result = t.execute(op)
    assert result.read_text() == "hello"


def test_overwrite_without_approval_is_refused(tools):
    t, root, _ = tools
    src = root / "in.pdf"
    src.write_text("new")
    (root / "out.pdf").write_text("old")
    op = t.plan_copy(src, "out.pdf", "collision")
    with pytest.raises(PermissionError):
        t.execute(op)
    assert (root / "out.pdf").read_text() == "old"


def test_overwrite_with_approval_proceeds(tools):
    t, root, _ = tools
    src = root / "in.pdf"
    src.write_text("new")
    (root / "out.pdf").write_text("old")
    op = t.plan_copy(src, "out.pdf", "collision")
    t.execute(op, approved=True)
    assert (root / "out.pdf").read_text() == "new"


def test_delete_without_approval_is_refused(tools):
    t, root, _ = tools
    victim = root / "victim.pdf"
    victim.write_text("x")
    with pytest.raises(PermissionError):
        t.execute(FileOp("delete", None, "victim.pdf", "cleanup"))
    assert victim.exists()


def test_escape_is_denied_and_audited(tools):
    t, root, events = tools
    with pytest.raises(PathEscapeError):
        t.execute(FileOp("copy", str(root / "in.pdf"), "../escaped.pdf", "bad"))
    assert any(action == "path_escape_denied" for action, _ in events)


def test_unique_target_suffixes_on_collision(tools):
    t, root, _ = tools
    (root / "a.pdf").write_text("x")
    assert t.unique_target("a.pdf") == "a__2.pdf"


def test_toolkit_is_scoped_to_the_root(tools):
    t, root, _ = tools
    toolkit = t.toolkit()
    assert str(root) in str(toolkit.root_dir)
    assert {tool.name for tool in toolkit.get_tools()} >= {"copy_file", "list_directory"}
```

```python
# backend/tests/test_manifest.py
import csv
import json
from app.workspace.manifest import write_labels_csv, write_manifest

RECORDS = [{
    "file_id": "f1", "filename": "note.pdf", "source_path": "bundle.zip!/cardio/note.pdf",
    "codes_branch": "with-codes", "specialty": "Cardiology", "confidence": 0.91,
    "method": "npi", "parser": "pypdf", "output_path": "output/with-codes/Cardiology/note.pdf",
    "code_hits": [{"code": "99213", "rule": "dictionary+cue"}],
}]


def test_manifest_is_jsonl(tmp_path):
    path = tmp_path / "manifest.jsonl"
    write_manifest(path, RECORDS)
    lines = path.read_text().strip().splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0])["file_id"] == "f1"


def test_labels_csv_has_the_expected_header_and_row(tmp_path):
    path = tmp_path / "labels.csv"
    write_labels_csv(path, RECORDS)
    rows = list(csv.DictReader(path.open()))
    assert rows[0]["specialty"] == "Cardiology"
    assert rows[0]["codes_branch"] == "with-codes"
    assert rows[0]["code_count"] == "1"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && pytest tests/test_filetools.py tests/test_manifest.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.workspace.filetools'`

- [ ] **Step 3: Implement `app/workspace/filetools.py`**

```python
from __future__ import annotations

import shutil
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from .paths import PathEscapeError, resolve_within

NEEDS_APPROVAL = frozenset({"delete", "overwrite"})


@dataclass(frozen=True)
class FileOp:
    op: str
    source: str | None
    target: str
    reason: str


class GuardedFileTools:
    """Every filesystem mutation the agent performs goes through this class.

    Writes are confined to `root`; delete and overwrite require an approval.
    """

    def __init__(self, root: Path, audit: Callable[[str, dict], None]) -> None:
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self._audit = audit

    def toolkit(self):
        from langchain_community.agent_toolkits import FileManagementToolkit

        return FileManagementToolkit(
            root_dir=str(self.root),
            selected_tools=["read_file", "write_file", "list_directory", "copy_file", "move_file", "file_search"],
        )

    def _resolve(self, target: str) -> Path:
        try:
            return resolve_within(self.root, target)
        except PathEscapeError as exc:
            self._audit("path_escape_denied", {"target": target, "root": str(self.root), "error": str(exc)})
            raise

    def unique_target(self, target_rel: str) -> str:
        path = Path(target_rel)
        counter = 2
        candidate = target_rel
        while self._resolve(candidate).exists():
            candidate = str(path.with_name(f"{path.stem}__{counter}{path.suffix}"))
            counter += 1
        return candidate

    def plan_copy(self, source: Path, target_rel: str, reason: str) -> FileOp:
        exists = self._resolve(target_rel).exists()
        return FileOp("overwrite" if exists else "copy", str(source), target_rel, reason)

    def execute(self, op: FileOp, approved: bool = False) -> Path:
        target = self._resolve(op.target)
        if op.op in NEEDS_APPROVAL and not approved:
            self._audit("guarded_op_refused", {"op": op.op, "target": op.target, "reason": op.reason})
            raise PermissionError(f"operation {op.op!r} on {op.target!r} requires approval")

        if op.op == "mkdir":
            target.mkdir(parents=True, exist_ok=True)
        elif op.op == "delete":
            if target.is_dir():
                shutil.rmtree(target)
            else:
                target.unlink(missing_ok=True)
        elif op.op in {"copy", "overwrite"}:
            source = Path(op.source or "")
            if not source.is_file():
                raise FileNotFoundError(f"source {op.source!r} does not exist")
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
        else:
            raise ValueError(f"unknown operation {op.op!r}")

        self._audit("op_executed", {"op": op.op, "target": op.target, "approved": approved, "reason": op.reason})
        return target
```

- [ ] **Step 4: Implement `app/workspace/manifest.py`**

```python
from __future__ import annotations

import csv
import json
from pathlib import Path

CSV_COLUMNS = ["file_id", "filename", "source_path", "codes_branch", "specialty",
               "confidence", "method", "parser", "output_path", "code_count"]


def write_manifest(path: Path, records: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for record in records:
            fh.write(json.dumps(record, default=str) + "\n")


def write_labels_csv(path: Path, records: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        for record in records:
            row = {key: record.get(key, "") for key in CSV_COLUMNS}
            row["code_count"] = len(record.get("code_hits") or [])
            writer.writerow(row)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd backend && pytest tests/test_filetools.py tests/test_manifest.py -v`
Expected: 11 passed

---

### Task 12: LangGraph agent — state, nodes, checkpointer, interrupts

**Files:**
- Create: `backend/app/agent/__init__.py`, `backend/app/agent/state.py`, `backend/app/agent/nodes.py`, `backend/app/agent/graph.py`, `backend/app/agent/approvals.py`
- Test: `backend/tests/test_agent_nodes.py`, `backend/tests/test_agent_graph.py`

**Interfaces:**
- Consumes: everything from Tasks 2–11
- Produces:
  - `class JobState(TypedDict, total=False)` with keys `job_id: str`, `root: str`, `files: list[dict]`, `batch_id: str | None`, `stage: str`, `pending_ops: list[dict]`, `manifest: list[dict]`, `error: str | None`
  - Node coroutines, each `async def node(state: JobState) -> dict`: `intake_node`, `unpack_node`, `parse_node`, `detect_codes_node`, `resolve_npi_node`, `classify_node`, `plan_placement_node`, `approval_gate_node`, `execute_ops_node`, `manifest_node`
  - `build_graph(checkpointer) -> CompiledStateGraph`
  - `async run_job(job_id: str, root: Path, checkpointer) -> JobState`
  - `async resume_job(job_id: str, resume_value: dict, checkpointer) -> JobState`
  - `approval_payload(kind: str, detail: dict) -> dict`

Each file dict carries: `file_id`, `path`, `filename`, `source_path`, `size_bytes`, `sha256`, `text`, `parser`, `parse_trail`, `ok`, `has_codes`, `code_hits`, `code_rejected`, `npis`, `specialty`, `confidence`, `method`, `output_path`.

- [ ] **Step 1: Write the failing node tests**

```python
# backend/tests/test_agent_nodes.py
import pytest
from app.agent import nodes
from app.agent.nodes import (detect_codes_node, execute_ops_node, intake_node,
                             plan_placement_node, unpack_node)


@pytest.fixture()
def workspace(tmp_path):
    root = tmp_path / "job-1"
    (root / "input").mkdir(parents=True)
    (root / "input" / "note.txt").write_text("Dx: E11.9\nProcedure Code: 99213")
    (root / "input" / "story.txt").write_text("The patient is 45 and lives in Beverly Hills, CA 90210.")
    return root


async def test_intake_enumerates_input_files(workspace):
    out = await intake_node({"job_id": "job-1", "root": str(workspace)})
    assert len(out["files"]) == 2
    assert all(f["sha256"] and f["file_id"] for f in out["files"])


async def test_unpack_expands_a_zip_and_records_source_path(tmp_path):
    import zipfile
    root = tmp_path / "job-2"
    (root / "input").mkdir(parents=True)
    with zipfile.ZipFile(root / "input" / "bundle.zip", "w") as zf:
        zf.writestr("cardio/a.txt", "Dx: I10")
    state = await intake_node({"job_id": "job-2", "root": str(root)})
    out = await unpack_node({**state, "job_id": "job-2", "root": str(root)})
    names = {f["filename"] for f in out["files"]}
    assert "a.txt" in names
    assert "bundle.zip" not in names
    entry = next(f for f in out["files"] if f["filename"] == "a.txt")
    assert entry["source_path"] == "bundle.zip!/cardio/a.txt"


async def test_detect_codes_splits_coded_and_uncoded(workspace, monkeypatch):
    files = [
        {"file_id": "f1", "text": "Dx: E11.9\nProcedure Code: 99213", "ok": True},
        {"file_id": "f2", "text": "The patient is 45 and lives in Beverly Hills, CA 90210.", "ok": True},
    ]
    out = await detect_codes_node({"files": files, "root": str(workspace)})
    by_id = {f["file_id"]: f for f in out["files"]}
    assert by_id["f1"]["has_codes"] is True
    assert by_id["f2"]["has_codes"] is False


async def test_plan_placement_builds_branch_and_specialty_paths(workspace):
    files = [
        {"file_id": "f1", "filename": "a.txt", "path": str(workspace / "input" / "note.txt"),
         "has_codes": True, "specialty": "Cardiology", "confidence": 0.9, "ok": True},
        {"file_id": "f2", "filename": "b.txt", "path": str(workspace / "input" / "story.txt"),
         "has_codes": False, "specialty": "Obstetrics & Gynecology", "confidence": 0.9, "ok": True},
    ]
    out = await plan_placement_node({"files": files, "root": str(workspace), "job_id": "job-1"})
    targets = {op["target"] for op in out["pending_ops"]}
    assert "output/with-codes/Cardiology/a.txt" in targets
    assert "output/without-codes/Obstetrics-and-Gynecology/b.txt" in targets


async def test_unparsed_files_are_planned_into_quarantine(workspace):
    files = [{"file_id": "f9", "filename": "bad.pdf", "path": str(workspace / "input" / "note.txt"),
              "ok": False, "has_codes": False, "specialty": None, "confidence": 0.0}]
    out = await plan_placement_node({"files": files, "root": str(workspace), "job_id": "job-1"})
    assert out["pending_ops"][0]["target"] == "output/unparsed/bad.pdf"


async def test_execute_ops_writes_the_files(workspace):
    ops = [{"op": "copy", "source": str(workspace / "input" / "note.txt"),
            "target": "output/with-codes/Cardiology/note.txt", "reason": "coded", "file_id": "f1"}]
    out = await execute_ops_node({"pending_ops": ops, "root": str(workspace), "job_id": "job-1",
                                  "files": [{"file_id": "f1"}]})
    assert (workspace / "output" / "with-codes" / "Cardiology" / "note.txt").exists()
    assert out["files"][0]["output_path"] == "output/with-codes/Cardiology/note.txt"
```

- [ ] **Step 2: Write the failing graph test**

```python
# backend/tests/test_agent_graph.py
import pytest
from langgraph.checkpoint.memory import MemorySaver
from app.agent import nodes as node_module
from app.agent.graph import build_graph


@pytest.fixture()
def workspace(tmp_path):
    root = tmp_path / "job-1"
    (root / "input").mkdir(parents=True)
    (root / "input" / "coded.txt").write_text("Diagnosis Code: E11.9\nProcedure Code: 99213")
    (root / "input" / "plain.txt").write_text("Patient reports a mild headache and is otherwise well.")
    return root


@pytest.fixture()
def stub_llm(monkeypatch):
    async def fake_classify_node(state):
        files = [{**f, "specialty": "Cardiology", "confidence": 0.95, "method": "llm_sync"}
                 for f in state["files"]]
        return {"files": files, "stage": "classify"}
    monkeypatch.setattr(node_module, "classify_node", fake_classify_node)


async def test_graph_runs_end_to_end_and_files_both_branches(workspace, stub_llm, monkeypatch):
    async def fake_parse_node(state):
        from pathlib import Path
        files = [{**f, "text": Path(f["path"]).read_text(), "ok": True, "parser": "text",
                  "parse_trail": [{"parser": "text", "ok": True, "reason": None}]} for f in state["files"]]
        return {"files": files, "stage": "parse"}
    monkeypatch.setattr(node_module, "parse_node", fake_parse_node)

    graph = build_graph(MemorySaver())
    config = {"configurable": {"thread_id": "job-1"}}
    result = await graph.ainvoke({"job_id": "job-1", "root": str(workspace)}, config)

    assert (workspace / "output" / "with-codes" / "Cardiology" / "coded.txt").exists()
    assert (workspace / "output" / "without-codes" / "Cardiology" / "plain.txt").exists()
    assert (workspace / "output" / "manifest.jsonl").exists()
    assert (workspace / "output" / "labels.csv").exists()
    assert len(result["manifest"]) == 2


async def test_graph_interrupts_when_an_op_needs_approval_and_resumes(workspace, stub_llm, monkeypatch):
    from langgraph.types import Command

    async def fake_parse_node(state):
        from pathlib import Path
        files = [{**f, "text": Path(f["path"]).read_text(), "ok": True, "parser": "text", "parse_trail": []}
                 for f in state["files"]]
        return {"files": files, "stage": "parse"}
    monkeypatch.setattr(node_module, "parse_node", fake_parse_node)

    # Pre-create a colliding target so plan_placement produces an overwrite op.
    target = workspace / "output" / "with-codes" / "Cardiology" / "coded.txt"
    target.parent.mkdir(parents=True)
    target.write_text("existing")

    graph = build_graph(MemorySaver())
    config = {"configurable": {"thread_id": "job-2"}}
    result = await graph.ainvoke({"job_id": "job-2", "root": str(workspace)}, config)

    assert "__interrupt__" in result
    payload = result["__interrupt__"][0].value
    assert payload["kind"] == "overwrite"

    resumed = await graph.ainvoke(Command(resume={"decisions": {payload["ops"][0]["target"]: "approve"}}), config)
    assert target.read_text().startswith("Diagnosis Code")
    assert len(resumed["manifest"]) == 2
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `cd backend && pytest tests/test_agent_nodes.py tests/test_agent_graph.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.agent'`

- [ ] **Step 4: Implement `app/agent/state.py` and `app/agent/approvals.py`**

```python
# app/agent/state.py
from __future__ import annotations

from typing import TypedDict


class JobState(TypedDict, total=False):
    job_id: str
    root: str
    stage: str
    files: list[dict]
    pending_ops: list[dict]
    batch_id: str | None
    manifest: list[dict]
    error: str | None
```

```python
# app/agent/approvals.py
from __future__ import annotations


def approval_payload(kind: str, detail: dict) -> dict:
    """Shape of every interrupt raised by the graph."""
    return {"kind": kind, **detail}


def decision_for(resume_value: dict | None, key: str, default: str = "reject") -> str:
    if not resume_value:
        return default
    return (resume_value.get("decisions") or {}).get(key, default)
```

- [ ] **Step 5: Implement `app/agent/nodes.py`**

```python
from __future__ import annotations

import hashlib
import logging
import uuid
from pathlib import Path

from langgraph.types import interrupt

from ..codes.detector import detect_codes
from ..codes.dictionaries import get_dictionaries
from ..config import get_settings
from ..parsing.chain import parse_document
from ..specialty.classifier import ClassificationRequest, classify
from ..specialty.npi import resolve_specialty_from_npis
from ..specialty.taxonomy import UNCLASSIFIED, folder_name, normalize_specialty
from ..workspace.archive import ArchiveError, extract_archive
from ..workspace.filetools import FileOp, GuardedFileTools
from ..workspace.manifest import write_labels_csv, write_manifest
from .approvals import approval_payload, decision_for
from .state import JobState

log = logging.getLogger(__name__)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        while chunk := fh.read(1 << 20):
            digest.update(chunk)
    return digest.hexdigest()


def _tools(state: JobState) -> GuardedFileTools:
    audit_log = Path(state["root"]) / "logs" / "audit.jsonl"

    def audit(action: str, detail: dict) -> None:
        audit_log.parent.mkdir(parents=True, exist_ok=True)
        import json
        with audit_log.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({"job_id": state.get("job_id"), "action": action, "detail": detail}) + "\n")

    return GuardedFileTools(Path(state["root"]), audit)


def _file_record(path: Path, source_path: str) -> dict:
    return {
        "file_id": str(uuid.uuid4()),
        "path": str(path),
        "filename": path.name,
        "source_path": source_path,
        "size_bytes": path.stat().st_size,
        "sha256": _sha256(path),
        "text": "",
        "parser": None,
        "parse_trail": [],
        "ok": False,
        "has_codes": False,
        "code_hits": [],
        "code_rejected": [],
        "npis": [],
        "specialty": None,
        "confidence": 0.0,
        "method": None,
        "output_path": None,
    }


async def intake_node(state: JobState) -> dict:
    input_dir = Path(state["root"]) / "input"
    files = [_file_record(p, p.name) for p in sorted(input_dir.rglob("*")) if p.is_file()]
    log.info("intake: %d files for job %s", len(files), state.get("job_id"))
    return {"files": files, "stage": "intake"}


async def unpack_node(state: JobState) -> dict:
    root = Path(state["root"])
    extracted_root = root / "extracted"
    result: list[dict] = []
    for record in state.get("files", []):
        path = Path(record["path"])
        if path.suffix.lower() != ".zip":
            result.append(record)
            continue
        dest = extracted_root / path.stem
        try:
            entries = extract_archive(path, dest)
        except ArchiveError as exc:
            log.warning("archive rejected: %s (%s)", path.name, exc)
            result.append({**record, "ok": False, "parse_trail": [{"parser": "zip", "ok": False, "reason": str(exc)}]})
            continue
        for entry in entries:
            child = _file_record(entry.path, f"{path.name}!/{entry.source_path}")
            result.append(child)
    return {"files": result, "stage": "unpack"}


async def parse_node(state: JobState) -> dict:
    files = []
    for record in state.get("files", []):
        if record.get("ok"):
            files.append(record)
            continue
        parsed = await parse_document(Path(record["path"]))
        files.append({**record, "text": parsed.text, "parser": parsed.parser, "ok": parsed.ok,
                      "parse_trail": [{"parser": a.parser, "ok": a.ok, "reason": a.reason} for a in parsed.trail]})
    return {"files": files, "stage": "parse"}


async def detect_codes_node(state: JobState) -> dict:
    dicts = get_dictionaries()
    threshold = get_settings().code_evidence_threshold
    files = []
    for record in state.get("files", []):
        if not record.get("ok"):
            files.append(record)
            continue
        result = detect_codes(record.get("text", ""), dicts, threshold)
        files.append({**record,
                      "has_codes": result.has_codes,
                      "code_hits": [h.__dict__ for h in result.hits],
                      "code_rejected": [h.__dict__ for h in result.rejected],
                      "npis": result.npis})
    return {"files": files, "stage": "detect_codes"}


async def resolve_npi_node(state: JobState) -> dict:
    files = []
    for record in state.get("files", []):
        if not record.get("ok") or not record.get("npis"):
            files.append(record)
            continue
        resolved = await resolve_specialty_from_npis(record["npis"])
        if resolved and resolved.specialty:
            files.append({**record, "specialty": resolved.specialty, "confidence": 1.0, "method": "npi"})
        else:
            files.append(record)
    return {"files": files, "stage": "resolve_npi"}


async def classify_node(state: JobState) -> dict:
    pending = [f for f in state.get("files", []) if f.get("ok") and not f.get("specialty")]
    if not pending:
        return {"files": state.get("files", []), "stage": "classify"}

    requests = [ClassificationRequest(f["file_id"], f.get("text", "")) for f in pending]
    results, batch_id = await classify(requests, Path(state["root"]) / "batch")
    if results is None:
        # The Celery task polls the batch and resumes the graph with the results.
        results = interrupt(approval_payload("batch_pending", {"batch_id": batch_id})) or []
        results = [ClassificationLike(**r) if isinstance(r, dict) else r for r in results]

    by_id = {r.file_id: r for r in results}
    files = []
    for record in state.get("files", []):
        label = by_id.get(record["file_id"])
        if label is None:
            files.append(record)
            continue
        files.append({**record, "specialty": normalize_specialty(label.specialty),
                      "confidence": label.confidence, "method": label.method})
    return {"files": files, "stage": "classify", "batch_id": batch_id}


class ClassificationLike:
    def __init__(self, file_id: str, specialty: str, confidence: float, rationale: str = "", method: str = "llm_batch"):
        self.file_id, self.specialty, self.confidence, self.rationale, self.method = (
            file_id, specialty, confidence, rationale, method)


async def plan_placement_node(state: JobState) -> dict:
    """Agent node: turn labelled files into a proposed operation list.

    Placement is derived from the label; the reasoning surface is collision
    handling and low-confidence escalation, both of which raise approvals.
    """
    threshold = get_settings().specialty_confidence_threshold
    tools = _tools(state)
    ops: list[dict] = []
    low_confidence: list[dict] = []

    for record in state.get("files", []):
        if not record.get("ok"):
            target = f"output/unparsed/{record['filename']}"
            ops.append({"op": "copy", "source": record["path"], "target": tools.unique_target(target),
                        "reason": "no parser could extract text", "file_id": record["file_id"]})
            continue

        specialty = record.get("specialty") or UNCLASSIFIED
        if record.get("method") != "npi" and record.get("confidence", 0.0) < threshold:
            low_confidence.append({"file_id": record["file_id"], "filename": record["filename"],
                                   "proposed_specialty": specialty, "confidence": record.get("confidence", 0.0)})
            specialty = UNCLASSIFIED

        branch = "with-codes" if record.get("has_codes") else "without-codes"
        target = f"output/{branch}/{folder_name(specialty)}/{record['filename']}"
        planned = tools.plan_copy(Path(record["path"]), target, f"{branch} / {specialty}")
        ops.append({"op": planned.op, "source": planned.source, "target": planned.target,
                    "reason": planned.reason, "file_id": record["file_id"]})

    if low_confidence:
        answer = interrupt(approval_payload("low_confidence", {"files": low_confidence}))
        chosen = (answer or {}).get("specialties") or {}
        files = []
        for record in state.get("files", []):
            override = chosen.get(record["file_id"])
            files.append({**record, "specialty": normalize_specialty(override), "method": "human"}
                         if override else record)
        state = {**state, "files": files}
        return await plan_placement_node(state)

    return {"pending_ops": ops, "stage": "plan_placement"}


async def approval_gate_node(state: JobState) -> dict:
    guarded = [op for op in state.get("pending_ops", []) if op["op"] in {"delete", "overwrite"}]
    if not guarded:
        return {"stage": "approval_gate"}

    answer = interrupt(approval_payload("overwrite", {"ops": guarded}))
    approved_targets = {t for t, d in ((answer or {}).get("decisions") or {}).items() if d == "approve"}

    tools = _tools(state)
    ops = []
    for op in state.get("pending_ops", []):
        if op["op"] in {"delete", "overwrite"} and op["target"] not in approved_targets:
            ops.append({**op, "op": "copy", "target": tools.unique_target(op["target"]),
                        "reason": f"{op['reason']} (approval declined; auto-suffixed)"})
        else:
            ops.append({**op, "approved": op["target"] in approved_targets})
    return {"pending_ops": ops, "stage": "approval_gate"}


async def execute_ops_node(state: JobState) -> dict:
    tools = _tools(state)
    outputs: dict[str, str] = {}
    for op in state.get("pending_ops", []):
        file_op = FileOp(op["op"], op.get("source"), op["target"], op.get("reason", ""))
        tools.execute(file_op, approved=bool(op.get("approved")))
        outputs[op["file_id"]] = op["target"]

    files = [{**f, "output_path": outputs.get(f["file_id"], f.get("output_path"))} for f in state.get("files", [])]
    return {"files": files, "stage": "execute_ops"}


async def manifest_node(state: JobState) -> dict:
    root = Path(state["root"])
    records = []
    for record in state.get("files", []):
        output_path = record.get("output_path") or ""
        records.append({
            "file_id": record["file_id"], "filename": record["filename"],
            "source_path": record.get("source_path", ""), "sha256": record.get("sha256"),
            "size_bytes": record.get("size_bytes", 0),
            "codes_branch": ("unparsed" if not record.get("ok")
                             else "with-codes" if record.get("has_codes") else "without-codes"),
            "specialty": record.get("specialty") or UNCLASSIFIED,
            "confidence": record.get("confidence", 0.0), "method": record.get("method"),
            "parser": record.get("parser"), "parse_trail": record.get("parse_trail", []),
            "code_hits": record.get("code_hits", []), "code_rejected": record.get("code_rejected", []),
            "npis": record.get("npis", []), "output_path": output_path,
        })
    write_manifest(root / "output" / "manifest.jsonl", records)
    write_labels_csv(root / "output" / "labels.csv", records)
    return {"manifest": records, "stage": "manifest"}
```

- [ ] **Step 6: Implement `app/agent/graph.py`**

```python
from __future__ import annotations

from pathlib import Path

from langgraph.graph import END, START, StateGraph

from . import nodes
from .state import JobState


def _delegate(name: str):
    """Look the node up at call time so tests can monkeypatch module attributes."""
    async def wrapper(state: JobState) -> dict:
        return await getattr(nodes, name)(state)
    wrapper.__name__ = name
    return wrapper


def build_graph(checkpointer):
    graph = StateGraph(JobState)
    order = ["intake_node", "unpack_node", "parse_node", "detect_codes_node", "resolve_npi_node",
             "classify_node", "plan_placement_node", "approval_gate_node", "execute_ops_node", "manifest_node"]
    for name in order:
        graph.add_node(name, _delegate(name))
    graph.add_edge(START, order[0])
    for current, following in zip(order, order[1:]):
        graph.add_edge(current, following)
    graph.add_edge(order[-1], END)
    return graph.compile(checkpointer=checkpointer)


async def run_job(job_id: str, root: Path, checkpointer) -> JobState:
    graph = build_graph(checkpointer)
    config = {"configurable": {"thread_id": job_id}}
    return await graph.ainvoke({"job_id": job_id, "root": str(root)}, config)


async def resume_job(job_id: str, resume_value: dict, checkpointer) -> JobState:
    from langgraph.types import Command

    graph = build_graph(checkpointer)
    config = {"configurable": {"thread_id": job_id}}
    return await graph.ainvoke(Command(resume=resume_value), config)
```

- [ ] **Step 7: Run tests to verify they pass**

Run: `cd backend && pytest tests/test_agent_nodes.py tests/test_agent_graph.py -v`
Expected: 8 passed

---

### Task 13: Celery worker and the jobs API

**Files:**
- Create: `backend/app/storage.py`, `backend/app/tasks.py`, `backend/app/api/v1/schemas.py`, `backend/app/api/v1/jobs.py`
- Modify: `backend/app/api/v1/router.py`
- Test: `backend/tests/test_jobs_api.py`

**Interfaces:**
- Consumes: `Repository`, `run_job`, `resume_job`, `poll_batch`, `fetch_batch_results`
- Produces:
  - `celery_app: Celery`
  - `@celery_app.task(name="jobs.run") def run_job_task(job_id: str) -> None`
  - `@celery_app.task(name="jobs.resume") def resume_job_task(job_id: str, resume_value: dict) -> None`
  - `@celery_app.task(name="jobs.poll_batch") def poll_batch_task(job_id: str, batch_id: str) -> None`
  - `checkpointer()` — yields an `AsyncPostgresSaver` bound to `DATABASE_URL`
  - Pydantic schemas `JobSummary`, `JobDetail`, `FileDetail`, `ApprovalOut`, `ApprovalDecisionIn`, `Page`
  - Routes: `POST /api/v1/jobs`, `GET /api/v1/jobs`, `GET /api/v1/jobs/{id}`, `GET /api/v1/jobs/{id}/files`, `GET /api/v1/jobs/{id}/files/{file_id}`, `POST /api/v1/jobs/{id}/cancel`, `GET /api/v1/jobs/{id}/tree`, `GET /api/v1/jobs/{id}/download`, `GET /api/v1/jobs/{id}/manifest.csv`, `GET /api/v1/jobs/{id}/events`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_jobs_api.py
import io
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app.db.base import Base
from app.db.repository import Repository


@pytest.fixture()
def api(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    repo = Repository(sessionmaker(bind=engine, expire_on_commit=False))

    import app.api.v1.jobs as jobs_module
    monkeypatch.setattr(jobs_module, "get_repository", lambda: repo)
    dispatched = []
    monkeypatch.setattr(jobs_module, "dispatch_job", lambda job_id: dispatched.append(job_id))

    from app.config import get_settings
    get_settings.cache_clear()
    monkeypatch.setenv("WORKSPACE_ROOT", str(tmp_path))
    monkeypatch.setenv("API_KEYS", "test-key")

    from app.main import create_app
    client = TestClient(create_app(), raise_server_exceptions=False)
    yield client, repo, dispatched
    get_settings.cache_clear()


AUTH = {"X-API-Key": "test-key"}


def test_upload_creates_a_job_and_dispatches_it(api):
    client, repo, dispatched = api
    files = [("files", ("note.txt", io.BytesIO(b"Dx: E11.9"), "text/plain"))]
    r = client.post("/api/v1/jobs", files=files, headers=AUTH)
    assert r.status_code == 202
    job_id = r.json()["id"]
    assert repo.get_job(job_id) is not None
    assert dispatched == [job_id]


def test_uploaded_bytes_land_in_the_job_input_folder(api, tmp_path):
    client, _, _ = api
    files = [("files", ("note.txt", io.BytesIO(b"hello"), "text/plain"))]
    job_id = client.post("/api/v1/jobs", files=files, headers=AUTH).json()["id"]
    assert (tmp_path / job_id / "input" / "note.txt").read_bytes() == b"hello"


def test_upload_requires_authentication(api):
    client, _, _ = api
    files = [("files", ("note.txt", io.BytesIO(b"x"), "text/plain"))]
    assert client.post("/api/v1/jobs", files=files).status_code == 401


def test_idempotency_key_returns_the_same_job(api):
    client, _, dispatched = api
    files = [("files", ("note.txt", io.BytesIO(b"x"), "text/plain"))]
    headers = {**AUTH, "Idempotency-Key": "abc"}
    first = client.post("/api/v1/jobs", files=files, headers=headers).json()["id"]
    files = [("files", ("note.txt", io.BytesIO(b"x"), "text/plain"))]
    second = client.post("/api/v1/jobs", files=files, headers=headers).json()["id"]
    assert first == second
    assert dispatched == [first]


def test_list_jobs_returns_a_paginated_envelope(api):
    client, repo, _ = api
    repo.create_job("j1", "test-key", ["a.pdf"], None)
    body = client.get("/api/v1/jobs?limit=1", headers=AUTH).json()
    assert "items" in body and "next_cursor" in body


def test_get_unknown_job_returns_problem_json(api):
    client, _, _ = api
    r = client.get("/api/v1/jobs/nope", headers=AUTH)
    assert r.status_code == 404
    assert r.headers["content-type"].startswith("application/problem+json")


def test_file_detail_exposes_code_evidence(api):
    client, repo, _ = api
    repo.create_job("j1", "test-key", [], None)
    repo.upsert_file("j1", "f1", filename="a.pdf", has_codes=True, specialty="Cardiology",
                     code_hits=[{"code": "99213", "rule": "dictionary+cue", "context": "Procedure Code: 99213"}])
    body = client.get("/api/v1/jobs/j1/files/f1", headers=AUTH).json()
    assert body["code_hits"][0]["code"] == "99213"
    assert body["specialty"] == "Cardiology"


def test_tree_endpoint_lists_the_output_structure(api, tmp_path):
    client, repo, _ = api
    repo.create_job("j1", "test-key", [], None)
    target = tmp_path / "j1" / "output" / "with-codes" / "Cardiology"
    target.mkdir(parents=True)
    (target / "note.pdf").write_text("x")
    body = client.get("/api/v1/jobs/j1/tree", headers=AUTH).json()
    assert "with-codes/Cardiology/note.pdf" in body["paths"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && pytest tests/test_jobs_api.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.api.v1.jobs'`

- [ ] **Step 3: Implement `app/api/v1/schemas.py`**

```python
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


class JobSummary(BaseModel):
    id: str
    status: str
    stage: str
    progress: float
    created_at: datetime
    file_count: int = 0


class FileDetail(BaseModel):
    file_id: str
    filename: str
    source_path: str
    status: str
    parser: str | None
    parse_trail: list[dict] = Field(default_factory=list)
    has_codes: bool
    code_hits: list[dict] = Field(default_factory=list)
    code_rejected: list[dict] = Field(default_factory=list)
    npis: list[str] = Field(default_factory=list)
    specialty: str | None
    confidence: float
    method: str | None
    output_path: str | None


class JobDetail(JobSummary):
    original_filenames: list[str] = Field(default_factory=list)
    batch_id: str | None = None
    error: str | None = None
    pending_approvals: int = 0


class ApprovalOut(BaseModel):
    id: str
    kind: str
    status: str
    payload: dict[str, Any]
    created_at: datetime


class ApprovalDecisionIn(BaseModel):
    decision: Literal["approve", "reject"]
    note: str | None = None
    specialty: str | None = None


class Page(BaseModel):
    items: list[Any]
    next_cursor: str | None = None
```

- [ ] **Step 4: Implement `app/storage.py` and `app/tasks.py`**

```python
# app/storage.py
from __future__ import annotations

import shutil
from pathlib import Path

from fastapi import UploadFile

from .config import get_settings
from .workspace.paths import resolve_within


def job_root(job_id: str) -> Path:
    return get_settings().workspace_root / job_id


def save_uploads(job_id: str, uploads: list[UploadFile]) -> list[str]:
    """Stream uploads into <workspace>/<job_id>/input/, rejecting unsafe names."""
    input_dir = job_root(job_id) / "input"
    input_dir.mkdir(parents=True, exist_ok=True)
    names: list[str] = []
    for upload in uploads:
        safe_name = Path(upload.filename or "unnamed").name
        target = resolve_within(input_dir, safe_name)
        with target.open("wb") as fh:
            shutil.copyfileobj(upload.file, fh, length=1 << 20)
        names.append(safe_name)
    return names
```

```python
# app/tasks.py
from __future__ import annotations

import asyncio
import logging
import time

from celery import Celery

from .config import get_settings
from .db.models import JobStatus
from .db.repository import get_repository
from .storage import job_root

log = logging.getLogger(__name__)
settings = get_settings()

celery_app = Celery("labeller", broker=settings.redis_url, backend=settings.redis_url)
celery_app.conf.update(task_acks_late=True, worker_prefetch_multiplier=1, task_track_started=True)


async def _with_checkpointer(coro_factory):
    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

    dsn = get_settings().database_url.replace("postgresql+psycopg", "postgresql")
    async with AsyncPostgresSaver.from_conn_string(dsn) as saver:
        await saver.setup()
        return await coro_factory(saver)


def _persist(job_id: str, state: dict) -> None:
    repo = get_repository()
    for record in state.get("files", []):
        repo.upsert_file(
            job_id, record["file_id"], filename=record.get("filename", ""),
            source_path=record.get("source_path", ""), sha256=record.get("sha256"),
            size_bytes=record.get("size_bytes", 0),
            status="filed" if record.get("output_path") else ("parsed" if record.get("ok") else "unparsed"),
            parser=record.get("parser"), parse_trail=record.get("parse_trail", []),
            has_codes=bool(record.get("has_codes")), code_hits=record.get("code_hits", []),
            code_rejected=record.get("code_rejected", []), npis=record.get("npis", []),
            specialty=record.get("specialty"), confidence=record.get("confidence", 0.0),
            method=record.get("method"), output_path=record.get("output_path"),
        )


def _handle_interrupt(job_id: str, state: dict) -> bool:
    """Persist an interrupt as an Approval row. Returns True if the job is now parked."""
    interrupts = state.get("__interrupt__") or []
    if not interrupts:
        return False
    repo = get_repository()
    payload = interrupts[0].value if hasattr(interrupts[0], "value") else interrupts[0]

    if payload.get("kind") == "batch_pending":
        repo.update_job(job_id, status=JobStatus.AWAITING_BATCH, batch_id=payload.get("batch_id"))
        poll_batch_task.apply_async((job_id, payload["batch_id"]), countdown=60)
        return True

    repo.create_approval(job_id, payload["kind"], payload)
    repo.update_job(job_id, status=JobStatus.AWAITING_APPROVAL)
    repo.audit(job_id, "approval_requested", payload)
    return True


@celery_app.task(name="jobs.run")
def run_job_task(job_id: str) -> None:
    from .agent.graph import run_job

    repo = get_repository()
    repo.update_job(job_id, status=JobStatus.RUNNING, stage="intake")
    try:
        state = asyncio.run(_with_checkpointer(lambda saver: run_job(job_id, job_root(job_id), saver)))
        _persist(job_id, state)
        if not _handle_interrupt(job_id, state):
            repo.update_job(job_id, status=JobStatus.COMPLETED, stage="manifest", progress=1.0)
    except Exception as exc:  # noqa: BLE001
        log.exception("job %s failed", job_id)
        repo.update_job(job_id, status=JobStatus.FAILED, error=str(exc))


@celery_app.task(name="jobs.resume")
def resume_job_task(job_id: str, resume_value: dict) -> None:
    from .agent.graph import resume_job

    repo = get_repository()
    repo.update_job(job_id, status=JobStatus.RUNNING)
    try:
        state = asyncio.run(_with_checkpointer(lambda saver: resume_job(job_id, resume_value, saver)))
        _persist(job_id, state)
        if not _handle_interrupt(job_id, state):
            repo.update_job(job_id, status=JobStatus.COMPLETED, stage="manifest", progress=1.0)
    except Exception as exc:  # noqa: BLE001
        log.exception("resuming job %s failed", job_id)
        repo.update_job(job_id, status=JobStatus.FAILED, error=str(exc))


@celery_app.task(name="jobs.poll_batch")
def poll_batch_task(job_id: str, batch_id: str) -> None:
    from .specialty.classifier import fetch_batch_results, poll_batch

    status = poll_batch(batch_id)
    if status in {"validating", "in_progress", "finalizing"}:
        poll_batch_task.apply_async((job_id, batch_id), countdown=60)
        return
    if status != "completed":
        get_repository().update_job(job_id, status=JobStatus.FAILED, error=f"OpenAI batch {batch_id} {status}")
        return
    results = [r.__dict__ for r in fetch_batch_results(batch_id)]
    resume_job_task.delay(job_id, results)


def dispatch_job(job_id: str) -> None:
    run_job_task.delay(job_id)
```

- [ ] **Step 5: Implement `app/api/v1/jobs.py`**

```python
from __future__ import annotations

import io
import uuid
import zipfile
from pathlib import Path

from fastapi import APIRouter, Depends, Header, Query, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from sse_starlette.sse import EventSourceResponse

from ...config import get_settings
from ...db.models import ApprovalStatus, JobStatus
from ...db.repository import get_repository
from ...errors import ProblemException
from ...security import require_api_key
from ...storage import job_root, save_uploads
from ...tasks import dispatch_job
from .schemas import FileDetail, JobDetail, JobSummary, Page

router = APIRouter(tags=["jobs"], dependencies=[Depends(require_api_key)])


def _job_or_404(job_id: str):
    job = get_repository().get_job(job_id)
    if job is None:
        raise ProblemException(404, "Not Found", f"Job {job_id!r} does not exist.")
    return job


def _summary(job) -> JobSummary:
    return JobSummary(id=job.id, status=job.status, stage=job.stage, progress=job.progress,
                      created_at=job.created_at, file_count=len(job.files))


@router.post("/jobs", status_code=202, response_model=JobDetail)
def create_job(files: list[UploadFile], api_key: str = Depends(require_api_key),
               idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")) -> JobDetail:
    repo = get_repository()
    if idempotency_key:
        existing = repo.find_by_idempotency_key(idempotency_key)
        if existing is not None:
            return JobDetail(**_summary(existing).model_dump(), original_filenames=existing.original_filenames)

    if not files:
        raise ProblemException(422, "Unprocessable Entity", "At least one file is required.")

    job_id = str(uuid.uuid4())
    names = save_uploads(job_id, files)
    job = repo.create_job(job_id, api_key, names, idempotency_key)
    repo.audit(job_id, "job_created", {"files": names})
    dispatch_job(job_id)
    return JobDetail(**_summary(job).model_dump(), original_filenames=names)


@router.get("/jobs", response_model=Page)
def list_jobs(status: str | None = None, limit: int = Query(default=25, le=100),
              cursor: str | None = None) -> Page:
    jobs, next_cursor = get_repository().list_jobs(status=status, limit=limit, cursor=cursor)
    return Page(items=[_summary(j).model_dump() for j in jobs], next_cursor=next_cursor)


@router.get("/jobs/{job_id}", response_model=JobDetail)
def get_job(job_id: str) -> JobDetail:
    job = _job_or_404(job_id)
    pending = len([a for a in job.approvals if a.status == ApprovalStatus.PENDING])
    return JobDetail(**_summary(job).model_dump(), original_filenames=job.original_filenames,
                     batch_id=job.batch_id, error=job.error, pending_approvals=pending)


@router.get("/jobs/{job_id}/files", response_model=Page)
def list_job_files(job_id: str) -> Page:
    _job_or_404(job_id)
    rows = get_repository().list_files(job_id)
    return Page(items=[_file_detail(r).model_dump() for r in rows], next_cursor=None)


def _file_detail(row) -> FileDetail:
    return FileDetail(
        file_id=row.file_id, filename=row.filename, source_path=row.source_path, status=row.status,
        parser=row.parser, parse_trail=row.parse_trail or [], has_codes=row.has_codes,
        code_hits=row.code_hits or [], code_rejected=row.code_rejected or [], npis=row.npis or [],
        specialty=row.specialty, confidence=row.confidence, method=row.method, output_path=row.output_path)


@router.get("/jobs/{job_id}/files/{file_id}", response_model=FileDetail)
def get_job_file(job_id: str, file_id: str) -> FileDetail:
    _job_or_404(job_id)
    row = next((r for r in get_repository().list_files(job_id) if r.file_id == file_id), None)
    if row is None:
        raise ProblemException(404, "Not Found", f"File {file_id!r} is not part of job {job_id!r}.")
    return _file_detail(row)


@router.post("/jobs/{job_id}/cancel", response_model=JobDetail)
def cancel_job(job_id: str) -> JobDetail:
    _job_or_404(job_id)
    repo = get_repository()
    repo.update_job(job_id, status=JobStatus.CANCELLED)
    repo.audit(job_id, "job_cancelled", {})
    return get_job(job_id)


@router.get("/jobs/{job_id}/tree")
def job_tree(job_id: str) -> dict:
    _job_or_404(job_id)
    output = job_root(job_id) / "output"
    paths = sorted(str(p.relative_to(output)) for p in output.rglob("*") if p.is_file()) if output.exists() else []
    return {"root": "output", "paths": paths}


@router.get("/jobs/{job_id}/download")
def download_results(job_id: str) -> StreamingResponse:
    _job_or_404(job_id)
    output = job_root(job_id) / "output"
    if not output.exists():
        raise ProblemException(409, "Conflict", "This job has no output yet.")

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in output.rglob("*"):
            if path.is_file():
                zf.write(path, path.relative_to(output))
    buffer.seek(0)
    headers = {"Content-Disposition": f'attachment; filename="{job_id}-output.zip"'}
    return StreamingResponse(buffer, media_type="application/zip", headers=headers)


@router.get("/jobs/{job_id}/manifest.csv")
def download_manifest(job_id: str) -> FileResponse:
    _job_or_404(job_id)
    path = job_root(job_id) / "output" / "labels.csv"
    if not path.exists():
        raise ProblemException(409, "Conflict", "This job has no manifest yet.")
    return FileResponse(path, media_type="text/csv", filename=f"{job_id}-labels.csv")


@router.get("/jobs/{job_id}/events")
async def job_events(job_id: str) -> EventSourceResponse:
    import asyncio
    import json

    _job_or_404(job_id)

    async def stream():
        last = None
        terminal = {JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED}
        while True:
            job = get_repository().get_job(job_id)
            snapshot = {"status": job.status, "stage": job.stage, "progress": job.progress,
                        "pending_approvals": len([a for a in job.approvals if a.status == ApprovalStatus.PENDING])}
            if snapshot != last:
                last = snapshot
                yield {"event": "progress", "data": json.dumps(snapshot)}
            if job.status in terminal:
                break
            await asyncio.sleep(2)

    return EventSourceResponse(stream())
```

- [ ] **Step 6: Register the router**

```python
# app/api/v1/router.py
from fastapi import APIRouter
from . import jobs, specialties, system

api_v1 = APIRouter(prefix="/api/v1")
api_v1.include_router(system.router)
api_v1.include_router(specialties.router)
api_v1.include_router(jobs.router)
```

- [ ] **Step 7: Run tests to verify they pass**

Run: `cd backend && pytest tests/test_jobs_api.py -v`
Expected: 8 passed

---

### Task 14: Approvals API and the code lookup endpoint

**Files:**
- Create: `backend/app/api/v1/approvals.py`, `backend/app/api/v1/codes.py`
- Modify: `backend/app/api/v1/router.py`
- Test: `backend/tests/test_approvals_api.py`

**Interfaces:**
- Consumes: `Repository`, `resume_job_task`, `get_dictionaries`
- Produces:
  - `GET /api/v1/jobs/{job_id}/approvals`
  - `POST /api/v1/jobs/{job_id}/approvals/{approval_id}` accepting `ApprovalDecisionIn`
  - `GET /api/v1/codes/lookup?code=`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_approvals_api.py
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app.db.base import Base
from app.db.models import ApprovalStatus
from app.db.repository import Repository

AUTH = {"X-API-Key": "test-key"}


@pytest.fixture()
def api(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    repo = Repository(sessionmaker(bind=engine, expire_on_commit=False))

    import app.api.v1.approvals as approvals_module
    import app.api.v1.jobs as jobs_module
    monkeypatch.setattr(approvals_module, "get_repository", lambda: repo)
    monkeypatch.setattr(jobs_module, "get_repository", lambda: repo)
    resumed = []
    monkeypatch.setattr(approvals_module, "resume_job", lambda job_id, value: resumed.append((job_id, value)))

    from app.config import get_settings
    get_settings.cache_clear()
    monkeypatch.setenv("API_KEYS", "test-key")
    monkeypatch.setenv("WORKSPACE_ROOT", str(tmp_path))
    from app.main import create_app
    yield TestClient(create_app(), raise_server_exceptions=False), repo, resumed
    get_settings.cache_clear()


def test_lists_pending_approvals(api):
    client, repo, _ = api
    repo.create_job("j1", "test-key", [], None)
    repo.create_approval("j1", "overwrite", {"kind": "overwrite", "ops": [{"target": "output/a.pdf"}]})
    body = client.get("/api/v1/jobs/j1/approvals", headers=AUTH).json()
    assert len(body["items"]) == 1
    assert body["items"][0]["kind"] == "overwrite"


def test_approving_resumes_the_graph_with_the_decision(api):
    client, repo, resumed = api
    repo.create_job("j1", "test-key", [], None)
    approval = repo.create_approval("j1", "overwrite", {"kind": "overwrite", "ops": [{"target": "output/a.pdf"}]})
    r = client.post(f"/api/v1/jobs/j1/approvals/{approval.id}", json={"decision": "approve"}, headers=AUTH)
    assert r.status_code == 200
    assert repo.list_approvals("j1")[0].status == ApprovalStatus.APPROVED
    job_id, value = resumed[0]
    assert job_id == "j1"
    assert value["decisions"]["output/a.pdf"] == "approve"


def test_rejecting_records_the_decision(api):
    client, repo, resumed = api
    repo.create_job("j1", "test-key", [], None)
    approval = repo.create_approval("j1", "overwrite", {"kind": "overwrite", "ops": [{"target": "output/a.pdf"}]})
    client.post(f"/api/v1/jobs/j1/approvals/{approval.id}",
                json={"decision": "reject", "note": "keep the original"}, headers=AUTH)
    assert repo.list_approvals("j1")[0].status == ApprovalStatus.REJECTED
    assert resumed[0][1]["decisions"]["output/a.pdf"] == "reject"


def test_low_confidence_approval_carries_a_specialty_override(api):
    client, repo, resumed = api
    repo.create_job("j1", "test-key", [], None)
    approval = repo.create_approval("j1", "low_confidence",
                                    {"kind": "low_confidence", "files": [{"file_id": "f1"}]})
    client.post(f"/api/v1/jobs/j1/approvals/{approval.id}",
                json={"decision": "approve", "specialty": "Neurology"}, headers=AUTH)
    assert resumed[0][1]["specialties"]["f1"] == "Neurology"


def test_deciding_an_already_decided_approval_is_a_conflict(api):
    client, repo, _ = api
    repo.create_job("j1", "test-key", [], None)
    approval = repo.create_approval("j1", "overwrite", {"kind": "overwrite", "ops": []})
    client.post(f"/api/v1/jobs/j1/approvals/{approval.id}", json={"decision": "approve"}, headers=AUTH)
    r = client.post(f"/api/v1/jobs/j1/approvals/{approval.id}", json={"decision": "reject"}, headers=AUTH)
    assert r.status_code == 409


def test_code_lookup_returns_the_dictionary_source(api):
    client, _, _ = api
    body = client.get("/api/v1/codes/lookup?code=99213", headers=AUTH).json()
    assert body["found"] is True
    assert body["source"] == "cpt"


def test_code_lookup_reports_unknown_codes(api):
    client, _, _ = api
    body = client.get("/api/v1/codes/lookup?code=ZZZZZ", headers=AUTH).json()
    assert body["found"] is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && pytest tests/test_approvals_api.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.api.v1.approvals'`

- [ ] **Step 3: Implement `app/api/v1/approvals.py`**

```python
from __future__ import annotations

from fastapi import APIRouter, Depends

from ...db.models import ApprovalStatus, JobStatus
from ...db.repository import get_repository
from ...errors import ProblemException
from ...security import require_api_key
from ...tasks import resume_job_task
from .schemas import ApprovalDecisionIn, ApprovalOut, Page

router = APIRouter(tags=["approvals"], dependencies=[Depends(require_api_key)])


def resume_job(job_id: str, resume_value: dict) -> None:
    """Indirection so tests can substitute the Celery dispatch."""
    resume_job_task.delay(job_id, resume_value)


@router.get("/jobs/{job_id}/approvals", response_model=Page)
def list_approvals(job_id: str, status: str | None = None) -> Page:
    repo = get_repository()
    if repo.get_job(job_id) is None:
        raise ProblemException(404, "Not Found", f"Job {job_id!r} does not exist.")
    rows = repo.list_approvals(job_id, status=status)
    items = [ApprovalOut(id=r.id, kind=r.kind, status=r.status, payload=r.payload,
                         created_at=r.created_at).model_dump() for r in rows]
    return Page(items=items, next_cursor=None)


def _resume_value(kind: str, payload: dict, body: ApprovalDecisionIn) -> dict:
    if kind == "low_confidence":
        specialty = body.specialty if body.decision == "approve" and body.specialty else "Unclassified"
        return {"specialties": {f["file_id"]: specialty for f in payload.get("files", [])}}
    return {"decisions": {op["target"]: body.decision for op in payload.get("ops", [])}}


@router.post("/jobs/{job_id}/approvals/{approval_id}", response_model=ApprovalOut)
def decide_approval(job_id: str, approval_id: str, body: ApprovalDecisionIn) -> ApprovalOut:
    repo = get_repository()
    if repo.get_job(job_id) is None:
        raise ProblemException(404, "Not Found", f"Job {job_id!r} does not exist.")

    approval = next((a for a in repo.list_approvals(job_id) if a.id == approval_id), None)
    if approval is None:
        raise ProblemException(404, "Not Found", f"Approval {approval_id!r} does not exist.")
    if approval.status != ApprovalStatus.PENDING:
        raise ProblemException(409, "Conflict", f"Approval {approval_id!r} was already {approval.status}.")

    decided = repo.decide_approval(approval_id, body.decision, body.note)
    repo.audit(job_id, f"approval_{body.decision}d",
               {"approval_id": approval_id, "kind": approval.kind, "note": body.note})
    repo.update_job(job_id, status=JobStatus.RUNNING)
    resume_job(job_id, _resume_value(approval.kind, approval.payload, body))

    return ApprovalOut(id=decided.id, kind=decided.kind, status=decided.status,
                       payload=decided.payload, created_at=decided.created_at)
```

- [ ] **Step 4: Implement `app/api/v1/codes.py`**

```python
from fastapi import APIRouter, Depends, Query

from ...codes.dictionaries import get_dictionaries
from ...security import require_api_key

router = APIRouter(tags=["codes"], dependencies=[Depends(require_api_key)])


@router.get("/codes/lookup")
def lookup(code: str = Query(min_length=2, max_length=10)) -> dict:
    dicts = get_dictionaries()
    source = dicts.contains(code)
    normalized = code.strip().upper()
    return {
        "code": normalized,
        "found": source is not None,
        "source": source,
        "description": dicts.descriptions.get(normalized) or dicts.descriptions.get(normalized.replace(".", "")),
    }
```

- [ ] **Step 5: Register both routers**

```python
# app/api/v1/router.py
from fastapi import APIRouter
from . import approvals, codes, jobs, specialties, system

api_v1 = APIRouter(prefix="/api/v1")
for module in (system, specialties, codes, jobs, approvals):
    api_v1.include_router(module.router)
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `cd backend && pytest tests/test_approvals_api.py -v`
Expected: 7 passed

- [ ] **Step 7: Run the full backend suite**

Run: `cd backend && pytest -v`
Expected: every test from Tasks 1–14 passes.

---

### Task 15: React + Vite frontend

**Files:**
- Create: `frontend/package.json`, `frontend/vite.config.ts`, `frontend/tsconfig.json`, `frontend/index.html`, `frontend/nginx.conf`, `frontend/Dockerfile`, `frontend/src/main.tsx`, `frontend/src/App.tsx`, `frontend/src/styles.css`, `frontend/src/api/client.ts`, `frontend/src/pages/UploadPage.tsx`, `frontend/src/pages/JobsPage.tsx`, `frontend/src/pages/JobDetailPage.tsx`, `frontend/src/components/StageProgress.tsx`, `frontend/src/components/ApprovalCard.tsx`, `frontend/src/components/FileTree.tsx`, `frontend/src/components/CodeEvidence.tsx`
- Test: `frontend/src/api/client.test.ts`

**Interfaces:**
- Consumes: the `/api/v1` endpoints from Tasks 13–14
- Produces:
  - `api.createJob(files: File[]): Promise<JobDetail>`
  - `api.listJobs(cursor?: string): Promise<Page<JobSummary>>`
  - `api.getJob(id: string): Promise<JobDetail>`
  - `api.listFiles(id: string): Promise<Page<FileDetail>>`
  - `api.listApprovals(id: string): Promise<Page<Approval>>`
  - `api.decideApproval(id: string, approvalId: string, body: {decision: 'approve'|'reject'; note?: string; specialty?: string}): Promise<Approval>`
  - `api.getTree(id: string): Promise<{root: string; paths: string[]}>`
  - `api.subscribe(id: string, onEvent: (e: Progress) => void): () => void`
  - `api.downloadUrl(id: string): string`

- [ ] **Step 1: Scaffold and write the failing client test**

```bash
cd frontend
npm create vite@latest . -- --template react-ts
npm install
npm install -D vitest @testing-library/react @testing-library/jest-dom jsdom
npm install react-router-dom
```

Add to `package.json` scripts: `"test": "vitest run"`.

```typescript
// frontend/src/api/client.test.ts
import { afterEach, describe, expect, it, vi } from 'vitest';
import { api, setApiKey } from './client';

afterEach(() => vi.restoreAllMocks());

describe('api client', () => {
  it('sends the API key header', async () => {
    setApiKey('secret');
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ items: [], next_cursor: null }), { status: 200 }),
    );
    vi.stubGlobal('fetch', fetchMock);

    await api.listJobs();

    const headers = fetchMock.mock.calls[0][1].headers as Record<string, string>;
    expect(headers['X-API-Key']).toBe('secret');
  });

  it('posts uploads as multipart form data', async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ id: 'job-1' }), { status: 202 }),
    );
    vi.stubGlobal('fetch', fetchMock);

    const job = await api.createJob([new File(['x'], 'note.pdf')]);

    expect(job.id).toBe('job-1');
    expect(fetchMock.mock.calls[0][1].body).toBeInstanceOf(FormData);
  });

  it('raises the problem+json detail on error', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ title: 'Not Found', detail: 'Job missing', status: 404 }),
        { status: 404, headers: { 'content-type': 'application/problem+json' } }),
    ));

    await expect(api.getJob('nope')).rejects.toThrow('Job missing');
  });
});
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd frontend && npm test`
Expected: FAIL — cannot resolve `./client`

- [ ] **Step 3: Implement `src/api/client.ts`**

```typescript
const BASE = import.meta.env.VITE_API_BASE ?? '/api/v1';
let apiKey = localStorage.getItem('apiKey') ?? '';

export function setApiKey(key: string): void {
  apiKey = key;
  localStorage.setItem('apiKey', key);
}

export function getApiKey(): string {
  return apiKey;
}

export interface Page<T> { items: T[]; next_cursor: string | null }
export interface JobSummary { id: string; status: string; stage: string; progress: number; created_at: string; file_count: number }
export interface JobDetail extends JobSummary { original_filenames: string[]; batch_id: string | null; error: string | null; pending_approvals: number }
export interface CodeHit { code: string; kind: string; rule: string; score: number; context: string; dictionary_hit: boolean }
export interface FileDetail {
  file_id: string; filename: string; source_path: string; status: string; parser: string | null;
  parse_trail: { parser: string; ok: boolean; reason: string | null }[];
  has_codes: boolean; code_hits: CodeHit[]; code_rejected: CodeHit[]; npis: string[];
  specialty: string | null; confidence: number; method: string | null; output_path: string | null;
}
export interface Approval { id: string; kind: string; status: string; payload: Record<string, unknown>; created_at: string }
export interface Progress { status: string; stage: string; progress: number; pending_approvals: number }

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers: Record<string, string> = { 'X-API-Key': apiKey, ...(init.headers as Record<string, string>) };
  if (init.body && !(init.body instanceof FormData)) headers['Content-Type'] = 'application/json';

  const response = await fetch(`${BASE}${path}`, { ...init, headers });
  if (!response.ok) {
    const problem = await response.json().catch(() => ({ detail: response.statusText }));
    throw new Error(problem.detail ?? problem.title ?? 'Request failed');
  }
  return response.json() as Promise<T>;
}

export const api = {
  createJob(files: File[]): Promise<JobDetail> {
    const form = new FormData();
    files.forEach((file) => form.append('files', file));
    return request<JobDetail>('/jobs', { method: 'POST', body: form });
  },
  listJobs(cursor?: string): Promise<Page<JobSummary>> {
    return request<Page<JobSummary>>(`/jobs${cursor ? `?cursor=${encodeURIComponent(cursor)}` : ''}`);
  },
  getJob(id: string): Promise<JobDetail> { return request<JobDetail>(`/jobs/${id}`); },
  listFiles(id: string): Promise<Page<FileDetail>> { return request<Page<FileDetail>>(`/jobs/${id}/files`); },
  listApprovals(id: string): Promise<Page<Approval>> { return request<Page<Approval>>(`/jobs/${id}/approvals`); },
  decideApproval(id: string, approvalId: string,
                 body: { decision: 'approve' | 'reject'; note?: string; specialty?: string }): Promise<Approval> {
    return request<Approval>(`/jobs/${id}/approvals/${approvalId}`, { method: 'POST', body: JSON.stringify(body) });
  },
  getTree(id: string): Promise<{ root: string; paths: string[] }> { return request(`/jobs/${id}/tree`); },
  listSpecialties(): Promise<{ items: { name: string; folder: string }[] }> { return request('/specialties'); },
  downloadUrl(id: string): string { return `${BASE}/jobs/${id}/download`; },
  subscribe(id: string, onEvent: (event: Progress) => void): () => void {
    const source = new EventSource(`${BASE}/jobs/${id}/events`);
    source.addEventListener('progress', (event) => onEvent(JSON.parse((event as MessageEvent).data)));
    return () => source.close();
  },
};
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd frontend && npm test`
Expected: 3 passed

- [ ] **Step 5: Implement the pages and components**

`src/App.tsx` — routes and an API-key field in the header:

```tsx
import { NavLink, Route, Routes } from 'react-router-dom';
import { getApiKey, setApiKey } from './api/client';
import JobDetailPage from './pages/JobDetailPage';
import JobsPage from './pages/JobsPage';
import UploadPage from './pages/UploadPage';

export default function App() {
  return (
    <div className="app">
      <header>
        <h1>Clinical Note Labeller</h1>
        <nav>
          <NavLink to="/">Upload</NavLink>
          <NavLink to="/jobs">Jobs</NavLink>
        </nav>
        <input
          aria-label="API key"
          placeholder="API key"
          defaultValue={getApiKey()}
          onBlur={(event) => setApiKey(event.target.value)}
        />
      </header>
      <main>
        <Routes>
          <Route path="/" element={<UploadPage />} />
          <Route path="/jobs" element={<JobsPage />} />
          <Route path="/jobs/:jobId" element={<JobDetailPage />} />
        </Routes>
      </main>
    </div>
  );
}
```

`src/pages/UploadPage.tsx` — drag-and-drop that posts to `api.createJob` and navigates to the new job:

```tsx
import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { api } from '../api/client';

export default function UploadPage() {
  const [files, setFiles] = useState<File[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const navigate = useNavigate();

  async function submit() {
    setBusy(true);
    setError(null);
    try {
      const job = await api.createJob(files);
      navigate(`/jobs/${job.id}`);
    } catch (exc) {
      setError((exc as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <section
      className="dropzone"
      onDragOver={(event) => event.preventDefault()}
      onDrop={(event) => { event.preventDefault(); setFiles(Array.from(event.dataTransfer.files)); }}
    >
      <p>Drop clinical notes here — PDF, DOCX, text, or ZIP.</p>
      <input type="file" multiple onChange={(event) => setFiles(Array.from(event.target.files ?? []))} />
      <ul>{files.map((file) => <li key={file.name}>{file.name} ({Math.round(file.size / 1024)} KB)</li>)}</ul>
      <button disabled={!files.length || busy} onClick={submit}>
        {busy ? 'Uploading…' : `Label ${files.length} file(s)`}
      </button>
      {error && <p className="error">{error}</p>}
    </section>
  );
}
```

`src/pages/JobDetailPage.tsx` — live progress, approvals inbox, file table and tree:

```tsx
import { useEffect, useState } from 'react';
import { useParams } from 'react-router-dom';
import { api, Approval, FileDetail, JobDetail, Progress } from '../api/client';
import ApprovalCard from '../components/ApprovalCard';
import CodeEvidence from '../components/CodeEvidence';
import FileTree from '../components/FileTree';
import StageProgress from '../components/StageProgress';

export default function JobDetailPage() {
  const { jobId = '' } = useParams();
  const [job, setJob] = useState<JobDetail | null>(null);
  const [files, setFiles] = useState<FileDetail[]>([]);
  const [approvals, setApprovals] = useState<Approval[]>([]);
  const [tree, setTree] = useState<string[]>([]);
  const [selected, setSelected] = useState<FileDetail | null>(null);

  async function refresh() {
    setJob(await api.getJob(jobId));
    setFiles((await api.listFiles(jobId)).items);
    setApprovals((await api.listApprovals(jobId)).items.filter((a) => a.status === 'pending'));
    setTree((await api.getTree(jobId)).paths);
  }

  useEffect(() => {
    refresh();
    return api.subscribe(jobId, (_event: Progress) => { refresh(); });
  }, [jobId]);

  if (!job) return <p>Loading…</p>;

  return (
    <section>
      <h2>Job {job.id.slice(0, 8)}</h2>
      <StageProgress stage={job.stage} status={job.status} progress={job.progress} />
      {job.error && <p className="error">{job.error}</p>}

      {approvals.length > 0 && (
        <section className="approvals">
          <h3>Approvals needed ({approvals.length})</h3>
          {approvals.map((approval) => (
            <ApprovalCard key={approval.id} jobId={jobId} approval={approval} onDecided={refresh} />
          ))}
        </section>
      )}

      <table>
        <thead><tr><th>File</th><th>Codes</th><th>Specialty</th><th>Method</th><th>Confidence</th></tr></thead>
        <tbody>
          {files.map((file) => (
            <tr key={file.file_id} onClick={() => setSelected(file)}>
              <td>{file.filename}</td>
              <td>{file.status === 'unparsed' ? 'unparsed' : file.has_codes ? 'with-codes' : 'without-codes'}</td>
              <td>{file.specialty ?? '—'}</td>
              <td>{file.method ?? '—'}</td>
              <td>{file.confidence.toFixed(2)}</td>
            </tr>
          ))}
        </tbody>
      </table>

      {selected && <CodeEvidence file={selected} />}
      <FileTree paths={tree} />
      <a href={api.downloadUrl(jobId)}>Download output.zip</a>
    </section>
  );
}
```

`src/components/ApprovalCard.tsx` — the guardrail UI:

```tsx
import { useEffect, useState } from 'react';
import { api, Approval } from '../api/client';

export default function ApprovalCard({ jobId, approval, onDecided }:
  { jobId: string; approval: Approval; onDecided: () => void }) {
  const [note, setNote] = useState('');
  const [specialty, setSpecialty] = useState('');
  const [options, setOptions] = useState<string[]>([]);

  useEffect(() => {
    if (approval.kind === 'low_confidence') {
      api.listSpecialties().then((r) => setOptions(r.items.map((i) => i.name)));
    }
  }, [approval.kind]);

  async function decide(decision: 'approve' | 'reject') {
    await api.decideApproval(jobId, approval.id, { decision, note, specialty: specialty || undefined });
    onDecided();
  }

  return (
    <article className="approval">
      <h4>{approval.kind.replace('_', ' ')}</h4>
      <pre>{JSON.stringify(approval.payload, null, 2)}</pre>
      {approval.kind === 'low_confidence' && (
        <select value={specialty} onChange={(event) => setSpecialty(event.target.value)}>
          <option value="">Choose a specialty…</option>
          {options.map((name) => <option key={name} value={name}>{name}</option>)}
        </select>
      )}
      <input placeholder="Note (optional)" value={note} onChange={(event) => setNote(event.target.value)} />
      <button onClick={() => decide('approve')}>Approve</button>
      <button onClick={() => decide('reject')}>Reject</button>
    </article>
  );
}
```

`src/components/CodeEvidence.tsx`, `FileTree.tsx`, `StageProgress.tsx`:

```tsx
// CodeEvidence.tsx
import { FileDetail } from '../api/client';

export default function CodeEvidence({ file }: { file: FileDetail }) {
  return (
    <section className="evidence">
      <h3>{file.filename}</h3>
      <p>Parser chain: {file.parse_trail.map((a) => `${a.parser}${a.ok ? '' : ' ✗'}`).join(' → ') || '—'}</p>
      <p>NPIs: {file.npis.join(', ') || 'none'}</p>
      <h4>Accepted codes</h4>
      <ul>{file.code_hits.map((hit, index) => (
        <li key={index}><code>{hit.code}</code> [{hit.kind}] {hit.rule} — “{hit.context}”</li>
      ))}</ul>
      <h4>Rejected candidates</h4>
      <ul>{file.code_rejected.map((hit, index) => (
        <li key={index}><code>{hit.code}</code> {hit.rule}</li>
      ))}</ul>
    </section>
  );
}
```

```tsx
// FileTree.tsx
export default function FileTree({ paths }: { paths: string[] }) {
  return (
    <section className="tree">
      <h3>Output ({paths.length} files)</h3>
      <ul>{paths.map((path) => <li key={path}><code>{path}</code></li>)}</ul>
    </section>
  );
}
```

```tsx
// StageProgress.tsx
const STAGES = ['intake', 'unpack', 'parse', 'detect_codes', 'resolve_npi',
                'classify', 'plan_placement', 'approval_gate', 'execute_ops', 'manifest'];

export default function StageProgress({ stage, status, progress }:
  { stage: string; status: string; progress: number }) {
  const current = STAGES.indexOf(stage);
  return (
    <div className="stages">
      <p>{status} — {Math.round(progress * 100)}%</p>
      <ol>{STAGES.map((name, index) => (
        <li key={name} className={index <= current ? 'done' : ''}>{name}</li>
      ))}</ol>
    </div>
  );
}
```

`src/main.tsx`:

```tsx
import React from 'react';
import ReactDOM from 'react-dom/client';
import { BrowserRouter } from 'react-router-dom';
import App from './App';
import './styles.css';

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode><BrowserRouter><App /></BrowserRouter></React.StrictMode>,
);
```

- [ ] **Step 6: Add `vite.config.ts` proxy, `nginx.conf`, and the Dockerfile**

```typescript
// vite.config.ts
import react from '@vitejs/plugin-react';
import { defineConfig } from 'vite';

export default defineConfig({
  plugins: [react()],
  server: { port: 5173, proxy: { '/api': { target: 'http://localhost:8000', changeOrigin: true } } },
  test: { environment: 'jsdom' },
});
```

```nginx
# frontend/nginx.conf
server {
  listen 80;
  root /usr/share/nginx/html;
  index index.html;

  location /api/ {
    proxy_pass http://api:8000;
    proxy_buffering off;                 # required for SSE
    proxy_read_timeout 3600s;
    proxy_set_header X-Request-ID $request_id;
  }

  location / { try_files $uri $uri/ /index.html; }
}
```

```dockerfile
# frontend/Dockerfile
FROM node:22-alpine AS build
WORKDIR /app
COPY package*.json ./
RUN npm ci
COPY . .
RUN npm run build

FROM nginx:1.27-alpine
COPY --from=build /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
```

- [ ] **Step 7: Verify the build**

Run: `cd frontend && npm run build && npm test`
Expected: a clean production build and 3 passing tests

---

### Task 16: Docker Compose wiring and the end-to-end smoke test

**Files:**
- Create: `backend/Dockerfile`, `backend/.dockerignore`, `docker-compose.yml`, `Makefile`, `README.md`
- Test: `backend/tests/test_e2e_pipeline.py`

**Interfaces:**
- Consumes: every previous task
- Produces: a running stack — `docker compose up --build` serves the UI on `http://localhost:5173` and the API on `http://localhost:8000/api/v1`

- [ ] **Step 1: Write the failing end-to-end test**

```python
# backend/tests/test_e2e_pipeline.py
"""End-to-end: mixed upload -> parse -> detect -> classify -> filed output tree.

The OpenAI and NPI calls are stubbed; everything else is the real pipeline.
"""
import zipfile
import pytest
from langgraph.checkpoint.memory import MemorySaver
from app.agent import nodes as node_module
from app.agent.graph import run_job


@pytest.fixture()
def workspace(tmp_path):
    root = tmp_path / "job-e2e"
    inbox = root / "input"
    inbox.mkdir(parents=True)
    (inbox / "cardiac.txt").write_text(
        "ASSESSMENT\nDiagnosis Code: I10 essential hypertension\nProcedure Code: 99213-25\n")
    (inbox / "narrative.txt").write_text(
        "The patient is a 45 year old who lives in Beverly Hills, CA 90210 and reports a headache.\n")
    with zipfile.ZipFile(inbox / "bundle.zip", "w") as zf:
        zf.writestr("derm/rash.txt", "Diagnosis Code: L20.9 atopic dermatitis. CPT: 11100 biopsy.")
    return root


@pytest.fixture()
def stub_external(monkeypatch):
    async def fake_classify_node(state):
        mapping = {"cardiac.txt": "Cardiology", "narrative.txt": "Family Medicine", "rash.txt": "Dermatology"}
        files = [{**f, "specialty": mapping.get(f["filename"], "Unclassified"),
                  "confidence": 0.95, "method": "llm_sync"} for f in state["files"]]
        return {"files": files, "stage": "classify"}

    async def fake_resolve_npi_node(state):
        return {"files": state["files"], "stage": "resolve_npi"}

    async def fake_parse_node(state):
        from pathlib import Path
        files = [{**f, "text": Path(f["path"]).read_text(), "ok": True, "parser": "text",
                  "parse_trail": [{"parser": "text", "ok": True, "reason": None}]} for f in state["files"]]
        return {"files": files, "stage": "parse"}

    monkeypatch.setattr(node_module, "classify_node", fake_classify_node)
    monkeypatch.setattr(node_module, "resolve_npi_node", fake_resolve_npi_node)
    monkeypatch.setattr(node_module, "parse_node", fake_parse_node)


async def test_full_pipeline_produces_the_expected_output_tree(workspace, stub_external):
    await run_job("job-e2e", workspace, MemorySaver())
    output = workspace / "output"

    assert (output / "with-codes" / "Cardiology" / "cardiac.txt").exists()
    assert (output / "with-codes" / "Dermatology" / "rash.txt").exists()
    assert (output / "without-codes" / "Family-Medicine" / "narrative.txt").exists()


async def test_zip_contents_are_flattened_but_source_is_recorded(workspace, stub_external):
    import json
    await run_job("job-e2e", workspace, MemorySaver())
    records = [json.loads(line) for line in (workspace / "output" / "manifest.jsonl").read_text().splitlines()]
    rash = next(r for r in records if r["filename"] == "rash.txt")
    assert rash["source_path"] == "bundle.zip!/derm/rash.txt"
    assert rash["output_path"] == "output/with-codes/Dermatology/rash.txt"


async def test_the_zip_itself_is_not_filed(workspace, stub_external):
    await run_job("job-e2e", workspace, MemorySaver())
    assert not list((workspace / "output").rglob("bundle.zip"))


async def test_labels_csv_covers_every_file(workspace, stub_external):
    import csv
    await run_job("job-e2e", workspace, MemorySaver())
    rows = list(csv.DictReader((workspace / "output" / "labels.csv").open()))
    assert {r["filename"] for r in rows} == {"cardiac.txt", "narrative.txt", "rash.txt"}


async def test_input_folder_is_untouched(workspace, stub_external):
    before = {p.name for p in (workspace / "input").iterdir()}
    await run_job("job-e2e", workspace, MemorySaver())
    assert {p.name for p in (workspace / "input").iterdir()} == before
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd backend && pytest tests/test_e2e_pipeline.py -v`
Expected: FAIL until the Docker/reference wiring in the next steps is in place — specifically, `get_dictionaries()` must find the reference data. Set `REFERENCE_ROOT` to the repository root when running tests locally:
`cd backend && REFERENCE_ROOT=.. pytest tests/test_e2e_pipeline.py -v`

- [ ] **Step 3: Write `backend/Dockerfile` and `.dockerignore`**

```dockerfile
FROM python:3.12-slim AS base

ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
RUN apt-get update && apt-get install -y --no-install-recommends build-essential curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /srv
COPY pyproject.toml .
RUN pip install --no-cache-dir .

COPY . .
RUN useradd --create-home --uid 10002 appuser && chown -R appuser /srv
USER appuser

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

```text
# backend/.dockerignore
.env
__pycache__/
.venv/
tests/
*.pyc
```

- [ ] **Step 4: Write `docker-compose.yml`**

```yaml
name: clinical-note-labeller

x-backend-env: &backend-env
  DATABASE_URL: postgresql+psycopg://labeller:labeller@postgres:5432/labeller
  REDIS_URL: redis://redis:6379/0
  WORKSPACE_ROOT: /data/workspace
  REFERENCE_ROOT: /data/reference
  SANDBOX_URL: http://parser-sandbox:8081
  S3_ENDPOINT: http://minio:9000
  S3_ACCESS_KEY: ${S3_ACCESS_KEY:-minioadmin}
  S3_SECRET_KEY: ${S3_SECRET_KEY:-minioadmin}
  S3_BUCKET: uploads
  API_KEYS: ${API_KEYS:-dev-key}
  OPENAI_API_KEY: ${OPENAI_API_KEY}
  OPENAI_MINI_MODEL_ID: ${OPENAI_MINI_MODEL_ID:-gpt-5.4-mini}
  LLAMA_CLOUD_API_KEY: ${LLAMA_CLOUD_API_KEY}
  CODE_EVIDENCE_THRESHOLD: ${CODE_EVIDENCE_THRESHOLD:-1.0}
  SPECIALTY_CONFIDENCE_THRESHOLD: ${SPECIALTY_CONFIDENCE_THRESHOLD:-0.65}
  LLM_BATCH_MIN_FILES: ${LLM_BATCH_MIN_FILES:-10}

services:
  postgres:
    image: postgres:16-alpine
    environment:
      POSTGRES_USER: labeller
      POSTGRES_PASSWORD: labeller
      POSTGRES_DB: labeller
    volumes: ["pgdata:/var/lib/postgresql/data"]
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U labeller"]
      interval: 5s
      retries: 10
    networks: [appnet]

  redis:
    image: redis:7-alpine
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 5s
      retries: 10
    networks: [appnet]

  minio:
    image: minio/minio:latest
    command: server /data --console-address ":9001"
    environment:
      MINIO_ROOT_USER: ${S3_ACCESS_KEY:-minioadmin}
      MINIO_ROOT_PASSWORD: ${S3_SECRET_KEY:-minioadmin}
    volumes: ["miniodata:/data"]
    ports: ["9001:9001"]
    networks: [appnet]

  parser-sandbox:
    build: ./sandbox
    # No egress: this network has no gateway to the outside world.
    networks: [sandboxnet]
    read_only: true
    tmpfs: ["/tmp:size=2g"]
    security_opt: ["no-new-privileges:true"]
    cap_drop: ["ALL"]
    pids_limit: 256
    mem_limit: 2g
    cpus: 2.0

  api:
    build: ./backend
    environment: *backend-env
    depends_on:
      postgres: {condition: service_healthy}
      redis: {condition: service_healthy}
    volumes:
      - workspace:/data/workspace
      - ./cpt-codes:/data/reference/cpt-codes:ro
      - ./ict-10-codes:/data/reference/ict-10-codes:ro
    command: >
      sh -c "alembic upgrade head &&
             uvicorn app.main:app --host 0.0.0.0 --port 8000"
    ports: ["8000:8000"]
    networks: [appnet]

  worker:
    build: ./backend
    environment: *backend-env
    depends_on:
      postgres: {condition: service_healthy}
      redis: {condition: service_healthy}
    volumes:
      - workspace:/data/workspace
      - ./cpt-codes:/data/reference/cpt-codes:ro
      - ./ict-10-codes:/data/reference/ict-10-codes:ro
    command: celery -A app.tasks.celery_app worker --loglevel=INFO --concurrency=4
    # On both networks: reaches the sandbox and the internet (OpenAI, LlamaParse, NPI).
    networks: [appnet, sandboxnet]

  frontend:
    build: ./frontend
    depends_on: [api]
    ports: ["5173:80"]
    networks: [appnet]

volumes:
  pgdata:
  miniodata:
  workspace:

networks:
  appnet:
  sandboxnet:
    internal: true
```

- [ ] **Step 5: Write the `Makefile` and `README.md`**

```makefile
.PHONY: up down logs test fmt
up:      ; docker compose up --build -d
down:    ; docker compose down -v
logs:    ; docker compose logs -f api worker
test:    ; cd backend && REFERENCE_ROOT=.. pytest -v && cd ../frontend && npm test
fmt:     ; cd backend && ruff check --fix . && ruff format .
```

`README.md` must document: copying `.env.example` to `.env` and filling in `OPENAI_API_KEY` / `LLAMA_CLOUD_API_KEY` / `API_KEYS`, `make up`, the UI at `http://localhost:5173`, the OpenAPI docs at `http://localhost:8000/docs`, the output-tree contract, and the four guardrails.

- [ ] **Step 6: Run the end-to-end test**

Run: `cd backend && REFERENCE_ROOT=.. pytest tests/test_e2e_pipeline.py -v`
Expected: 5 passed

- [ ] **Step 7: Run the whole suite and bring the stack up**

Run: `cd backend && REFERENCE_ROOT=.. pytest -v`
Expected: every test in the plan passes.

Run: `docker compose up --build -d && sleep 30 && curl -s localhost:8000/api/v1/health`
Expected: `{"status":"ok"}`

Run: `curl -s -H "X-API-Key: dev-key" -F "files=@backend/tests/fixtures/notes/coded.txt" localhost:8000/api/v1/jobs`
Expected: HTTP 202 with a job id; `docker compose logs worker` shows the pipeline running to `manifest`.

---

## Self-Review

**Spec coverage**

| Spec section | Task(s) |
|---|---|
| §2 Reference data | 2 |
| §3 Output contract | 11, 12, 16 |
| §4 Extraction chain | 6, 7 |
| §5 Code detection | 3, 4 |
| §6 Specialty classification | 8, 9, 12 |
| §7 Agent architecture | 12 |
| §8 Guardrails | 11, 12, 14 |
| §9 Services | 6, 13, 16 |
| §10 API surface | 1, 13, 14 |
| §11 Frontend | 15 |
| §12 Testing | every task |
| §13 Configuration | 1, 16 |

**Known deviations from the spec, deliberate:**
- Uploads stream to the workspace volume rather than MinIO in v1; MinIO is provisioned in Compose and `app/storage.py` is the single place to swap in presigned uploads. This keeps Task 13 testable without object storage.
- `GET /api/v1/metrics` is mounted at the app level rather than on the v1 router, so scraping does not require an API key.
