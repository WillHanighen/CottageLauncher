# Cottage Launcher

A modern, clean Minecraft launcher with modpack support and Modrinth discovery. Built with FastAPI, Tailwind CSS, HTMX, Alpine.js, and a PyWebView desktop wrapper.

## Desktop Wrapper

The desktop wrapper uses PyWebView, which provides a lightweight, cross-platform web view that works on Windows, macOS, and Linux without requiring additional system dependencies. PyWebView automatically uses the system's native web engine (WebView2 on Windows, WKWebView on macOS, and WebKit on Linux).

## Features (WIP)

- Mod discovery via Modrinth API (search, details, versions)
- Manage modpacks and local installations
- Download manager with progress (Redis-backed)
- Launch vanilla and modded (Fabric/Forge/Quilt) profiles
- Desktop app via PyWebView (lightweight, cross-platform)

## Tech Stack

- Backend: FastAPI + Uvicorn (Gunicorn for prod)
- Database: PostgreSQL (SQLAlchemy async + Alembic)
- Cache/Queue: Redis
- Templates: Jinja2 + HTMX + Alpine.js
- Styling: Tailwind CSS (CDN for dev)
- Desktop: PyWebView wrapper

## Prerequisites

- Python 3.10+
- Redis (for future download queue)
- PostgreSQL (for persistent data)
- For desktop wrapper: PyWebView with Qt or GTK backends (automatically installed via requirements.txt)

## Setup

```bash
# 1) Create and activate a virtualenv
python3.9 -m venv .venv
source .venv/bin/activate  # Windows: .venv\\Scripts\\activate

# 2) Install deps
python3.9 -m pip install -r requirements.txt

# 3) Copy env and edit
cp .env.example .env
# update DATABASE_URL, REDIS_URL if needed

# 4) Run backend (dev)
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000

# 5) Open the UI
# Visit http://127.0.0.1:8000
```

## Desktop Wrapper continued

PyWebView provides a lightweight desktop wrapper that works across all platforms without requiring additional system dependencies.

```bash
# Run the desktop app (starts server and opens in native web view)
python desktop/wrapper.py
```

### PyWebView Features

- **Cross-platform**: Works on Windows, macOS, and Linux
- **Multiple backends**: Supports Qt and GTK backends for maximum compatibility
- **Lightweight**: Uses system's native web engine (WebView2/WKWebView/WebKit)
- **Fallback support**: Automatically falls back to system browser if PyWebView is unavailable

### Platform-specific Notes

- **Windows**: Uses WebView2 (included in Windows 10/11)
- **macOS**: Uses WKWebView (included in macOS)
- **Linux**: Uses Qt WebEngine or GTK WebKit (dependencies installed via requirements.txt)

### Dependencies

The desktop wrapper requires GUI backends for PyWebView:

- **PyQt5 + PyQtWebEngine**: For Qt-based web rendering
- **PyGObject**: For GTK-based web rendering (Linux)

These are automatically installed when you run `pip install -r requirements.txt`.

If PyWebView is not available on your system, the wrapper will automatically fall back to opening your default web browser.

- Ensure Python 3.9 is installed on your system.
- Create and activate a 3.9 venv, then install deps:

```bash
python3.9 -m venv .venv311
source .venv311/bin/activate
python -m pip install -r requirements.txt
```

- Run the wrapper:

```bash
python desktop/wrapper.py
```

Note: If you previously had `cefpython3` installed, you can remove it with:

```bash
pip uninstall -y cefpython3
```

### Attach to an already-running server

You can run the server yourself (e.g., with reload) and ask the wrapper to just open a window:

```bash
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
# In another terminal:
python desktop/wrapper.py --no-server --host 127.0.0.1 --port 8000
```

## Docker Services (Optional)

A minimal `docker-compose.yml` is provided for Postgres and Redis.

```bash
docker compose up -d postgres redis
```

## Notes

- Tailwind is loaded via CDN for rapid iteration. For production, switch to a build pipeline.
- Database models and migrations will be added in subsequent steps.

## License

MIT
