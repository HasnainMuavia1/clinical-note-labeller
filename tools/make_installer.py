#!/usr/bin/env python3
"""Generate the client installer by filling the template with real credentials.

The template in installer/ is safe to commit; the generated file is not, because
it carries live API keys. It is written to dist/, which is gitignored.

    python3 tools/make_installer.py [--env .env] [--tag latest]
"""
from __future__ import annotations

import argparse
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
TEMPLATE = ROOT / "installer" / "Install-ClinicalNoteLabeller.bat"
OUTPUT = ROOT / "dist" / "Clinical-Note-Labeller-Setup.bat"

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
    text = TEMPLATE.read_text()

    text = text.replace("@@IMAGE_TAG@@", args.tag)
    missing = []
    for placeholder, (name, default) in FIELDS.items():
        # API_KEYS is a comma-separated list; the installer uses the first entry.
        value = env.get(name, default).split(",")[0].strip() or default
        if not value:
            missing.append(name)
        text = text.replace(placeholder, value)

    if "@@" in text:
        sys.exit("template still contains unfilled placeholders")

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    # Batch files need CRLF; LF-only line endings break labels and goto on Windows.
    OUTPUT.write_bytes(text.replace("\r\n", "\n").replace("\n", "\r\n").encode("utf-8"))

    print(f"wrote {OUTPUT.relative_to(ROOT)}  ({OUTPUT.stat().st_size:,} bytes, CRLF)")
    for name in ("OPENAI_API_KEY", "LLAMA_CLOUD_API_KEY"):
        value = env.get(name, "")
        state = f"{value[:6]}…{value[-4:]}" if value else "EMPTY"
        print(f"  {name:22} {state}")
    if missing:
        print("\n  WARNING: no value for " + ", ".join(missing))
        print("  The app still runs; unlabelled notes go to the approval queue instead.")
    print("\n  This file contains live credentials. Do not commit it or publish it.")


if __name__ == "__main__":
    main()
