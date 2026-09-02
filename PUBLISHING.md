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

## 2. Make the packages public — ONE TIME ONLY

**The installer cannot work until you do this.** New GHCR packages are private by
default, so a client machine gets `unauthorized` when it tries to pull.

After the first successful workflow run, for each of the three packages:

1. Open <https://github.com/HasnainMuavia1?tab=packages>
2. Click the package → **Package settings**
3. Scroll to **Danger Zone** → **Change visibility** → **Public**

You only ever do this once per package. Later pushes keep the visibility.

To confirm it worked, from any machine with Docker and no GitHub login:

```bash
docker pull ghcr.io/hasnainmuavia1/clinical-note-labeller/backend:latest
```

## 3. Generate the client installer

```bash
python3 tools/make_installer.py            # or --tag <commit-sha> to pin a build
```

This reads your local `.env` and writes `dist/Clinical-Note-Labeller-Setup.bat`.
Send that one file to the client. Nothing else — no repository, no `.env`, no
instructions beyond "double-click it".

### The credential trade-off, stated plainly

The generated `.bat` contains your OpenAI and LlamaParse keys in plain text. Anyone
who opens it in Notepad can read and use them, and they will be billed to you. That
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

`dist/` is gitignored. Never commit the generated file.

## What the client's machine does

1. Installs Docker Desktop if absent (downloads ~600 MB, needs one restart).
2. Downloads `docker-compose.prod.yml` — a single file, about 3 KB.
3. Writes its own `.env` and picks free ports if 8000 or 5173 are taken.
4. Pulls the images (~1 GB once) and starts six containers.
5. Waits for health, then opens the browser and drops a Desktop shortcut.

No Python, Node, git or compiler is ever installed on the client machine. The code
dictionaries are baked into the backend image, so there is nothing to mount and
nothing that can go missing.
