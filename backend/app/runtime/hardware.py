from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class GpuDevice:
    index: int
    name: str
    backend: str


@dataclass(frozen=True)
class HardwareProfile:
    cpu_count: int
    memory_bytes: int | None
    gpus: tuple[GpuDevice, ...]

    @property
    def gpu_count(self) -> int:
        return len(self.gpus)


def _run(cmd: list[str], timeout: float = 2.0) -> str | None:
    if not cmd or not shutil.which(cmd[0]):
        return None
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout


def _cpu_count() -> int:
    try:
        affinity = os.sched_getaffinity(0)
        if affinity:
            return max(1, len(affinity))
    except (AttributeError, OSError):
        pass
    return max(1, os.cpu_count() or 2)


def _read_int_file(path: Path) -> int | None:
    try:
        raw = path.read_text().strip()
    except OSError:
        return None
    if raw in {"", "max"}:
        return None
    if raw.isdigit():
        value = int(raw)
        # Some cgroup v1 hosts advertise "unlimited" as a huge sentinel.
        if value >= 1 << 60:
            return None
        return value
    return None


def _memory_bytes() -> int | None:
    for candidate in (
        Path("/sys/fs/cgroup/memory.max"),
        Path("/sys/fs/cgroup/memory/memory.limit_in_bytes"),
    ):
        value = _read_int_file(candidate)
        if value:
            return value
    meminfo = Path("/proc/meminfo")
    if meminfo.is_file():
        try:
            for line in meminfo.read_text().splitlines():
                if line.startswith("MemAvailable:") or line.startswith("MemTotal:"):
                    return int(line.split()[1]) * 1024
        except (OSError, ValueError, IndexError):
            return None
    return None


def _nvidia_device_nodes() -> list[Path]:
    return sorted(Path("/dev").glob("nvidia[0-9]*"))


def _apple_metal_available() -> bool:
    if sys.platform != "darwin":
        return False
    out = _run(["system_profiler", "SPDisplaysDataType"], timeout=4.0)
    if not out:
        return False
    return "Metal" in out or "Chipset Model" in out


def _visible_device_count(*env_names: str) -> int | None:
    for name in env_names:
        raw = os.environ.get(name, "").strip()
        if not raw or raw.lower() in {"void", "none"}:
            continue
        if raw.lower() == "all":
            return None
        parts = [p for p in raw.split(",") if p.strip() != ""]
        if parts:
            return len(parts)
    return None


def detect_gpus() -> tuple[GpuDevice, ...]:
    explicit = os.environ.get("GPU_COUNT", "").strip()
    if explicit.isdigit() and int(explicit) > 0:
        count = int(explicit)
        return tuple(GpuDevice(i, f"gpu-{i}", "cuda") for i in range(count))

    smi = _run(["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"])
    if smi:
        names = [line.strip() for line in smi.splitlines() if line.strip()]
        visible = _visible_device_count("CUDA_VISIBLE_DEVICES", "NVIDIA_VISIBLE_DEVICES")
        if visible is not None:
            names = names[:visible]
        return tuple(GpuDevice(i, name, "cuda") for i, name in enumerate(names))

    visible = _visible_device_count("CUDA_VISIBLE_DEVICES", "NVIDIA_VISIBLE_DEVICES")
    if visible:
        return tuple(GpuDevice(i, f"cuda:{i}", "cuda") for i in range(visible))

    nodes = _nvidia_device_nodes()
    if nodes:
        return tuple(GpuDevice(i, node.name, "cuda") for i, node in enumerate(nodes))

    if _apple_metal_available():
        return (GpuDevice(0, "Apple Metal", "mps"),)

    return ()


def probe_hardware() -> HardwareProfile:
    profile = HardwareProfile(
        cpu_count=_cpu_count(),
        memory_bytes=_memory_bytes(),
        gpus=detect_gpus(),
    )
    log.info(
        "hardware probe: cpus=%s memory=%s gpus=%s",
        profile.cpu_count,
        profile.memory_bytes,
        [f"{g.backend}:{g.name}" for g in profile.gpus],
    )
    return profile
