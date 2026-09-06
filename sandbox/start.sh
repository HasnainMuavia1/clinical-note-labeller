#!/bin/sh
set -eu
export OMP_NUM_THREADS="${OMP_NUM_THREADS:-1}"
export OPENBLAS_NUM_THREADS="${OPENBLAS_NUM_THREADS:-1}"
export MKL_NUM_THREADS="${MKL_NUM_THREADS:-1}"
W="${SANDBOX_WORKERS:-1}"
if [ "$W" = "0" ]; then
  W="$(nproc 2>/dev/null || echo 1)"
fi
exec uvicorn app:app --host 0.0.0.0 --port 8081 --workers "$W"
