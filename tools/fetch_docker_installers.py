#!/usr/bin/env python3
"""Download official Docker Desktop installers into installer/docker/."""
from __future__ import annotations

import pathlib
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent
DEST = ROOT / "installer" / "docker"

FILES = [
    ("DockerDesktopInstaller.exe",
     "https://desktop.docker.com/win/main/amd64/Docker%20Desktop%20Installer.exe"),
    ("Docker-arm64.dmg",
     "https://desktop.docker.com/mac/main/arm64/Docker.dmg"),
    ("Docker-amd64.dmg",
     "https://desktop.docker.com/mac/main/amd64/Docker.dmg"),
]


def _download(url: str, dest: pathlib.Path) -> None:
    print(f"downloading {dest.name} …")
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    req = urllib.request.Request(url, headers={"User-Agent": "clinical-note-labeller"})
    with urllib.request.urlopen(req, timeout=120) as resp, tmp.open("wb") as fh:
        while True:
            chunk = resp.read(1 << 20)
            if not chunk:
                break
            fh.write(chunk)
    tmp.replace(dest)
    print(f"  {dest.name}  {dest.stat().st_size:,} bytes")


def main() -> None:
    DEST.mkdir(parents=True, exist_ok=True)
    for name, url in FILES:
        _download(url, DEST / name)
    print(f"\nSaved under {DEST.relative_to(ROOT)}/")


if __name__ == "__main__":
    main()
