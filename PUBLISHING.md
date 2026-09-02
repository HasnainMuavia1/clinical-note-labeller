# Publishing a client build

Three steps. Only step 2 is manual, and only once ever.

## 1. Push to `main`

`.github/workflows/publish-images.yml` runs the backend and frontend test suites,
then builds and pushes three images to GitHub Container Registry:

```
ghcr.io/hasnainmuavia1/clinical-note-labeller/backend
ghcr.io/hasnainmuavia1/clinical-note-labeller/sandbox
ghcr.io/hasnainmuavia1/clinical-note-labeller/frontend
```

Each is tagged `latest` and with the short commit SHA. If the tests fail, nothing is
published — clients never pull a broken build.

## 2. Confirm the packages are public

Packages published by Actions from a **public** repository inherit its visibility,
so this normally needs no action — it was already public on the first run here.

Verify without needing a GitHub login (a local `docker pull` can pass on stored
credentials and hide the problem, so check the registry directly):

```bash
for img in backend sandbox frontend; do
  tok=$(curl -s "https://ghcr.io/token?scope=repository:hasnainmuavia1/clinical-note-labeller/$img:pull&service=ghcr.io" | python3 -c 'import json,sys;print(json.load(sys.stdin)["token"])')
  curl -s -o /dev/null -w "$img %{http_code}\n" -H "Authorization: Bearer $tok" \
    "https://ghcr.io/v2/hasnainmuavia1/clinical-note-labeller/$img/manifests/latest"
done
```

`200` on all three means clients can pull. Anything else means the package is
private: open <https://github.com/HasnainMuavia1?tab=packages> → the package →
**Package settings** → **Danger Zone** → **Change visibility** → **Public**.
Until then the installer stops with an `unauthorized` message.

## 3. Generate the client installer

```bash
python3 tools/make_installer.py            # or --tag <commit-sha> to pin a build
```

This reads your local `.env` and writes:

- `dist/Clinical-Note-Labeller-Setup.exe` — Windows (double-click). `.bat` is also written if you prefer a script.
- `dist/Clinical-Note-Labeller-Setup.command` — macOS and Linux

Send the client the one for their machine. Nothing else — no repository, no
`.env`, no instructions beyond "double-click it".

macOS blocks unsigned downloaded scripts, so tell a Mac client: **right-click the
file → Open → Open**. Once only. If the file arrives without its executable bit
(some transfer methods strip it), `chmod +x` restores it.

### The credential trade-off, stated plainly

The generated `.exe` / `.bat` contains your OpenAI and LlamaParse keys. Anyone
who unpacks the file can read and use them, and they will be billed to you. That
is the unavoidable cost of "the client configures nothing".

Reduce the blast radius:

- Use a **separate OpenAI key** for client builds, never your main one.
- Set a **monthly budget cap** on it at
  <https://platform.openai.com/settings/organization/limits>.
- Rotate the key and regenerate the installer if a client relationship ends.

If a client should pay for their own usage instead, delete the two key lines from
`installer/Install-ClinicalNoteLabeller.bat` before generating. The app still runs:
notes it cannot classify go to the built-in approval queue for a human to label,
and code detection, NPI lookup and filing are unaffected.

`dist/` is gitignored. Never commit the generated files.

## Architecture

Images are built for **linux/amd64 and linux/arm64**, so one set of images covers:

| Machine | Architecture |
|---|---|
| Windows PC (Intel/AMD) | amd64 |
| Windows on ARM (Snapdragon X) | arm64 |
| Mac with Apple Silicon (M1-M4) | arm64 |
| Intel Mac | amd64 |

Docker picks the right one automatically; nobody has to choose. The arm64 half is
cross-built with QEMU, which is why the workflow takes longer than a single-arch
build.

## Image size

Measured, so the numbers here are not guesses:

| | On the client's disk | Compressed download |
|---|---|---|
| backend | 704 MB | ~134 MB |
| sandbox | — | ~113 MB |
| frontend | — | ~21 MB |
| postgres + redis | — | ~52 MB |
| **first run total** | | **~320 MB** |

Docker transfers gzipped layers, so disk size and download size are very different
numbers — the backend is 704 MB unpacked but about 134 MB over the wire. Slimming
the image (multi-stage build, dropping boto3 and the llama-index stack) halved the
disk footprint but moved the download only about 5 MB, because compilers and Python
packages compress roughly ten to one. The reasons to keep it slim are disk space on
the client and not shipping a compiler toolchain in a runtime image, not bandwidth.

## What the client's machine does

1. Installs Docker Desktop if absent (downloads ~600 MB, needs one restart).
2. Downloads `docker-compose.prod.yml` — a single file, about 3 KB.
3. Writes its own `.env` and picks free ports if 8000 or 5173 are taken.
4. Pulls the images (~320 MB compressed, once) and starts six containers.
5. Waits for health, then opens the browser and drops a Desktop shortcut.

No Python, Node, git or compiler is ever installed on the client machine. The code
dictionaries are baked into the backend image, so there is nothing to mount and
nothing that can go missing.
