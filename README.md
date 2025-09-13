# Cottage Launcher

A modern, clean Minecraft launcher with Modrinth integration. Backend is FastAPI (HTMX + Tailwind UI), with an Electron desktop wrapper for a native-like experience.

## WARNING: THIS PROJECT WAS BUILT AND TESTED ON LINUX ONLY AND IS NOT DESIGNED FOR USE ON WINDOWS OR MACOS

### it is unlikely this project will funciton on Windows and *may* work on Mac, but do not count on it

## Features

- **Modpack install (.mrpack)** via Modrinth API
- **10-way parallel downloads** for fast pack installs
- **Per-instance management** under `~/.cottage_launcher/instances/<slug>/`
- **Shared Minecraft dir** for versions/libs: `~/.cottage_launcher/minecraft/`
- **Auto Java (Temurin JRE)** per instance (Java 21/17/16/8 based on MC version)
- **Launch vanilla and loaders** (Fabric/Quilt/Forge/NeoForge)
- **Instance management UI**
  - Mods: upload, enable/disable, delete, filter installed
  - Resource packs: upload/filter/manage
  - Shader packs: upload/filter/manage
  - Data packs: per-world upload/filter/manage (`saves/<world>/datapacks/`)
  - Catalog page to browse Modrinth, view project pages, and add content
- **Classpath safety**: automatically de-duplicates conflicting ASM jars to prevent Fabric crashes

## Requirements

- Python 3.9+
- Node.js + npm (for the Electron wrapper)

Optional (not required to run basic launcher):

- Docker (for optional Postgres/Redis dev services)

## Setup

```bash
# 1) Create and activate a virtualenv (Python 3.9+)
python3.9 -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate

# 2) Install Python dependencies
python -m pip install -r requirements.txt

# 3) Create .env from example
cp .env.example .env
# Edit as needed (e.g., DEV_MODE=true)
```

### Run (Desktop Wrapper)

```bash
# Starts FastAPI in a background thread on a free port, then launches Electron
python desktop/wrapper.py
```

Wrapper behavior:

- Probes the server at `/healthz` and passes `BACKEND_URL` to Electron.
- Uses `desktop/electron/` if present. If `package.json` exists, runs `npm start`; otherwise falls back to `npx electron .`.
- If Electron tooling is not found, opens the system browser as a fallback.

You may want to install Electron deps for a smoother dev experience:

```bash
cd desktop/electron
npm install
npm start
```

### Run (Backend only)

```bash
uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
# Then open http://127.0.0.1:8000 in your browser
```

### Alternate wrapper modes

```bash
# Attach wrapper to an already-running server
python desktop/wrapper.py --no-server --host 127.0.0.1 --port 8000

# Launch wrapper but use a browser instead of Electron
python desktop/wrapper.py --frontend browser
```

## How it works

- Installed instances live under `~/.cottage_launcher/instances/<slug>/`.
  - Pack files and overrides are placed here.
  - Logs (e.g., `latest-launch.log`) are written here.
- Minecraft versions and libraries are installed once in `~/.cottage_launcher/minecraft/` and shared by all instances.
- A suitable Temurin JRE is downloaded into each instance folder and used to launch the game.
- Launch uses `minecraft-launcher-lib` to build the command and starts the process with the instance directory as the game directory.

## UI Guide

- Browse/Modpacks: search and discover packs from Modrinth.
- Installed: shows your installed instances.
  - Click **Manage** on a card to open the management page for that instance.
- Manage page includes:
  - Mods: upload jar, filter installed, enable/disable, delete.
  - Resource packs & Shaders: upload/filter/manage.
  - Data packs: select world (from `saves`), upload/filter/manage.
  - Check Updates: if the original Modrinth project/version is recorded, you can fetch and apply updates.
  - **Browse catalog** link: opens a dedicated page to search Modrinth and add content (mods/resource packs/shaders/data packs). Includes a button to view the project page.

## Troubleshooting

- **Manage page 404**: If `/instances/<slug>` 404s, the instance folder may not exist or the slug changed.
  - Use the Installed page to navigate; a friendly 404 will list available slugs as links.
- **Fabric crash: duplicate ASM classes**: The launcher prunes old ASM versions on disk and de-duplicates classpath entries at runtime. If needed, manually remove older `org/ow2/asm/*/9.6` directories from `~/.cottage_launcher/minecraft/libraries/` and relaunch.
- **Java issues**: The launcher downloads a Temurin JRE per instance when needed. You can set `JAVA_HOME`, but per-instance JRE is preferred for compatibility.

## Developer Notes

- Stack: FastAPI, Jinja2, HTMX, Alpine.js, Tailwind (CDN in dev), Electron wrapper.
- Important paths:
  - Instances: `~/.cottage_launcher/instances/`
  - Shared MC dir: `~/.cottage_launcher/minecraft/`
- Significant modules:
  - Backend routes: `app/api/routes.py`
  - Templates: `app/templates/`
  - Modrinth client: `app/services/modrinth.py`
  - Desktop wrapper: `desktop/wrapper.py`

## Optional Docker Services

A minimal `docker-compose.yml` includes Postgres and Redis if you wish to extend the app.

```bash
docker compose up -d postgres redis
```

## License

CC-BY-NC-4.0
