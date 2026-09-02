#!/usr/bin/env python3
"""Generate the client installers by filling the templates with real credentials.

The templates in installer/ are safe to commit; the generated files are not,
because they carry live API keys. They are written to dist/, which is gitignored.

    python3 tools/make_installer.py [--env .env] [--tag latest]
"""
from __future__ import annotations

import argparse
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import zipfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
# (template, output, line-ending, executable)
TARGETS = [
    (ROOT / "installer" / "Install-ClinicalNoteLabeller.bat",
     ROOT / "dist" / "Clinical-Note-Labeller-Setup.bat", "\r\n", False),
    (ROOT / "installer" / "install-clinical-note-labeller.command",
     ROOT / "dist" / "Clinical-Note-Labeller-Setup.command", "\n", True),
]

FIELDS = {
    "@@OPENAI_API_KEY@@": ("OPENAI_API_KEY", ""),
    "@@OPENAI_MINI_MODEL_ID@@": ("OPENAI_MINI_MODEL_ID", "gpt-5.4-mini"),
    "@@LLAMA_CLOUD_API_KEY@@": ("LLAMA_CLOUD_API_KEY", ""),
    "@@APP_API_KEY@@": ("API_KEYS", "dev-key"),
}


def read_env(path: pathlib.Path) -> dict[str, str]:
    if not path.is_file():
        sys.exit(f"{path} not found. Copy .env.example to .env and fill it in first.")
    values: dict[str, str] = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip()
    return values


def _copy_bundled_docker() -> None:
    """Copy official Docker installers next to the generated setup files."""
    src = ROOT / "installer" / "docker"
    dest = ROOT / "dist" / "docker"
    if not src.is_dir():
        print("  (no installer/docker/ bundle — clients will download Docker)")
        return
    dest.mkdir(parents=True, exist_ok=True)
    copied = 0
    for path in src.iterdir():
        if path.suffix.lower() not in {".exe", ".dmg"}:
            continue
        target = dest / path.name
        shutil.copy2(path, target)
        copied += 1
        print(f"wrote {target.relative_to(ROOT)}  ({target.stat().st_size:,} bytes)")
    if copied:
        print("  Send the docker/ folder next to the setup file so Docker is offline.")
    else:
        print("  (installer/docker/ is empty — run tools/fetch_docker_installers.py)")


def _zip_files(zip_path: pathlib.Path, items: list[tuple[pathlib.Path, str]]) -> None:
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_STORED) as zf:
        for source, arcname in items:
            if source.is_file():
                zf.write(source, arcname)


def _write_client_zips(exe: pathlib.Path | None) -> None:
    """One unzip-and-run pack per platform, with the matching Docker installer."""
    docker = ROOT / "installer" / "docker"
    dist = ROOT / "dist"
    bat = dist / "Clinical-Note-Labeller-Setup.bat"
    command = dist / "Clinical-Note-Labeller-Setup.command"
    note = "Keep the docker folder next to the setup file, then double-click setup."

    win_items: list[tuple[pathlib.Path, str]] = []
    if exe and exe.is_file():
        win_items.append((exe, "Clinical-Note-Labeller-Windows/Clinical-Note-Labeller-Setup.exe"))
    if bat.is_file():
        win_items.append((bat, "Clinical-Note-Labeller-Windows/Clinical-Note-Labeller-Setup.bat"))
    win_docker = docker / "DockerDesktopInstaller.exe"
    if win_docker.is_file():
        win_items.append((win_docker, "Clinical-Note-Labeller-Windows/docker/DockerDesktopInstaller.exe"))
    win_note = dist / "Windows-README.txt"
    win_note.write_text(note + "\n", encoding="utf-8")
    win_items.append((win_note, "Clinical-Note-Labeller-Windows/README.txt"))
    win_zip = dist / "Clinical-Note-Labeller-Windows.zip"
    _zip_files(win_zip, win_items)
    print(f"wrote {win_zip.relative_to(ROOT)}  ({win_zip.stat().st_size:,} bytes)")

    mac_items: list[tuple[pathlib.Path, str]] = []
    if command.is_file():
        mac_items.append((command, "Clinical-Note-Labeller-Mac/Clinical-Note-Labeller-Setup.command"))
    for dmg in ("Docker-arm64.dmg", "Docker-amd64.dmg"):
        path = docker / dmg
        if path.is_file():
            mac_items.append((path, f"Clinical-Note-Labeller-Mac/docker/{dmg}"))
    mac_note = dist / "Mac-README.txt"
    mac_note.write_text(
        note + " On a Mac: right-click the .command → Open → Open.\n", encoding="utf-8")
    mac_items.append((mac_note, "Clinical-Note-Labeller-Mac/README.txt"))
    mac_zip = dist / "Clinical-Note-Labeller-Mac.zip"
    _zip_files(mac_zip, mac_items)
    print(f"wrote {mac_zip.relative_to(ROOT)}  ({mac_zip.stat().st_size:,} bytes)")


def _build_windows_exe(filled_bat: pathlib.Path) -> pathlib.Path | None:
    """Cross-compile a double-click .exe that embeds the filled .bat."""
    go = shutil.which("go")
    if go is None:
        return None
    output = ROOT / "dist" / "Clinical-Note-Labeller-Setup.exe"
    launcher_src = ROOT / "installer" / "windows-launcher" / "main.go"
    with tempfile.TemporaryDirectory() as tmp:
        work = pathlib.Path(tmp)
        shutil.copy(launcher_src, work / "main.go")
        (work / "go.mod").write_text("module cnl-setup\n\ngo 1.22\n")
        (work / "setup.bat").write_bytes(filled_bat.read_bytes())
        env = {**os.environ, "GOOS": "windows", "GOARCH": "amd64", "CGO_ENABLED": "0"}
        subprocess.run(
            [go, "build", "-trimpath", "-ldflags", "-s -w", "-o", str(output), "."],
            cwd=work, env=env, check=True,
        )
    print(f"wrote {output.relative_to(ROOT)}  ({output.stat().st_size:,} bytes, PE exe)")
    return output


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env", default=str(ROOT / ".env"))
    parser.add_argument("--tag", default="latest", help="image tag the installer pulls")
    args = parser.parse_args()

    env = read_env(pathlib.Path(args.env))

    values = {"@@IMAGE_TAG@@": args.tag}
    missing = []
    for placeholder, (name, default) in FIELDS.items():
        # API_KEYS is a comma-separated list; the installer uses the first entry.
        value = env.get(name, default).split(",")[0].strip() or default
        if not value:
            missing.append(name)
        values[placeholder] = value

    bat_output = ROOT / "dist" / "Clinical-Note-Labeller-Setup.bat"
    for template, output, newline, executable in TARGETS:
        text = template.read_text()
        for placeholder, value in values.items():
            text = text.replace(placeholder, value)
        if "@@" in text:
            sys.exit(f"{template.name} still contains unfilled placeholders")

        output.parent.mkdir(parents=True, exist_ok=True)
        # .bat needs CRLF - LF-only endings break labels and goto on Windows.
        body = text.replace("\r\n", "\n")
        if newline != "\n":
            body = body.replace("\n", newline)
        output.write_bytes(body.encode("utf-8"))
        if executable:
            output.chmod(0o755)

        ending = "CRLF" if newline == "\r\n" else "LF"
        print(f"wrote {output.relative_to(ROOT)}  ({output.stat().st_size:,} bytes, {ending})")

    exe = _build_windows_exe(bat_output)
    _copy_bundled_docker()
    _write_client_zips(exe)

    print()
    for name in ("OPENAI_API_KEY", "LLAMA_CLOUD_API_KEY"):
        value = env.get(name, "")
        state = f"{value[:6]}…{value[-4:]}" if value else "EMPTY"
        print(f"  {name:22} {state}")
    if missing:
        print("\n  WARNING: no value for " + ", ".join(missing))
        print("  The app still runs; unlabelled notes go to the approval queue instead.")
    print("\n  Windows client -> send Clinical-Note-Labeller-Windows.zip")
    print("  Mac client     -> send Clinical-Note-Labeller-Mac.zip")
    if exe is None:
        print("\n  Note: .exe was not built (install Go, then re-run). The .bat is enough.")
    print("\n  These files contain live credentials. Do not commit or publish them.")


if __name__ == "__main__":
    main()
