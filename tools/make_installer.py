#!/usr/bin/env python3
"""Generate the client installers by filling the templates with real credentials.

The templates in installer/ are safe to commit; the generated files are not,
because they carry live API keys. They are written to dist/, which is gitignored.

    python3 tools/make_installer.py [--env .env] [--tag latest]
"""
from __future__ import annotations

import argparse
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
# (template, output, line-ending, executable)
TARGETS = [
    (ROOT / "installer" / "Install-ClinicalNoteLabeller.bat",
     ROOT / "dist" / "Clinical-Note-Labeller-Setup.bat", "\r\n", False),
    (ROOT / "installer" / "install-clinical-note-labeller.command",
     ROOT / "dist" / "Clinical-Note-Labeller-Setup.command", "\n", True),
]

# Placeholder -> (env var, default). A None default means the value is required.
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
    print()
    for name in ("OPENAI_API_KEY", "LLAMA_CLOUD_API_KEY"):
        value = env.get(name, "")
        state = f"{value[:6]}…{value[-4:]}" if value else "EMPTY"
        print(f"  {name:22} {state}")
    if missing:
        print("\n  WARNING: no value for " + ", ".join(missing))
        print("  The app still runs; unlabelled notes go to the approval queue instead.")
    print("\n  Windows client -> send Clinical-Note-Labeller-Setup.bat")
    print("  Mac client     -> send Clinical-Note-Labeller-Setup.command")
    print("\n  These files contain live credentials. Do not commit or publish them.")


if __name__ == "__main__":
    main()
