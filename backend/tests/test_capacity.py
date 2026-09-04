"""Hardware-aware worker sizing: CPU scale-up, GPU batch boost, env overrides."""
from types import SimpleNamespace

from app.runtime.capacity import CapacityOverrides, plan_capacity
from app.runtime.hardware import GpuDevice, HardwareProfile, detect_gpus, probe_hardware


def test_small_cpu_box_stays_conservative():
    plan = plan_capacity(HardwareProfile(cpu_count=2, memory_bytes=2 * 1024**3, gpus=()))
    assert plan.celery_concurrency == 1
    assert 4 <= plan.file_concurrency <= 8
    assert plan.detect_concurrency <= plan.file_concurrency
    assert plan.gpu_batch_size == 1
    assert plan.source == "auto"


def test_high_cpu_box_raises_file_and_celery_workers():
    plan = plan_capacity(HardwareProfile(cpu_count=16, memory_bytes=32 * 1024**3, gpus=()))
    assert plan.file_concurrency >= 16
    assert plan.celery_concurrency >= 4
    assert plan.file_concurrency <= 64
    assert plan.celery_concurrency <= 16
    assert plan.llm_sync_concurrency >= 8


def test_gpu_raises_batch_size_and_parse_concurrency():
    gpus = (GpuDevice(index=0, name="NVIDIA A10", backend="cuda"),
            GpuDevice(index=1, name="NVIDIA A10", backend="cuda"))
    cpu = plan_capacity(HardwareProfile(cpu_count=16, memory_bytes=64 * 1024**3, gpus=()))
    gpu = plan_capacity(HardwareProfile(cpu_count=16, memory_bytes=64 * 1024**3, gpus=gpus))
    assert gpu.gpu_batch_size >= 8
    assert gpu.gpu_batch_size > cpu.gpu_batch_size
    assert gpu.file_concurrency >= cpu.file_concurrency
    assert gpu.parse_concurrency >= gpu.file_concurrency
    assert gpu.ocr_workers >= 4
    assert gpu.parse_concurrency >= gpu.gpu_batch_size


def test_memory_ceiling_caps_file_workers():
    plan = plan_capacity(HardwareProfile(cpu_count=64, memory_bytes=512 * 1024**2, gpus=()))
    assert plan.file_concurrency <= 8


def test_explicit_overrides_win_over_auto():
    hw = HardwareProfile(cpu_count=16, memory_bytes=32 * 1024**3, gpus=())
    plan = plan_capacity(hw, CapacityOverrides(file_concurrency=5, celery_concurrency=3))
    assert plan.file_concurrency == 5
    assert plan.celery_concurrency == 3
    assert plan.source == "env"


def test_zero_or_negative_overrides_mean_auto():
    hw = HardwareProfile(cpu_count=8, memory_bytes=16 * 1024**3, gpus=())
    plan = plan_capacity(hw, CapacityOverrides(file_concurrency=0, celery_concurrency=-1))
    assert plan.source == "auto"
    assert plan.file_concurrency >= 4
    assert plan.celery_concurrency >= 2


def test_detect_gpus_reads_nvidia_smi(monkeypatch):
    monkeypatch.setattr(
        "app.runtime.hardware._run",
        lambda cmd, timeout=2.0: "Tesla T4\nA100-SXM4-40GB\n" if cmd[:1] == ["nvidia-smi"] else None,
    )
    monkeypatch.delenv("CUDA_VISIBLE_DEVICES", raising=False)
    monkeypatch.delenv("NVIDIA_VISIBLE_DEVICES", raising=False)
    monkeypatch.delenv("GPU_COUNT", raising=False)
    gpus = detect_gpus()
    assert [g.name for g in gpus] == ["Tesla T4", "A100-SXM4-40GB"]
    assert all(g.backend == "cuda" for g in gpus)


def test_detect_gpus_respects_cuda_visible_devices(monkeypatch):
    monkeypatch.setenv("CUDA_VISIBLE_DEVICES", "0,1")
    monkeypatch.delenv("GPU_COUNT", raising=False)
    monkeypatch.setattr("app.runtime.hardware._run", lambda *_args, **_kwargs: None)
    monkeypatch.setattr("app.runtime.hardware._nvidia_device_nodes", lambda: [])
    gpus = detect_gpus()
    assert len(gpus) == 2
    assert gpus[0].backend == "cuda"


def test_detect_gpus_empty_when_nothing_is_present(monkeypatch):
    monkeypatch.delenv("CUDA_VISIBLE_DEVICES", raising=False)
    monkeypatch.delenv("NVIDIA_VISIBLE_DEVICES", raising=False)
    monkeypatch.delenv("GPU_COUNT", raising=False)
    monkeypatch.setattr("app.runtime.hardware._run", lambda *_args, **_kwargs: None)
    monkeypatch.setattr("app.runtime.hardware._nvidia_device_nodes", lambda: [])
    monkeypatch.setattr("app.runtime.hardware._apple_metal_available", lambda: False)
    assert detect_gpus() == ()


def test_probe_hardware_uses_affinity_and_cgroup_memory(monkeypatch):
    monkeypatch.setattr("app.runtime.hardware._cpu_count", lambda: 12)
    monkeypatch.setattr("app.runtime.hardware._memory_bytes", lambda: 8 * 1024**3)
    monkeypatch.setattr("app.runtime.hardware.detect_gpus", lambda: ())
    profile = probe_hardware()
    assert profile.cpu_count == 12
    assert profile.memory_bytes == 8 * 1024**3
    assert profile.gpus == ()


def test_resolve_capacity_is_cached_until_cleared(monkeypatch):
    from app.runtime import capacity as cap

    cap.resolve_capacity.cache_clear()
    monkeypatch.setattr(cap, "probe_hardware", lambda: HardwareProfile(2, 2 * 1024**3, ()))
    monkeypatch.setattr(cap, "_overrides_from_env", lambda: CapacityOverrides())
    first = cap.resolve_capacity()
    monkeypatch.setattr(cap, "probe_hardware", lambda: HardwareProfile(64, 64 * 1024**3, ()))
    assert cap.resolve_capacity() is first
    cap.resolve_capacity.cache_clear()
    second = cap.resolve_capacity()
    assert second.file_concurrency != first.file_concurrency


def test_compose_files_add_gpu_overlay_only_when_nvidia_is_on_the_host(tmp_path, monkeypatch):
    from app.runtime.compose import compose_argv

    base = tmp_path / "docker-compose.yml"
    overlay = tmp_path / "docker-compose.gpu.yml"
    base.write_text("name: x\n")
    overlay.write_text("name: x\n")
    monkeypatch.setattr("app.runtime.compose.host_can_pass_nvidia", lambda: True)
    assert compose_argv(tmp_path) == ["-f", str(base), "-f", str(overlay)]
    monkeypatch.setattr("app.runtime.compose.host_can_pass_nvidia", lambda: False)
    assert compose_argv(tmp_path) == ["-f", str(base)]


def test_compose_files_stay_cpu_only_if_overlay_is_missing(tmp_path, monkeypatch):
    from app.runtime.compose import compose_argv

    (tmp_path / "docker-compose.yml").write_text("name: x\n")
    monkeypatch.setattr("app.runtime.compose.host_can_pass_nvidia", lambda: True)
    assert compose_argv(tmp_path) == ["-f", str(tmp_path / "docker-compose.yml")]


def test_celery_picks_up_planned_concurrency(monkeypatch):
    from app.runtime.capacity import apply_celery_concurrency

    app = SimpleNamespace(conf=SimpleNamespace(worker_concurrency=None))
    monkeypatch.setattr(
        "app.runtime.capacity.resolve_capacity",
        lambda: SimpleNamespace(celery_concurrency=7),
    )
    apply_celery_concurrency(app)
    assert app.conf.worker_concurrency == 7
