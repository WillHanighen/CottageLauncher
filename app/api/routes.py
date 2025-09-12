from pathlib import Path
from fastapi import APIRouter, Request, Query
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from app.config import get_settings
from app.services.modrinth import ModrinthClient

router = APIRouter()

TEMPLATES_DIR = Path(__file__).resolve().parents[1] / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


@router.get("/", response_class=HTMLResponse)
async def browse_page(request: Request):
    return templates.TemplateResponse("browse.html", {"request": request})


@router.get("/modpacks", response_class=HTMLResponse)
async def modpacks_page(request: Request):
    return templates.TemplateResponse("modpacks.html", {"request": request})


@router.get("/installed", response_class=HTMLResponse)
async def installed_page(request: Request):
    return templates.TemplateResponse("installed.html", {"request": request})


@router.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request):
    return templates.TemplateResponse("settings.html", {"request": request})


@router.get("/browse/search", response_class=HTMLResponse)
async def browse_search(request: Request, q: str = Query("")):
    settings = get_settings()
    async with ModrinthClient(user_agent=settings.modrinth_user_agent) as client:
        projects = await client.search_projects(q)
    return templates.TemplateResponse(
        "components/project_list.html",
        {"request": request, "projects": projects, "query": q},
    )


@router.get("/api/projects/{project_id}")
async def get_project(project_id: str):
    settings = get_settings()
    async with ModrinthClient(user_agent=settings.modrinth_user_agent) as client:
        data = await client.get_project(project_id)
    return JSONResponse(data)
