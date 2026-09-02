# Bundled Docker Desktop installers

These files are downloaded by `python3 tools/fetch_docker_installers.py`.
They are gitignored (~600 MB each). The setup scripts use them when present
so Windows/macOS clients do not have to download Docker themselves.

| File | Machine |
|---|---|
| `DockerDesktopInstaller.exe` | Windows (Intel/AMD) |
| `Docker-arm64.dmg` | Mac with Apple Silicon |
| `Docker-amd64.dmg` | Intel Mac |

`python3 tools/make_installer.py` copies this folder to `dist/docker/`.
Send `docker/` next to the `.exe` / `.bat` / `.command`.
