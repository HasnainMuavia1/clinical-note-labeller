from __future__ import annotations

from pathlib import Path

from .hardware import _nvidia_device_nodes, _run


def host_can_pass_nvidia() -> bool:
    """True when this host can attach an NVIDIA GPU into a Linux container."""
    smi = _run(["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"])
    if smi and smi.strip():
        return True
    return bool(_nvidia_device_nodes())


def compose_argv(root: Path, *, want_gpu: bool | None = None) -> list[str]:
    """Compose `-f` flags: GPU overlay is added only when NVIDIA is on the host."""
    root = Path(root)
    base = root / "docker-compose.yml"
    overlay = root / "docker-compose.gpu.yml"
    args = ["-f", str(base)]
    use_gpu = host_can_pass_nvidia() if want_gpu is None else want_gpu
    if use_gpu and overlay.is_file():
        args.extend(["-f", str(overlay)])
    return args
