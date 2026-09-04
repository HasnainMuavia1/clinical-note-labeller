# Clinical Note Labeller

Upload clinical notes (PDF, DOCX, text, or a ZIP of those). The app parses each
file, looks for billing/clinical codes, assigns a specialty, and files a **copy**
into a fixed folder tree. Originals in `input/` are never changed or deleted.

```
output/
  with-codes/<Specialty>/<file>
  without-codes/<Specialty>/<file>
  unparsed/<file>
  labels.csv
  manifest.jsonl
```

A note is `with-codes` only when it has a validated ICD-10, CPT, HCPCS, or
attached modifier. An NPI is a provider id — it does not count as a code.

## Requirements

- [Docker Desktop](https://www.docker.com/products/docker-desktop/) (or Docker Engine + Compose)
- An [OpenAI](https://platform.openai.com/) API key (specialty classification)
- Optional: a [Llama Cloud](https://cloud.llamaindex.ai/) key (LlamaParse fallback for hard PDFs)

## Run it

```bash
cp .env.example .env          # then set OPENAI_API_KEY (and LLAMA_CLOUD_API_KEY if you have one)
make up                       # docker compose up --build -d
```

| Service | URL |
|---|---|
| UI | http://localhost:5173 |
| API | http://localhost:8000/api/v1 |
| OpenAPI | http://localhost:8000/docs |
| MinIO console | http://localhost:9101 |

The UI is open — no login. Stop with `make down`. Wipe volumes with `make clean`.

```bash
curl -s -F "files=@notes.zip" http://localhost:8000/api/v1/jobs
```

## Using the app

1. Drop a file or ZIP on **Upload**.
2. Watch live stage + file progress (`N of M files`). Leaving the page does not
   cancel the job; reopen it from **Jobs** or the last-run link.
3. If confidence is low, or a write would overwrite/delete, the job pauses for
   approval in the same view.
4. When the job completes, labelled copies are already on disk (see below).
   **Download** is only a convenience zip of that same `output/` tree.

Restarting the **worker** container kills an in-flight job. Rebuild the API
alone if you need a backend fix without dropping the worker.

## Where files are saved

Jobs write to a `workspace/` folder **on the host**, bind-mounted into the
containers as `/data/workspace`.

- Local `make up`: `workspace/` next to `docker-compose.yml` (this repo)
- Client installer: `ClinicalNoteLabeller/workspace/` next to the `.bat` / `.exe` / `.command`

```
workspace/<upload-name>/          # ECW_zip.zip → workspace/ECW_zip/
  input/                          original upload, immutable
  extracted/                      unpacked ZIP contents
  output/                         labelled copies (the result)
  logs/audit.jsonl
```

When a job finishes you get both:

- `workspace/<upload-name>/output/` — the labelled folder tree
- `workspace/<upload-name>-output.zip` — the same tree as a zip

Download in the UI serves that zip.

## How a job runs

LangGraph `StateGraph`, checkpointed in Postgres (thread id = job id). A job
survives API restarts and can sit for hours on an OpenAI batch or a human
approval:

```
intake → unpack → parse → detect_codes → resolve_npi → classify
       → plan_placement → [approval_gate] → execute_ops → manifest
```

Parse, code detection, NPI lookup, and sync classification run several files
at once. Worker counts are sized from the machine at startup (CPU, memory,
GPU). Set `FILE_CONCURRENCY` / `CELERY_CONCURRENCY` to pin them. LlamaParse
uploads stay inside the official paid-plan window (50 / 10s).

See **Capacity** below.

### Parsing

On CPU: `pypdf` / `python-docx` / text → **LlamaParse** (failures only) →
**Tesseract OCR** → `output/unparsed/`.

On a CUDA GPU: **Tesseract first** (PDFs and images only), then pypdf, then
LlamaParse. Tesseract itself is CPU/LSTM (`--oem 1`); the GPU speeds the job
by running more page OCR and more files at once, not by replacing Tesseract
with a CUDA model. Each hop is stored as `parse_trail`.

LlamaParse is called over REST from the worker, not via the full `llama-index`
SDK. Local parse + OCR run in `parser-sandbox`: internal Docker network, no
internet, no API keys, read-only root, dropped capabilities.

OCR is **Tesseract** (`tesseract-ocr` in the sandbox image). It is the last
hop: digital text (`pypdf` / `python-docx` / charset text) → LlamaParse →
Tesseract. `GET /api/v1/capacity` reports `ocr_engine: tesseract`.

### Code detection

Raw regex on clinical text is noisy (`90210` looks like CPT; `04/25` looks like
a modifier), so detection is two stages:

1. **Candidates** — ICD-10, CPT, HCPCS, hyphen-attached modifiers, Luhn-valid NPI.
2. **Evidence** — a candidate counts when it is in the dictionary, or structurally
   valid with a nearby cue (`CPT:`, `Diagnosis Code`, claim language). ZIP/phone/
   date/vital/MRN context rejects it.

Accepted and rejected candidates both land in the manifest with offset, rule, and
dictionary-hit flag.

| Source | Content | Codes |
|---|---|---|
| `cpt-codes/cpt.xlsx` | 2026 PFS RVU — numeric CPT + HCPCS | 16,978 |
| `cpt-codes/cpt-codes.txt` | HCPCS Level II | 9,229 distinct |
| `ict-10-codes/icd10cm_order_2026.txt` | ICD-10-CM 2026 order file | 98,186 |

Full CPT is AMA-licensed and is not redistributed, so Category III codes (e.g.
`0042T`) are usually missing. The cue rule covers those holes.

### Specialty

1. **NPI (no LLM).** Luhn-valid NPI → NPPES Registry → primary NUCC taxonomy.
   When several NPIs appear, the individual clinician wins over an organisation.
2. **LLM.** OpenAI Batch API (50% cheaper) with structured output on the closed
   specialty list. Jobs smaller than `LLM_BATCH_MIN_FILES` use the sync endpoint.
3. **Low confidence** asks a human. Unresolved notes go to `Unclassified/`.

`manifest.method` is `npi | llm_batch | llm_sync | human` per file.

## Guardrails

| Action | What happens |
|---|---|
| Delete a file | Job pauses for approval |
| Overwrite an existing file | Job pauses; reject → `name__2.ext` |
| Low-confidence specialty | Job pauses; pick the specialty |
| Write outside the workspace | Denied — never approvable |

Writes go through LangChain `FileManagementToolkit` scoped to the job folder,
plus a path-traversal / symlink check. ZIP handling refuses zip-slip and caps
uncompressed size, entry count, and nesting depth.

## Capacity

The worker sizes itself from the box it is running on. Nothing is pinned to
“12 files / 4 Celery processes” unless you set those env vars.

| Signal | What we do |
|---|---|
| CPU count (cgroup affinity) | More file workers and Celery processes |
| Available memory | Cap file workers at ~256 MB each so a large ZIP cannot OOM |
| NVIDIA GPU (`nvidia-smi`, `/dev/nvidia*`, or `CUDA_VISIBLE_DEVICES`) | Larger parse/OCR batches and a higher file fan-out |
| Apple Metal (macOS host, not Docker) | Same GPU boost via the `mps` backend |

`GET /api/v1/capacity` shows the live plan (`cpu_count`, `gpu_count`,
`file_concurrency`, `celery_concurrency`, `gpu_batch_size`, `ocr_engine`).

`make up` probes the host. If `nvidia-smi` (or `/dev/nvidia*`) is present it
attaches the GPU overlay by itself; if attach fails it starts CPU-only. You
do not pass compose files by hand. Classification still goes to OpenAI. A GPU does not run Tesseract on CUDA —
it raises page-OCR workers and file fan-out, and puts Tesseract first on
scanned PDFs.

## Configuration

Copy `.env.example` → `.env`. Secrets come only from the environment.

| Variable | Meaning |
|---|---|
| `OPENAI_API_KEY` / `OPENAI_MINI_MODEL_ID` | Classification model (default `gpt-5.4-mini`) |
| `LLAMA_CLOUD_API_KEY` | LlamaParse fallback; without it the chain skips to OCR |
| `LLAMA_PARSE_TIER` | `standard` (paid, 50 uploads / 10s) or `free` (20 / min) |
| `FILE_CONCURRENCY` | Parallel files in parse / detect / NPI. `0` = auto from CPU/GPU |
| `CELERY_CONCURRENCY` | Parallel jobs in the Celery worker. `0` = auto |
| `OCR_WORKERS` | Parallel Tesseract pages in the sandbox. `0` = auto |
| `CODE_EVIDENCE_THRESHOLD` | Score a note must clear to be `with-codes` |
| `SPECIALTY_CONFIDENCE_THRESHOLD` | Below this, a human is asked (default `0.65`) |
| `LLM_BATCH_MIN_FILES` | Job size that switches sync → Batch API (default `10`) |
| `WORKSPACE_ROOT` | Trusted root; the agent cannot write outside it |

## API

```
POST /api/v1/jobs                         multipart upload; Idempotency-Key supported
GET  /api/v1/jobs?status=&limit=&cursor=
GET  /api/v1/jobs/{id}                    status, stage, files_done / files_total
GET  /api/v1/jobs/{id}/events             SSE progress
GET  /api/v1/jobs/{id}/files
GET  /api/v1/jobs/{id}/files/{file_id}    codes, evidence, specialty, parse trail
GET  /api/v1/jobs/{id}/approvals
POST /api/v1/jobs/{id}/approvals/{aid}    {decision, note?, specialty?}
POST /api/v1/jobs/{id}/cancel
GET  /api/v1/jobs/{id}/tree | /download | /manifest.csv
GET  /api/v1/codes/lookup?code=
GET  /api/v1/specialties
GET  /api/v1/health  /readyz  /version  /capacity  /metrics
```

Open access with a write-side rate limit, RFC 7807 errors, `X-Request-ID`,
JSON logs, and Prometheus metrics.

## Client installer

A client needs one file and no repo. Build it with:

```bash
python3 tools/make_installer.py
```

| Client | File | Output appears in |
|---|---|---|
| Windows | `dist/Clinical-Note-Labeller-Setup.exe` | `ClinicalNoteLabeller\workspace` next to the exe |
| macOS / Linux | `dist/Clinical-Note-Labeller-Setup.command` | `ClinicalNoteLabeller/workspace` next to the script |

The installer installs Docker if needed, pulls published images, starts the
stack, and opens the browser. Images are amd64 + arm64.

On macOS, first launch is blocked by Gatekeeper: **right-click → Open → Open**.

The generated file embeds live API keys. Read [PUBLISHING.md](PUBLISHING.md)
before handing it out. `dist/` is gitignored — never commit it.

## Development

```bash
cd backend && python3.12 -m venv .venv && .venv/bin/pip install -e ".[dev]"
cd ../frontend && npm install
make test          # backend pytest + frontend vitest
make fmt           # ruff
```

Tests mock OpenAI, LlamaParse, and the NPI Registry (`respx`). They never call
those services.

```
backend/     FastAPI + Celery worker + LangGraph
frontend/    React + Vite, served by nginx in Docker
sandbox/     offline PDF/DOCX/OCR parser
cpt-codes/   CPT + HCPCS dictionaries (mounted / baked into the image)
ict-10-codes/ ICD-10-CM 2026 order file
```

## Not in v1

Splitting one file that contains several distinct notes; PHI de-identification;
user accounts and RBAC.
