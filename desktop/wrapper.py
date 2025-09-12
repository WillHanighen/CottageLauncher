/********/

/********/

import threading
import time
import webbrowser
import os
import httpx
import argparse
import sys
from pathlib import Path
import uvicorn
from PyQt6.QtWidgets import QApplication, QMainWindow, QVBoxLayout, QWidget
from PyQt6.QtWebEngineWidgets import QWebEngineView, QWebEnginePage
from PyQt6.QtCore import QUrl, QTimer
from PyQt6.QtGui import QIcon, QDesktopServices

# Ensure project root is on sys.path when running this file directly
ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from app.config import get_settings


class ExternalLinkPage(QWebEnginePage):
    """Route target=_blank links to the system browser."""

    def createWindow(self, _type):  # _type: QWebEnginePage.WebWindowType
        temp_page = QWebEnginePage(self)
        temp_page.urlChanged.connect(lambda url: QDesktopServices.openUrl(url))
        return temp_page


class CottageLauncherWindow(QMainWindow):
    def __init__(self, url: str):
        super().__init__()
        self.setWindowTitle("Cottage Launcher")
        self.setGeometry(100, 100, 1200, 800)
        self.setMinimumSize(800, 600)
        
        # Create central widget and layout
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        layout = QVBoxLayout(central_widget)
        
        # Create web view
        self.web_view = QWebEngineView()
        # Ensure external links (target=_blank) open in the system browser
        self.web_view.setPage(ExternalLinkPage(self.web_view))
        self.web_view.setUrl(QUrl(url))
        layout.addWidget(self.web_view)
        
        # Enable text selection and other features
        self.web_view.settings().setAttribute(self.web_view.settings().WebAttribute.TextSelectEnabled, True)
        self.web_view.settings().setAttribute(self.web_view.settings().WebAttribute.JavascriptEnabled, True)
        self.web_view.settings().setAttribute(self.web_view.settings().WebAttribute.LocalContentCanAccessRemoteUrls, True)


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
    parser.add_argument("--url", default=None, help="Override URL to open in PyWebView (e.g., http://127.0.0.1:8000)")
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

    # Try to launch PySide6 + QtWebEngine; fallback to system browser if unavailable
    try:
        app = QApplication(sys.argv)
        app.setApplicationName("Cottage Launcher")
        app.setApplicationVersion("1.0.0")
        
        # Create and show the main window
        window = CottageLauncherWindow(sys_url)
        window.show()
        
        # Start the Qt event loop
        sys.exit(app.exec())
    except Exception as e:
        print("[Wrapper] PySide6 + QtWebEngine unavailable or unsupported. Falling back to system browser.\n", e)
        webbrowser.open(sys_url)


if __name__ == "__main__":
    main()
