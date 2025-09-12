# Cottage Launcher

A modern, clean Minecraft launcher with modpack support and Modrinth discovery. Built with FastAPI, Tailwind CSS, HTMX, Alpine.js, and a CEF Python desktop wrapper.

## MUST READ: Linux CEF compatibility on non-Ubuntu 22.04 LTS / Debian 12
CEF Python is sensitive to the host distro (glibc and system libraries). On most Linux distributions other than Ubuntu 22.04 LTS or Debian 12, the embedded CEF will refuse to load. If CEF is failing on your system, run the desktop wrapper inside an Ubuntu 22.04 environment via distrobox and Python 3.9.13.

Quick steps (host + container):
```bash
# On the host
# 1) Create and enter an Ubuntu 22.04 environment
#    (run this from your project directory so the code is available inside the container)
distrobox-create --name cefbox --image ubuntu:22.04
distrobox-enter cefbox

# Inside the container
# 2) Install build prerequisites
sudo apt update && sudo apt install -y \
  build-essential libffi-dev libssl-dev zlib1g-dev libbz2-dev \
  libreadline-dev libsqlite3-dev libncurses5-dev libncursesw5-dev \
  xz-utils tk-dev wget curl apt install libnss3 libxss1 libgconf-2-4 \
  libasound2 libatk1.0-0 libgtk-3-0 libx11-xcb1

# 3) Build and install Python 3.9.13 locally (no sudo)
wget https://www.python.org/ftp/python/3.9.13/Python-3.9.13.tgz
tar xvf Python-3.9.13.tgz
cd Python-3.9.13
./configure --prefix=$HOME/python3.9.13 --enable-optimizations
make -j"$(nproc)"
make install
export PATH="$HOME/python3.9.13/bin:$PATH"
cd ../

# 4) Create a venv, install deps, and run the desktop wrapper
python3.9 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python desktop/wrapper.py
```

Notes:
- Run `distrobox-enter cefbox` from the project root so the source tree is available inside the container.
- The wrapper will start the FastAPI server and open the embedded Chromium (CEF). Use `--no-server` to attach to an already-running server.
- This is a temporary workaround until broader Linux wheel coverage for `cefpython3` is available.

## Features (WIP)
- Mod discovery via Modrinth API (search, details, versions)
- Manage modpacks and local installations
- Download manager with progress (Redis-backed)
- Launch vanilla and modded (Fabric/Forge/Quilt) profiles
- Desktop app via bundled Chromium (CEF Python)

## Tech Stack
- Backend: FastAPI + Uvicorn (Gunicorn for prod)
- Database: PostgreSQL (SQLAlchemy async + Alembic)
- Cache/Queue: Redis
- Templates: Jinja2 + HTMX + Alpine.js
- Styling: Tailwind CSS (CDN for dev)
- Desktop: CEF Python wrapper

## Prerequisites
- Python 3.10+
- Redis (for future download queue)
- PostgreSQL (for persistent data)

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

## Desktop Wrapper (Experimental)
CEF Python has native requirements. On Linux, ensure GTK/X11 dependencies. If `pip install cefpython3` fails, see upstream docs.

```bash
# Run the desktop app (starts server and opens embedded Chromium)
python3.9 desktop/wrapper.py
```

### CEF Python and Python 3.9
The current CEF Python wheels do not fully support Python 3.9 on all platforms. If you see an error like:
```
Exception: Python version not supported: 3.9.x
```
you have two options:

1) Use the wrapper's fallback to your system browser (no CEF)
   - The wrapper now lazily imports CEF and falls back to `webbrowser.open()` if CEF is unavailable.
   - Command:
     ```bash
     python3.9 desktop/wrapper.py
     ```
     It will open your default browser if CEF isn't supported.

2) Create a Python 3.9 virtualenv specifically for the wrapper
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

Note: `requirements.txt` only installs `cefpython3` on Python < 3.12. If you previously installed it on 3.12, you can remove it with:
```bash
python3.12 -m pip uninstall -y cefpython3
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
