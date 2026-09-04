#!/usr/bin/env python3
"""Start the stack and attach GPUs automatically when NVIDIA is on the host."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.runtime.compose import compose_argv  # noqa: E402


def _run(files: list[str], extra: list[str]) -> int:
    cmd = ["docker", "compose", *files, *extra]
    print(" ".join(cmd), file=sys.stderr)
    return subprocess.call(cmd, cwd=ROOT)


def main(argv: list[str] | None = None) -> int:
    extra = list(argv if argv is not None else sys.argv[1:]) or ["up", "--build", "-d"]
    gpu = compose_argv(ROOT)
    cpu = compose_argv(ROOT, want_gpu=False)
    if gpu != cpu:
        print("NVIDIA GPU detected; attaching it to the worker and parser.", file=sys.stderr)
        rc = _run(gpu, extra)
        if rc == 0:
            return rc
        print("GPU attach failed; starting CPU-only.", file=sys.stderr)
    else:
        print("No NVIDIA GPU on this host; starting CPU-only.", file=sys.stderr)
    return _run(cpu, extra)


if __name__ == "__main__":
    raise SystemExit(main())
