from pathlib import Path
from typing import Optional
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
async def browse_search(
    request: Request,
    q: str = Query(""),
    type: str = Query("", alias="type"),
    loader: str = Query(""),
    mc: str = Query(""),
    index: Optional[str] = Query(None),
):
    settings = get_settings()
    # Build Modrinth facets from filters
    facets: list[list[str]] = []
    if type:
        facets.append([f"project_type:{type}"])
    if loader:
        facets.append([f"categories:{loader}"])
    if mc:
        facets.append([f"versions:{mc}"])
    async with ModrinthClient(user_agent=settings.modrinth_user_agent) as client:
        projects = await client.search_projects(q, facets=facets or None, index=index)
    return templates.TemplateResponse(
        "components/project_list.html",
        {"request": request, "projects": projects, "query": q},
    )


@router.get("/browse/featured_modpacks", response_class=HTMLResponse)
async def featured_modpacks(request: Request, limit: int = Query(9, ge=1, le=50)):
    settings = get_settings()
    async with ModrinthClient(user_agent=settings.modrinth_user_agent) as client:
        projects = await client.discover_modpacks(limit=limit)
    return templates.TemplateResponse(
        "components/project_list.html",
        {"request": request, "projects": projects, "query": ""},
    )


@router.get("/api/projects/{project_id}")
async def get_project(project_id: str):
    settings = get_settings()
    async with ModrinthClient(user_agent=settings.modrinth_user_agent) as client:
        data = await client.get_project(project_id)
    return JSONResponse(data)


@router.get("/api/projects/{project_id}/versions")
async def get_project_versions(project_id: str):
    settings = get_settings()
    async with ModrinthClient(user_agent=settings.modrinth_user_agent) as client:
        data = await client.get_project_versions(project_id)
    return JSONResponse(data)


@router.get("/modpacks/{id_or_slug}", response_class=HTMLResponse)
async def modpack_detail_page(request: Request, id_or_slug: str):
    settings = get_settings()
    async with ModrinthClient(user_agent=settings.modrinth_user_agent) as client:
        project = await client.get_project(id_or_slug)
        versions = await client.get_project_versions(id_or_slug)
    return templates.TemplateResponse(
        "modpack_detail.html",
        {"request": request, "project": project, "versions": versions},
    )


@router.get("/projects/{id_or_slug}", response_class=HTMLResponse)
async def project_detail_page(request: Request, id_or_slug: str):
    """Generic project detail page for non-modpack listings (mods, resource packs, shaders, etc.)."""
    settings = get_settings()
    async with ModrinthClient(user_agent=settings.modrinth_user_agent) as client:
        project = await client.get_project(id_or_slug)
        versions = await client.get_project_versions(id_or_slug)
    # Reuse the existing detail template, which is generic enough for all project types
    return templates.TemplateResponse(
        "modpack_detail.html",
        {"request": request, "project": project, "versions": versions},
    )
