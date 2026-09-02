# Clinical Note Labeller — Design

Date: 2026-09-02
Status: Approved for implementation

## 1. Purpose

Ingest clinical notes (PDF, DOCX, plain text, ZIP archives), determine whether each
note contains medical billing/clinical codes, determine the clinical specialty of
each note, and file every note into a deterministic output tree:

```
output/{with-codes|without-codes}/<Specialty>/<file>
```

The system is an agentic, resumable, dockerized service with a FastAPI `/api/v1`
backend and a React + Vite frontend.

## 2. Reference data (verified 2026-09-02)

| File | Content | Count |
|---|---|---|
| `cpt-codes/cpt-codes.txt` | HCPCS Level II only, all letter-prefixed, fixed-width | 16,734 |
| `cpt-codes/cpt.xlsx` | 2026 PFS RVU file (`PPRRVU2026_Jan_nonQPP`), numeric CPT + HCPCS | 16,978 |
| `cpt-codes/cpt-chapters.txt` | 10 CPT chapter ranges | 10 |
| `ict-10-codes/icd10cm_order_2026.txt` | ICD-10-CM order file, fixed-width | 98,186 |
| `ict-10-codes/ict-10-chapters.txt` | 22 ICD-10 chapter ranges | 22 |

Verified coverage in `cpt.xlsx`: `99213` yes, `99490` yes, `80053` yes, `87880` yes,
`36415` yes, `0001F` yes, `19307` yes, `J1885` yes, `A0425` yes; `0042T` **no**.

Consequence: full CPT is AMA-licensed and cannot be redistributed, so the local
dictionary has holes (mostly Category III). The detector compensates with the
contextual fallback in §5, and a bundled modifier list asset.

Loaders must be tolerant of the fixed-width quirks observed:
- `cpt-codes.txt`: 16,157 lines start at column 0, 576 lines have 3 leading spaces,
  1 line has 297. Extract the code by `line.strip()[:5]`.
- `icd10cm_order_2026.txt`: order-number, code, valid-flag, short desc, long desc.
  Codes are stored without the decimal point; normalize by stripping `.`.

## 3. Output contract

```
workspace/<job_id>/
  input/                      uploaded bytes, immutable, never mutated or deleted
  extracted/                  recursive ZIP expansion, sandboxed
  output/
    with-codes/<Specialty>/<file>
    without-codes/<Specialty>/<file>
    unparsed/<file>           quarantine for files no parser could read
    manifest.jsonl
    labels.csv
  logs/audit.jsonl            append-only guardrail + approval audit trail
```

Rules:

1. Files are **copied** into `output/`. `input/` is immutable.
2. ZIPs are expanded recursively; the internal folder structure is **flattened** in
   the output tree but preserved in `manifest.source_path`.
3. Branch folders are created lazily — no empty `with-codes/` when nothing has codes.
4. Name collisions raise an approval (overwrite guardrail). Rejection falls back to
   a `name__2.ext` suffix.
5. Specialty folder names come from a **fixed NUCC-derived taxonomy** (§6), plus
   `Unclassified`.
6. A bare NPI does **not** make a note "with codes" — it is a provider identifier,
   not a billing or clinical code. Only CPT, HCPCS Level II, ICD-10-CM and
   modifiers count.

## 4. Extraction chain

Per file, in order, stopping at first success producing non-trivial text:

1. `pypdf` (PDF), `python-docx` (DOCX), charset-detected decode (txt/md/rtf/csv/json)
2. **LlamaParse** (`LLAMA_CLOUD_API_KEY`) — only for files that failed step 1
3. **Tesseract OCR** — scanned/image PDFs that failed step 2
4. `output/unparsed/` quarantine, surfaced in the UI

Network split: steps 1 and 3 run inside the **no-egress parser sandbox**; step 2 runs
in the worker, which has egress. The sandbox never receives an API key.

Every hop is recorded per file (`parse_trail`) so the reason a file needed the
expensive path is visible.

## 5. Code detection

Two-stage: regex candidates, then dictionary validation plus contextual evidence
scoring. Raw regex alone is unusable on clinical text — a patient ZIP code `90210`
matches the CPT shape, and `04/25` matches a modifier.

### Stage A — candidate regexes

| Kind | Pattern |
|---|---|
| ICD-10-CM | `\b[A-TV-Z]\d[0-9A-Z](?:\.?[0-9A-Z]{1,4})?\b` |
| CPT | `\b\d{4}[\dFTU]\b` |
| HCPCS II | `\b[A-V]\d{4}\b` |
| Modifier | 2-char, only when hyphen-attached to a validated code or in the bundled modifier list |
| NPI | `\b\d{10}\b` validated by Luhn with the `80840` prefix |

### Stage B — evidence scoring

A candidate is a real code when it is **dictionary-validated**, or **structurally
valid with a nearby positive cue** (`CPT`, `HCPCS`, `ICD-10`, `Dx:`, `Diagnosis Code`,
`Procedure Code`, claim-line or table context within a configurable window).

Negative context subtracts: ZIP code following a state abbreviation, phone number
shapes, dates, vital signs, MRN/account-number labels.

The document is `with-codes` when total evidence clears a configurable threshold.
Every accepted and rejected candidate is written to the manifest with its matched
string, character offset, deciding rule, and dictionary-hit flag, so a wrong bucket
is debuggable.

Dictionaries load once per worker process from the mounted reference folder into
in-memory frozensets. The modifier list ships as a versioned JSON asset.

## 6. Specialty classification

Fixed closed taxonomy derived from the CMS/NUCC provider taxonomy (~40 clinical
specialties) plus `Unclassified`, shipped as a versioned JSON asset and served by
`GET /api/v1/specialties`.

Resolution order:

1. **NPI path (no LLM).** Extract Luhn-valid NPIs, query the NPI Registry
   (`https://npiregistry.cms.hhs.gov/api/?version=2.1&number=<npi>`), take the primary
   taxonomy, map the NUCC taxonomy code to the fixed specialty list. Responses are
   cached in Postgres. When several NPIs are present (referring / rendering /
   facility), prefer the one whose taxonomy is an individual clinical specialty; if
   still ambiguous, fall through to the LLM.
2. **LLM path.** OpenAI **Batch API** (`/v1/batches`, 50% discount) with structured
   outputs constrained to the fixed list, returning `{specialty, confidence,
   rationale}`. Jobs of fewer than 10 files use the synchronous chat completions
   endpoint instead so small uploads are not delayed by batch turnaround. Model id
   from `OPENAI_MINI_MODEL_ID`; key from `OPENAI_API_KEY`. Both read from `.env`,
   never hardcoded.
3. **Low confidence** (below configurable threshold) raises a human approval
   (§8). Rejected or unresolved notes go to `Unclassified/`.

`manifest.method` records `npi | llm_batch | llm_sync | human` per file.

## 7. Agent architecture

LangGraph `StateGraph`, checkpointed with `AsyncPostgresSaver`, thread id = job id.

```
intake -> unpack -> parse -> detect_codes -> resolve_npi -> classify
       -> plan_placement -> [approval_gate] -> execute_ops -> manifest
```

The checkpointer makes the long path survivable: the graph parks on `interrupt()`
while an OpenAI batch runs or an approval is pending, and resumes via
`Command(resume=...)`. Restarts and redeploys do not lose a job.

Deterministic nodes handle parsing, detection and filing. The single **agent** node
is `plan_placement`: it reasons over messy ZIP layouts, filename collisions and odd
names, and emits a proposed operation list. It never touches the filesystem
directly. All writes go through LangChain `FileManagementToolkit(root_dir=<job
workspace>)` plus a path-traversal validator.

## 8. Guardrails

| Action | Behaviour |
|---|---|
| Delete any file | Pause, require approval in the UI |
| Overwrite an existing file | Pause, require approval; rejection auto-suffixes |
| Low-confidence specialty placement | Pause, require confirmation |
| Move/write outside the trusted root | **Denied outright** with an audit entry — never approvable |

Every proposal, decision and denial is appended to `logs/audit.jsonl` and the
Postgres audit table.

## 9. Services

| Container | Role |
|---|---|
| `api` | FastAPI, `/api/v1`, uvicorn |
| `worker` | Celery + Redis; runs the LangGraph job; has egress (OpenAI, LlamaParse, NPI) |
| `parser-sandbox` | Hardened, **no egress** (internal docker network), non-root, read-only mounts, memory/pid/CPU caps, zip-bomb and path-traversal limits |
| `postgres` | Jobs, LangGraph checkpoints, NPI cache, audit log |
| `redis` | Celery broker and cache |
| `minio` | S3-compatible object storage for multi-GB uploads |
| `frontend` | React + Vite build served by nginx |

## 10. API surface (v1)

```
POST /api/v1/jobs                            multipart or presigned MinIO upload; Idempotency-Key
GET  /api/v1/jobs?status=&cursor=            cursor-paginated envelope
GET  /api/v1/jobs/{id}                       status + per-stage progress
GET  /api/v1/jobs/{id}/events                SSE live progress
GET  /api/v1/jobs/{id}/files                 paginated file list
GET  /api/v1/jobs/{id}/files/{file_id}       codes + evidence + specialty + parse trail
GET  /api/v1/jobs/{id}/approvals
POST /api/v1/jobs/{id}/approvals/{aid}       {decision, note} -> resumes the graph
POST /api/v1/jobs/{id}/cancel
GET  /api/v1/jobs/{id}/tree
GET  /api/v1/jobs/{id}/download              zip of output/
GET  /api/v1/jobs/{id}/manifest.csv
GET  /api/v1/codes/lookup?code=
GET  /api/v1/specialties
GET  /api/v1/health  /readyz  /version  /metrics
```

Cross-cutting: `X-API-Key` auth with per-key rate limiting, RFC 7807 `problem+json`
errors, request-id correlation header, structured JSON logging, Prometheus metrics,
OpenAPI at `/api/v1/openapi.json`, isolated `/api/v1` router so a future v2 can
co-exist.

## 11. Frontend

React + Vite + TypeScript.

- Upload: drag-drop, multi-file and ZIP, progress
- Job list and job detail with live stage progress over SSE
- **Approvals inbox**: each proposed operation, why it needs sign-off, approve/reject
- Result tree browser, per-file label detail with highlighted code hits and evidence
- Download results

## 12. Testing

Test-driven throughout.

- Golden-corpus precision/recall tests for the code detector (highest-risk component)
- Synthetic clinical-note fixtures covering: coded note, uncoded narrative, note with
  NPI, scanned PDF, nested ZIP, colliding filenames, zip bomb
- `respx` cassettes for NPI Registry and OpenAI
- testcontainers for Postgres and MinIO
- LangGraph interrupt/resume tests
- Playwright smoke test on the approval flow

## 13. Configuration

`.env` (gitignored), never hardcoded:

```
OPENAI_API_KEY=
OPENAI_MINI_MODEL_ID=gpt-5.4-mini
LLAMA_CLOUD_API_KEY=
API_KEYS=
DATABASE_URL=
REDIS_URL=
S3_ENDPOINT= S3_ACCESS_KEY= S3_SECRET_KEY=
WORKSPACE_ROOT=/data/workspace
CODE_EVIDENCE_THRESHOLD=
SPECIALTY_CONFIDENCE_THRESHOLD=
LLM_BATCH_MIN_FILES=10
```

## 14. Out of scope for v1

- Splitting a single file that contains multiple distinct notes
- PHI de-identification
- User accounts / RBAC (API-key auth only)
- Re-training or fine-tuning any model
