from __future__ import annotations

import logging
from dataclasses import dataclass
from functools import lru_cache

from .hardware import HardwareProfile, probe_hardware

log = logging.getLogger(__name__)

FILE_MIN, FILE_MAX = 4, 64
CELERY_MIN, CELERY_MAX = 1, 16
BYTES_PER_FILE_WORKER = 256 * 1024 * 1024


@dataclass(frozen=True)
class CapacityOverrides:
    file_concurrency: int = 0
    celery_concurrency: int = 0


@dataclass(frozen=True)
class CapacityPlan:
    file_concurrency: int
    celery_concurrency: int
    parse_concurrency: int
    parse_workers: int
    parse_threads: int
    detect_concurrency: int
    llm_sync_concurrency: int
    gpu_batch_size: int
    ocr_workers: int
    ocr_inflight: int
    ocr_page_workers: int
    source: str
    hardware: HardwareProfile


def _clamp(value: int, lo: int, hi: int) -> int:
    return max(lo, min(hi, value))


def plan_capacity(hardware: HardwareProfile,
                  overrides: CapacityOverrides | None = None) -> CapacityPlan:
    overrides = overrides or CapacityOverrides()
    cpus = max(1, hardware.cpu_count)
    gpu_n = hardware.gpu_count
    cuda_n = sum(1 for gpu in hardware.gpus if gpu.backend == "cuda")

    mem_cap = FILE_MAX
    if hardware.memory_bytes:
        mem_cap = max(FILE_MIN, hardware.memory_bytes // BYTES_PER_FILE_WORKER)

    file_auto = min(FILE_MAX, mem_cap, max(FILE_MIN, cpus * 2))
    if cuda_n:
        file_auto = min(FILE_MAX, mem_cap, max(file_auto, cpus * 4, cuda_n * 16))

    celery_auto = _clamp(max(CELERY_MIN, cpus // 2), CELERY_MIN, CELERY_MAX)
    if cuda_n:
        celery_auto = _clamp(max(celery_auto, cuda_n * 2), CELERY_MIN, CELERY_MAX)

    if cpus <= 2:
        celery_auto = 1
        file_auto = min(file_auto, 8)

    file_override = overrides.file_concurrency
    celery_override = overrides.celery_concurrency
    source = "env" if file_override > 0 or celery_override > 0 else "auto"

    file_c = file_override if file_override > 0 else file_auto
    celery_c = celery_override if celery_override > 0 else celery_auto
    file_c = _clamp(file_c, 1, FILE_MAX)
    celery_c = _clamp(celery_c, 1, CELERY_MAX)

    gpu_batch = 1
    if cuda_n:
        gpu_batch = min(64, max(16, cuda_n * 16))

    detect_c = min(file_c, max(2, cpus))
    parse_workers = _clamp(max(1, cpus // 3), 1, 4)
    parse_threads = 4
    if cuda_n:
        parse_workers = _clamp(max(2, cuda_n * 2, cpus // 3), 1, 8)
        parse_threads = _clamp(max(4, cuda_n * 4), 4, 8)
    elif cpus <= 2:
        parse_workers = 1
        parse_threads = min(4, file_auto)
    parse_c = max(file_c, gpu_batch, parse_workers * parse_threads)
    llm_sync_c = min(16, max(4, file_c))
    ocr_workers = min(8, max(1, cpus if not cuda_n else max(cpus, cuda_n * 8)))
    if cuda_n:
        ocr_page_workers = min(8, max(4, cuda_n * 4))
        ocr_inflight = min(32, max(8, cuda_n * 8, cpus))
    else:
        ocr_page_workers = min(4, max(2, cpus // 4 or 2))
        # Fewer parallel scans on CPU so a 20-page packet can finish before timeout.
        ocr_inflight = min(4, max(2, cpus // 3))

    return CapacityPlan(
        file_concurrency=file_c,
        celery_concurrency=celery_c,
        parse_concurrency=parse_c,
        parse_workers=parse_workers,
        parse_threads=parse_threads,
        detect_concurrency=detect_c,
        llm_sync_concurrency=llm_sync_c,
        gpu_batch_size=gpu_batch,
        ocr_workers=ocr_workers,
        ocr_inflight=ocr_inflight,
        ocr_page_workers=ocr_page_workers,
        source=source,
        hardware=hardware,
    )


def _overrides_from_env() -> CapacityOverrides:
    from ..config import get_settings

    settings = get_settings()
    return CapacityOverrides(
        file_concurrency=int(settings.file_concurrency or 0),
        celery_concurrency=int(getattr(settings, "celery_concurrency", 0) or 0),
    )


@lru_cache
def resolve_capacity() -> CapacityPlan:
    plan = plan_capacity(probe_hardware(), _overrides_from_env())
    log.info(
        "capacity plan source=%s file=%s celery=%s detect=%s llm_sync=%s gpu_batch=%s ocr=%s inflight=%s pages=%s",
        plan.source, plan.file_concurrency, plan.celery_concurrency,
        plan.detect_concurrency, plan.llm_sync_concurrency,
        plan.gpu_batch_size, plan.ocr_workers, plan.ocr_inflight, plan.ocr_page_workers,
    )
    return plan


def apply_celery_concurrency(app) -> int:
    planned = resolve_capacity().celery_concurrency
    app.conf.worker_concurrency = planned
    return planned
