#!/bin/sh
set -eu
W="${SANDBOX_WORKERS:-2}"
if [ "$W" = "0" ]; then
  W="$(nproc 2>/dev/null || echo 2)"
fi
exec uvicorn app:app --host 0.0.0.0 --port 8081 --workers "$W"
