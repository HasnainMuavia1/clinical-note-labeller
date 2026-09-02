# Build context is the repository root so the code dictionaries can be baked in,
# which lets a deployed stack run with no bind mounts at all.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update && apt-get install -y --no-install-recommends \
      build-essential curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /srv

# Dependencies are their own layer: application edits must not trigger a reinstall.
COPY backend/pyproject.toml ./
RUN python -c "import tomllib, pathlib; \
      deps = tomllib.load(open('pyproject.toml', 'rb'))['project']['dependencies']; \
      pathlib.Path('requirements.txt').write_text('\n'.join(deps))" \
    && pip install --no-cache-dir -r requirements.txt

# CPT/HCPCS/ICD-10 dictionaries. Baked in so a deployment needs no mounted volume;
# a bind mount over /data/reference still wins if you want to update them in place.
COPY cpt-codes /data/reference/cpt-codes
COPY ict-10-codes /data/reference/ict-10-codes

COPY backend/app ./app
COPY backend/alembic.ini ./
COPY backend/alembic ./alembic

RUN useradd --create-home --uid 10002 appuser \
    && mkdir -p /data/workspace \
    && chown -R appuser /srv /data
USER appuser

EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=5s --start-period=30s --retries=5 \
  CMD curl -fsS http://localhost:8000/api/v1/health || exit 1
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
