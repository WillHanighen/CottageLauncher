from pathlib import Path
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from app.config import get_settings
from app.api.routes import router as ui_router
import sys

settings = get_settings()

app = FastAPI(title="Cottage Launcher")

# Resolve base directory for static/templates.
# When frozen by PyInstaller, data files are unpacked under sys._MEIPASS/app
if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
    BASE_DIR = Path(sys._MEIPASS) / "app"
else:
    BASE_DIR = Path(__file__).resolve().parent

STATIC_DIR = BASE_DIR / "static"
TEMPLATES_DIR = BASE_DIR / "templates"

# Ensure directories exist in dev
STATIC_DIR.mkdir(parents=True, exist_ok=True)
TEMPLATES_DIR.mkdir(parents=True, exist_ok=True)

app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

# Include UI + API routes
app.include_router(ui_router)


@app.get("/healthz")
async def healthz():
    return {"ok": True}
