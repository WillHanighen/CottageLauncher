import threading
import time
import webbrowser
import os
import httpx
import argparse
import sys
from pathlib import Path
import uvicorn

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
        reload=False,
    )


def main():
    settings = get_settings()

    parser = argparse.ArgumentParser(description="Cottage Launcher Desktop Wrapper")
    parser.add_argument("--no-server", action="store_true", help="Do not start the embedded FastAPI server")
    parser.add_argument("--host", default=settings.app_host, help="Host for the server (default from .env)")
    parser.add_argument("--port", type=int, default=settings.app_port, help="Port for the server (default from .env)")
    parser.add_argument("--url", default=None, help="Override URL to open in CEF (e.g., http://127.0.0.1:8000)")
    args = parser.parse_args()

    # Start backend server in a separate thread (unless attaching to existing server)
    if not args.no_server:
        t = threading.Thread(target=run_server, args=(args.host, args.port), daemon=True)
        t.start()

    # Wait for server to be reachable
    sys_url = args.url or f"http://{args.host}:{args.port}"
    start = time.time()
    while time.time() - start < 10:
        try:
            r = httpx.get(f"{sys_url}/healthz", timeout=0.5)
            if r.status_code == 200:
                break
        except Exception:
            pass
        time.sleep(0.2)

    sys_settings = {
        "context_menu": {
            "enabled": True,
            "navigation": True,
            "print": False,
        }
    }

    # Add Linux Wayland/GPU compatibility switches if needed
    switches = {
        "disable-gpu": "1",
        "disable-gpu-compositing": "1",
    }
    if os.environ.get("XDG_SESSION_TYPE", "").lower() == "wayland":
        switches.update({
            "enable-features": "UseOzonePlatform",
            "ozone-platform": "wayland",
        })

    # Try to launch embedded Chromium via CEF; fallback to system browser if unavailable
    try:
        from cefpython3 import cefpython as cef
        cef.Initialize(settings=sys_settings, switches=switches)
        cef.CreateBrowserSync(url=sys_url, window_title="Cottage Launcher")
        cef.MessageLoop()
        cef.Shutdown()
    except Exception as e:
        print("[Wrapper] CEF unavailable or unsupported in this Python version. Falling back to system browser.\n", e)
        webbrowser.open(sys_url)


if __name__ == "__main__":
    main()
