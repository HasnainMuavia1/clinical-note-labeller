# Build context is the repository root so the code dictionaries can be baked in,
# which lets a deployed stack run with no bind mounts at all.

# ---- builder: compilers live here and never reach the runtime image ----------
FROM python:3.12-slim AS builder

ENV PIP_NO_CACHE_DIR=1
RUN apt-get update && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build
COPY backend/pyproject.toml ./
RUN python -c "import tomllib, pathlib; \
      deps = tomllib.load(open('pyproject.toml', 'rb'))['project']['dependencies']; \
      pathlib.Path('requirements.txt').write_text('\n'.join(deps))" \
    && python -m venv /opt/venv \
    && /opt/venv/bin/pip install --no-cache-dir -r requirements.txt

# ---- runtime -----------------------------------------------------------------
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/opt/venv/bin:$PATH"

RUN apt-get update && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 10002 appuser \
    && mkdir -p /data/workspace && chown appuser /data/workspace

COPY --from=builder --chown=appuser /opt/venv /opt/venv

WORKDIR /srv

# CPT/HCPCS/ICD-10 dictionaries. Baked in so a deployment needs no mounted volume;
# a bind mount over /data/reference still wins if you want to update them in place.
# --chown avoids a second full-size layer from a later recursive chown.
COPY --chown=appuser cpt-codes /data/reference/cpt-codes
COPY --chown=appuser ict-10-codes /data/reference/ict-10-codes

COPY --chown=appuser backend/app ./app
COPY --chown=appuser backend/alembic.ini ./
COPY --chown=appuser backend/alembic ./alembic

USER appuser

EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=5s --start-period=30s --retries=5 \
  CMD curl -fsS http://localhost:8000/api/v1/health || exit 1
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
