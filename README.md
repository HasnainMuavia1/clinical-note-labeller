# Clinical Note Labeller

Ingests clinical notes (PDF, DOCX, plain text, ZIP), decides whether each note contains
medical codes, determines its clinical specialty, and files every note into a
deterministic output tree:

```
output/
  with-codes/<Specialty>/<file>
  without-codes/<Specialty>/<file>
  unparsed/<file>            # nothing could parse it; surfaced in the UI
  manifest.jsonl             # full evidence per file
  labels.csv                 # flat label table
logs/audit.jsonl             # every guarded operation and approval decision
```

Uploaded bytes in `input/` are never modified or deleted — files are **copied** into `output/`.

## Quick start

```bash
cp .env.example .env      # then fill in OPENAI_API_KEY (and LLAMA_CLOUD_API_KEY if you have one)
make up                   # docker compose up --build -d
```

- UI: http://localhost:5173 (open — no login or workspace key)
- API: http://localhost:8000/api/v1
- OpenAPI: http://localhost:8000/docs
- MinIO console: http://localhost:9101

```bash
curl -s -F "files=@note.pdf" http://localhost:8000/api/v1/jobs
```

## Giving it to a client

A client needs one file and no configuration. Build it with:

```bash
python3 tools/make_installer.py
```

That writes two files. Send whichever matches the client's machine:

| Client | File | Output appears in |
|---|---|---|
| Windows | `dist/Clinical-Note-Labeller-Setup.exe` (or `.bat`) | `%USERPROFILE%\ClinicalNoteLabeller\workspace` |
| macOS / Linux | `dist/Clinical-Note-Labeller-Setup.command` | `~/ClinicalNoteLabeller/workspace` |

Either one installs Docker if needed, pulls the published images, starts
everything and opens the browser. Images are published for both amd64 and arm64,
so Intel and Apple Silicon are both native.

On macOS the first launch is blocked by Gatekeeper because the file is unsigned:
**right-click the file → Open → Open**. Only needed once.

The generated file carries live API keys in plain text — read
[PUBLISHING.md](PUBLISHING.md) before handing it out.

## How a job runs

LangGraph `StateGraph`, checkpointed in Postgres (thread id = job id), so a job survives
restarts and can park for hours on an OpenAI batch or a human approval:

```
intake → unpack → parse → detect_codes → resolve_npi → classify
       → plan_placement → [approval_gate] → execute_ops → manifest
```

### Parsing

`pypdf` / `python-docx` / charset-detected text → **LlamaParse** (only for failures) →
**Tesseract OCR** → `output/unparsed/`. Every hop is recorded per file as `parse_trail`, so
you can see exactly why a file needed the expensive path.

LlamaParse is called through its REST API directly rather than the official SDK,
which would pull the entire `llama-index` stack (numpy, pillow, nltk, networkx) —
about 155 MB — to perform one upload and one poll.

Steps 1 and 3 run inside `parser-sandbox`: a container on an `internal: true` Docker network
with no route to the internet, a read-only root filesystem, all capabilities dropped, and
memory/pid/CPU caps. It never receives an API key. LlamaParse is called from the worker,
which is the only container holding credentials.

### Code detection

Two stages, because raw regex on clinical text is a false-positive machine — a patient's ZIP
code `90210` matches the CPT shape and `04/25` matches a modifier.

1. **Candidates** — ICD-10 `[A-TV-Z]\d[0-9A-Z](\.?[0-9A-Z]{1,4})?`, CPT `\d{4}[\dFTUM]`,
   HCPCS `[A-V]\d{4}`, modifiers only when hyphen-attached to a validated code, NPI
   `\d{10}` validated by Luhn over the `80840` prefix.
2. **Evidence scoring** — a candidate counts when it is **dictionary-validated**, or
   **structurally valid with a nearby cue** (`CPT:`, `Diagnosis Code`, claim/billing context).
   Negative context subtracts: ZIP after a state abbreviation, phone shapes, dates, vitals,
   MRN labels.

Every accepted *and rejected* candidate is written to the manifest with its matched string,
offset, deciding rule and dictionary-hit flag, so a wrong bucket is debuggable.

Dictionaries loaded from the mounted reference folder:

| Source | Content | Codes |
|---|---|---|
| `cpt-codes/cpt.xlsx` | 2026 PFS RVU file — numeric CPT + HCPCS | 16,978 |
| `cpt-codes/cpt-codes.txt` | HCPCS Level II (16,734 rows, 9,229 distinct) | 9,229 |
| `ict-10-codes/icd10cm_order_2026.txt` | ICD-10-CM 2026 order file | 98,186 |

Full CPT is AMA-licensed and cannot be redistributed, so the dictionary has holes (mostly
Category III, e.g. `0042T`). The contextual-cue rule is what covers them.

**An NPI alone does not make a note "coded."** It is a provider identifier, not a billing code.

### Specialty classification

1. **NPI path (no LLM).** Luhn-valid NPIs → NPI Registry v2.1 → primary taxonomy → the fixed
   NUCC specialty list. Several NPIs in one note resolve to the individual clinician's
   taxonomy over an organisation's.
2. **LLM path.** OpenAI **Batch API** (`/v1/batches`, 50% cheaper) with a structured output
   constrained to the closed specialty list. Jobs under `LLM_BATCH_MIN_FILES` use the
   synchronous endpoint so small uploads aren't waiting on batch turnaround.
3. **Low confidence** raises a human approval; unresolved notes go to `Unclassified/`.

`manifest.method` records `npi | llm_batch | llm_sync | human` per file.

## Guardrails

| Action | Behaviour |
|---|---|
| Delete any file | Job pauses; approve in the UI |
| Overwrite an existing file | Job pauses; rejecting auto-suffixes `name__2.ext` |
| Low-confidence specialty | Job pauses; pick the specialty yourself |
| Write outside the trusted root | **Denied outright** — never approvable |

All agent filesystem writes go through LangChain's `FileManagementToolkit` scoped to the job
workspace, plus a path-traversal and symlink-escape validator. Every proposal, decision and
denial is appended to `logs/audit.jsonl` and the Postgres audit table.

ZIP handling refuses zip-slip, enforces an uncompressed byte budget, an entry cap and a
nesting-depth cap. Archives are expanded recursively and flattened in the output tree, with
the original internal path preserved as `manifest.source_path`.

## API (v1)

```
POST /api/v1/jobs                            multipart upload; honours Idempotency-Key
GET  /api/v1/jobs?status=&limit=&cursor=     cursor-paginated
GET  /api/v1/jobs/{id}                       status + stage + pending approvals
GET  /api/v1/jobs/{id}/events                SSE live progress
GET  /api/v1/jobs/{id}/files                 all labelled files
GET  /api/v1/jobs/{id}/files/{file_id}       codes + evidence + specialty + parse trail
GET  /api/v1/jobs/{id}/approvals
POST /api/v1/jobs/{id}/approvals/{aid}       {decision, note?, specialty?} → resumes the graph
POST /api/v1/jobs/{id}/cancel
GET  /api/v1/jobs/{id}/tree | /download | /manifest.csv
GET  /api/v1/codes/lookup?code=              dictionary lookup
GET  /api/v1/specialties                     the closed specialty list
GET  /api/v1/health  /readyz  /version  /metrics
```

Open access with a shared rate limit, RFC 7807 `application/problem+json` errors,
`X-Request-ID` correlation, structured JSON logs, Prometheus metrics, and an isolated
`/api/v1` router so a future v2 can co-exist.

## Development

```bash
cd backend && python3.12 -m venv .venv && .venv/bin/pip install -e ".[dev]"
make test          # backend pytest + frontend vitest
make fmt           # ruff
```

Tests never touch OpenAI, LlamaParse or the NPI Registry — those are mocked with `respx`.

## Configuration

See `.env.example`. Secrets are read only from the environment; nothing is hardcoded.

| Variable | Meaning |
|---|---|
| `OPENAI_API_KEY` / `OPENAI_MINI_MODEL_ID` | Classification model (default `gpt-5.4-mini`) |
| `LLAMA_CLOUD_API_KEY` | LlamaParse fallback; without it the chain skips to OCR |
| `CODE_EVIDENCE_THRESHOLD` | Score a note must clear to be `with-codes` |
| `SPECIALTY_CONFIDENCE_THRESHOLD` | Below this, a human is asked |
| `LLM_BATCH_MIN_FILES` | Job size at which the Batch API takes over from sync |
| `WORKSPACE_ROOT` | The trusted root; the agent cannot write outside it |

## Not in v1

Splitting one file containing several distinct notes; PHI de-identification; user accounts
and RBAC (API-key auth only).
