# Cottage Launcher desktop wrapper (Electron-only)

import threading
import time
import webbrowser
import os
import httpx
import argparse
import sys
from pathlib import Path
import uvicorn
import subprocess
import socket

# Ensure project root is on sys.path when running this file directly
ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from app.config import get_settings


def run_server(host: str, port: int):
    uvicorn.run(
        "app.main:app",
        host=host,
        port=port,
        log_level="info",
        # Reload cannot run in a background thread (uses OS signals). Musb be False.
        reload=False,
    )


def find_free_port(host: str) -> int:
    """Bind to port 0 on the given host to obtain an available ephemeral port."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind((host, 0))
        return s.getsockname()[1]


def launch_electron_app(url: str, electron_dir: Path) -> int:
    """
    Launch an Electron app located at `electron_dir`, passing the backend URL via BACKEND_URL env.
    Prefers `npm start` if package.json is present, otherwise tries `npx electron .`.
    Returns the Electron process exit code, or -1 on failure to spawn.
    """
    env = os.environ.copy()
    env["BACKEND_URL"] = url

    # Pass DEV_MODE from .env to Electron
    settings = get_settings()
    env["DEV_MODE"] = str(settings.dev_mode).lower()

    package_json = electron_dir / "package.json"
    try:
        if package_json.exists():
            cmd = ["npm", "start", "--silent"]
        else:
            cmd = ["npx", "electron", ".", "--no-sandbox"]
        print(f"[Wrapper] Launching Electron in {electron_dir} with URL {url} (DEV_MODE={env['DEV_MODE']})")
        proc = subprocess.Popen(cmd, cwd=str(electron_dir), env=env)
        return proc.wait()
    except FileNotFoundError as e:
        print("[Wrapper] Electron tooling not found (npm/npx).", e)
        return -1
    except Exception as e:
        print("[Wrapper] Failed to launch Electron:", e)
        return -1


def main():
    settings = get_settings()

    parser = argparse.ArgumentParser(description="Cottage Launcher Desktop Wrapper")
    parser.add_argument("--no-server", action="store_true", help="Do not start the embedded FastAPI server")
    parser.add_argument("--host", default=settings.app_host, help="Host for the server (default from .env)")
    parser.add_argument("--port", type=int, default=0, help="Port for the server (0 chooses a random free port)")
    parser.add_argument("--url", default=None, help="Override URL to open in frontend (e.g., http://127.0.0.1:8000)")
    parser.add_argument("--electron-dir", default=str(ROOT_DIR / "desktop" / "electron"), help="Path to the Electron app directory (should contain package.json)")
    parser.add_argument("--frontend", choices=["electron", "browser"], default="electron", help="Frontend to launch (default: electron)")
    args = parser.parse_args()

    # Start backend server in a separate thread (unless attaching to existing server)
    if not args.no_server:
        selected_port = args.port if args.port and args.port != 0 else find_free_port(args.host)
        t = threading.Thread(target=run_server, args=(args.host, selected_port), daemon=True)
        t.start()
    else:
        selected_port = args.port if args.port else settings.app_port

    # Determine URL for frontend
    sys_url = args.url or f"http://{args.host}:{selected_port}"

    # Wait for server to be reachable
    start = time.time()
    while time.time() - start < 15:
        try:
            r = httpx.get(f"{sys_url}/healthz", timeout=0.75)
            if r.status_code == 200:
                break
        except Exception:
            pass
        time.sleep(0.25)

    # Launch Electron or fallback to system browser
    if args.frontend == "electron":
        electron_dir = Path(args.electron_dir)
        if electron_dir.exists():
            code = launch_electron_app(sys_url, electron_dir)
            if code == 0:
                sys.exit(0)
            else:
                print(f"[Wrapper] Electron exited with code {code}. Falling back to system browser.")
        else:
            print(f"[Wrapper] Electron directory not found at {electron_dir}. Falling back to system browser.")

    webbrowser.open(sys_url)


if __name__ == "__main__":
    main()
