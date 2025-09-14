from pathlib import Path
from typing import Optional
import json
import os
import re
import secrets
import shutil
import tempfile
import zipfile
import time
import subprocess
import uuid
import platform
import tarfile
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from fastapi import APIRouter, Request, Query, BackgroundTasks, Form, UploadFile, File, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, PlainTextResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
import httpx
import sys

try:
    from minecraft_launcher_lib import install as mll_install
    from minecraft_launcher_lib import command as mll_command
except Exception:  # ImportError or other
    mll_install = None
    mll_command = None
try:
    from minecraft_launcher_lib import exceptions as mll_exc
except Exception:
    mll_exc = None
try:
    from minecraft_launcher_lib.fabric import install_fabric as mll_install_fabric
except Exception:
    mll_install_fabric = None
try:
    from minecraft_launcher_lib.quilt import install_quilt as mll_install_quilt
except Exception:
    mll_install_quilt = None
try:
    from minecraft_launcher_lib.forge import install_forge_version as mll_install_forge
except Exception:
    mll_install_forge = None
try:
    from minecraft_launcher_lib.neoforge import install_neoforge_version as mll_install_neoforge
except Exception:
    mll_install_neoforge = None

from app.config import get_settings
from app.services.modrinth import ModrinthClient

router = APIRouter()

# Resolve templates directory; handle PyInstaller onefile via sys._MEIPASS
if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
    TEMPLATES_DIR = Path(sys._MEIPASS) / "app" / "templates"
else:
    TEMPLATES_DIR = Path(__file__).resolve().parents[1] / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

# Simple in-memory job tracking and instance directory
JOBS: dict[str, dict] = {}
INSTANCES_DIR = Path.home() / ".cottage_launcher" / "instances"
INSTANCES_DIR.mkdir(parents=True, exist_ok=True)
MC_DIR = Path.home() / ".cottage_launcher" / "minecraft"
MC_DIR.mkdir(parents=True, exist_ok=True)

JAVA_CANDIDATES = [
    os.environ.get("JAVA_HOME"),
    shutil.which("java"),
]

# ---- Eclipse Adoptium JVM bundling helpers ----
def _parse_mc_version(ver: str) -> tuple:
    parts = re.split(r"[.\-]", ver or "")
    nums = []
    for p in parts:
        try:
            nums.append(int(p))
        except ValueError:
            break
    while len(nums) < 3:
        nums.append(0)
    return tuple(nums[:3])


def _required_java_feature_version(mc_ver: str) -> int:
    """Return Java feature version required/recommended for a given MC version.
    1.20.5+ -> 21, 1.18.0..1.20.4 -> 17, 1.17.x -> 16, else -> 8
    """
    major, minor, patch = _parse_mc_version(mc_ver)
    if (major, minor, patch) >= (1, 20, 5):
        return 21
    if (major, minor, patch) >= (1, 18, 0):
        return 17
    if (major, minor, patch) >= (1, 17, 0):
        return 16
    return 8


def _adoptium_os() -> str:
    sys = platform.system().lower()
    if sys.startswith("linux"):
        return "linux"
    if sys.startswith("darwin"):
        return "mac"
    if sys.startswith("windows"):
        return "windows"
    return "linux"


def _adoptium_arch() -> str:
    m = platform.machine().lower()
    if m in ("x86_64", "amd64"):
        return "x64"
    if m in ("aarch64", "arm64"):
        return "aarch64"
    if m in ("armv7l", "armv7"):
        return "arm"
    return "x64"


def _ensure_adoptium_jre(java_feature: int, inst_dir: Path) -> Path:
    """Ensure a Temurin JRE for the given Java feature exists under the instance dir.
    Returns the path to the java executable.
    """
    jre_dir = inst_dir / f"jre-temurin-{java_feature}"
    java_bin = jre_dir / ("bin/java.exe" if os.name == "nt" else "bin/java")
    if java_bin.exists():
        return java_bin

    os_name = _adoptium_os()
    arch = _adoptium_arch()
    api_url = (
        f"https://api.adoptium.net/v3/assets/latest/{java_feature}/hotspot?"
        f"architecture={arch}&os={os_name}&image_type=jre"
    )
    with httpx.Client(timeout=300.0, follow_redirects=True) as client:
        r = client.get(api_url)
        r.raise_for_status()
        assets = r.json()
        if not assets:
            raise RuntimeError(f"No Adoptium assets for Java {java_feature} on {os_name}/{arch}")
        download_link = None
        filename = None
        for a in assets:
            b = a.get("binary", {})
            pkg = b.get("package", {})
            link = pkg.get("link")
            name = pkg.get("name")
            if not link:
                continue
            if os_name == "windows" and str(name).endswith(".zip"):
                download_link = link
                filename = name
                break
            if os_name != "windows" and (str(name).endswith(".tar.gz") or str(name).endswith(".tgz")):
                download_link = link
                filename = name
                break
        if not download_link:
            b = assets[0].get("binary", {})
            pkg = b.get("package", {})
            download_link = pkg.get("link")
            filename = pkg.get("name")
        if not download_link:
            raise RuntimeError("Failed to locate Adoptium download link")

        jre_dir.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="adoptium_") as td:
            tmp = Path(td)
            file_path = tmp / (filename or f"temurin-{java_feature}.pkg")
            with client.stream("GET", download_link) as resp:
                resp.raise_for_status()
                with file_path.open("wb") as f:
                    for chunk in resp.iter_bytes():
                        f.write(chunk)
            # Extract archive
            if file_path.suffix == ".zip":
                with zipfile.ZipFile(file_path, "r") as zf:
                    zf.extractall(tmp)
            else:
                with tarfile.open(file_path, "r:gz") as tf:
                    tf.extractall(tmp)
            # Find extracted root containing bin/java
            os_java = ("bin/java.exe" if os.name == "nt" else "bin/java")
            extracted_root = None
            for root, dirs, files in os.walk(tmp):
                candidate = Path(root) / os_java
                if candidate.exists():
                    extracted_root = Path(root)
                    break
            if not extracted_root:
                raise RuntimeError("Failed to locate extracted JRE bin/java")
            # Move into place
            for item in extracted_root.iterdir():
                dest = jre_dir / item.name
                if item.is_dir():
                    shutil.copytree(item, dest, dirs_exist_ok=True)
                else:
                    shutil.copy2(item, dest)

    if not java_bin.exists():
        raise RuntimeError("JRE setup incomplete; java binary missing")
    try:
        if os.name != "nt":
            java_bin.chmod(java_bin.stat().st_mode | 0o111)
    except Exception:
        pass
    return java_bin


class InstallModpackRequest(BaseModel):
    version_id: str
    instance_name: Optional[str] = None
    project_title: Optional[str] = None


def _slugify(name: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9\-_. ]+", "", name).strip().lower()
    s = re.sub(r"[\s_]+", "-", s)
    return s or f"instance-{secrets.token_hex(4)}"


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


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


def _install_modpack_job(job_id: str, version_id: str, instance_name: str, user_agent: str):
    JOBS[job_id] = {"status": "running", "progress": 0, "message": "Starting…"}
    try:
        with httpx.Client(headers={"User-Agent": user_agent, "Accept": "application/json"}, timeout=30.0) as client:
            # 1) Get version info
            JOBS[job_id].update(message="Fetching version metadata…", progress=2)
            r_v = client.get(f"https://api.modrinth.com/v2/version/{version_id}")
            r_v.raise_for_status()
            version = r_v.json()

            # 2) Find primary .mrpack file
            files = version.get("files", [])
            mrpack = None
            for f in files:
                fn = f.get("filename", "")
                if fn.endswith(".mrpack"):
                    mrpack = f
                    break
            if not mrpack and files:
                # Fallback: first file
                mrpack = files[0]
            if not mrpack:
                raise RuntimeError("No files found for version; cannot install.")

            dl_url = (mrpack.get("url") or (mrpack.get("downloads") or [None])[0])
            if not dl_url:
                raise RuntimeError("No download URL available for version file.")

            # 3) Prepare instance directory
            inst_slug = _slugify(instance_name)
            inst_dir = INSTANCES_DIR / inst_slug
            inst_dir.mkdir(parents=True, exist_ok=True)

            with tempfile.TemporaryDirectory(prefix="cottage_mrpack_") as td:
                tdir = Path(td)
                pack_path = tdir / (mrpack.get("filename") or f"{version_id}.mrpack")
                JOBS[job_id].update(message="Downloading .mrpack…", progress=8)
                with client.stream("GET", dl_url) as r:
                    r.raise_for_status()
                    with pack_path.open("wb") as f_out:
                        for chunk in r.iter_bytes():
                            f_out.write(chunk)
                JOBS[job_id].update(message="Extracting .mrpack…", progress=15)
                with zipfile.ZipFile(pack_path, "r") as zf:
                    zf.extractall(tdir)

                index_path = tdir / "modrinth.index.json"
                if not index_path.exists():
                    raise RuntimeError("modrinth.index.json not found in pack.")
                index = json.loads(index_path.read_text("utf-8"))
                # Persist index to instance for launch/runtime info
                _write_json(inst_dir / "modrinth.index.json", index)
                files_entries = index.get("files", [])
                total = max(1, len(files_entries))
                done = 0

                # 4) Download files referenced by index concurrently (up to 10 at a time)
                def _download_entry(entry: dict) -> bool:
                    rel_path = entry.get("path")
                    urls = entry.get("downloads") or []
                    if not rel_path or not urls:
                        return False
                    target_path = inst_dir / rel_path
                    target_path.parent.mkdir(parents=True, exist_ok=True)
                    url0 = urls[0]
                    try:
                        with client.stream("GET", url0) as r:
                            r.raise_for_status()
                            with target_path.open("wb") as f_out:
                                for chunk in r.iter_bytes():
                                    f_out.write(chunk)
                        return True
                    except Exception as e:
                        # Best-effort: skip failed file and continue
                        print("[Install] Failed to download", url0, e)
                        return False

                JOBS[job_id].update(message="Downloading files…", progress=15)
                lock = threading.Lock()
                with ThreadPoolExecutor(max_workers=10) as ex:
                    futures = [ex.submit(_download_entry, entry) for entry in files_entries]
                    for _ in as_completed(futures):
                        with lock:
                            done += 1
                            pct = 15 + int(80 * (done / total))
                            JOBS[job_id].update(message=f"Downloading files… {done}/{total}", progress=pct)

                # 5) Apply overrides directory if present
                overrides = tdir / "overrides"
                if overrides.exists():
                    JOBS[job_id].update(message="Applying overrides…", progress=96)
                    for root, dirs, files in os.walk(overrides):
                        rel = Path(root).relative_to(overrides)
                        dest_root = inst_dir / rel
                        dest_root.mkdir(parents=True, exist_ok=True)
                        for fn in files:
                            src_f = Path(root) / fn
                            dst_f = dest_root / fn
                            shutil.copy2(src_f, dst_f)

            # 6) Write instance manifest
            manifest = {
                "instance_name": instance_name,
                "slug": inst_slug,
                "version_id": version_id,
                "created_at": __import__("datetime").datetime.utcnow().isoformat() + "Z",
                # for updates
                "project_id": version.get("project_id"),
                "version_number": version.get("version_number"),
            }
            _write_json(inst_dir / "instance.json", manifest)

        JOBS[job_id].update(status="completed", progress=100, message="Install complete")
    except Exception as e:
        JOBS[job_id].update(status="failed", message=str(e))


@router.post("/api/install/modpack", response_class=HTMLResponse)
async def install_modpack(
    version_id: str = Form(...),
    instance_name: Optional[str] = Form(None),
    project_title: Optional[str] = Form(None),
    background_tasks: BackgroundTasks = None,
):
    settings = get_settings()
    name = instance_name or (project_title or "Modpack")
    job_id = secrets.token_hex(8)
    JOBS[job_id] = {"status": "queued", "progress": 0, "message": "Queued"}
    background_tasks.add_task(_install_modpack_job, job_id, version_id, name, settings.modrinth_user_agent)
    # Return an HTMX-friendly snippet that kicks off polling
    html = f'''<div class="p-3 rounded border border-slate-800 bg-slate-900/50">
      <div class="text-sm text-slate-300">Started install: <span class="font-mono">{name}</span></div>
      <div id="job-{job_id}" hx-get="/api/jobs/{job_id}" hx-trigger="load, every 1s" hx-swap="innerHTML"></div>
    </div>'''
    return HTMLResponse(content=html)


@router.get("/api/jobs/{job_id}", response_class=HTMLResponse)
async def get_job_status(job_id: str):
    job = JOBS.get(job_id)
    if not job:
        return HTMLResponse('<div class="text-red-400 text-sm">Job not found.</div>')
    status = job.get("status", "running")
    progress = job.get("progress", 0)
    msg = job.get("message", "")
    if status == "completed":
        return HTMLResponse(f'<div class="text-emerald-400 text-sm">✅ {msg} ({progress}%)</div>')
    if status == "failed":
        return HTMLResponse(f'<div class="text-red-400 text-sm">❌ {msg}</div>')
    # running/queued
    bar = f'''<div class="mt-2">
      <div class="w-full h-2 bg-slate-800 rounded">
        <div class="h-2 bg-emerald-600 rounded" style="width: {progress}%"></div>
      </div>
      <div class="mt-1 text-xs text-slate-400">{msg} ({progress}%)</div>
    </div>'''
    return HTMLResponse(bar)


def _dedupe_asm_in_cmd(cmd: list[str]) -> list[str]:
    """Remove duplicate org.ow2.asm artifacts from classpath (inline or in @argfile).
    Keeps the highest version per artifact to avoid Fabric duplicate ASM crash.
    """
    def _dedupe_cp_string(cp_str: str) -> str:
        sep = os.pathsep
        entries = cp_str.split(sep)
        pat = re.compile(r".*/org/ow2/asm/(?P<artifact>asm(?:-[a-z]+)*)/(?P<ver>\\d+\\.\\d+)/(?P=artifact)-(?P<ver)\\.jar$")
        versions: dict[str, list[tuple[tuple[int, int], str]]] = {}
        for e in entries:
            m = pat.match(e)
            if not m:
                continue
            art = m.group("artifact")
            ver = m.group("ver")
            try:
                major, minor = ver.split(".", 1)
                key = (int(major), int(minor))
            except Exception:
                key = (0, 0)
            versions.setdefault(art, []).append((key, e))
        keep_entries = set()
        for art, lst in versions.items():
            lst.sort()
            _, best_entry = lst[-1]
            keep_entries.add(best_entry)
        new_entries: list[str] = []
        for e in entries:
            m = pat.match(e)
            if not m:
                new_entries.append(e)
                continue
            if e in keep_entries:
                new_entries.append(e)
        return sep.join(new_entries)

    try:
        # First, handle @argument files
        for i, token in enumerate(cmd):
            if token.startswith("@"):
                arg_path = token[1:]
                p = Path(arg_path)
                if p.exists():
                    try:
                        lines = p.read_text(encoding="utf-8").splitlines()
                        # find -cp / -classpath entry, expecting next line is the classpath string
                        for j, line in enumerate(lines):
                            if line.strip() in ("-cp", "-classpath") and j + 1 < len(lines):
                                lines[j + 1] = _dedupe_cp_string(lines[j + 1])
                                p.write_text("\n".join(lines), encoding="utf-8")
                                break
                    except Exception:
                        pass

        # Then, handle inline -cp if present
        cp_idx = None
        for i, token in enumerate(cmd):
            if token in ("-cp", "-classpath"):
                cp_idx = i
                break
        if cp_idx is not None and cp_idx + 1 < len(cmd):
            cmd[cp_idx + 1] = _dedupe_cp_string(cmd[cp_idx + 1])
    except Exception:
        return cmd
    return cmd


def _prune_asm_libraries(inst_dir: Path) -> list[str]:
    """Remove older versions of org.ow2.asm artifacts from the instance libraries folder.
    Keeps only the highest version directory per artifact. Returns list of removed paths (as strings).
    """
    removed: list[str] = []
    libs_root = inst_dir / "libraries" / "org" / "ow2" / "asm"
    artifacts = [
        "asm",
        "asm-commons",
        "asm-tree",
        "asm-util",
        "asm-analysis",
    ]
    try:
        for art in artifacts:
            art_dir = libs_root / art
            if not art_dir.is_dir():
                continue
            versions: list[tuple[tuple[int, ...], Path]] = []
            for v in art_dir.iterdir():
                if not v.is_dir():
                    continue
                ver = v.name
                try:
                    nums = tuple(int(p) for p in ver.split(".") if p.isdigit())
                except Exception:
                    nums = (0,)
                versions.append((nums, v))
            if len(versions) <= 1:
                continue
            versions.sort()
            keep = versions[-1][1]
            for _, d in versions[:-1]:
                try:
                    shutil.rmtree(d, ignore_errors=True)
                    removed.append(str(d))
                except Exception:
                    pass
    except Exception:
        # Non-fatal
        return removed
    return removed


def _launch_instance_job(job_id: str, slug: str):
    JOBS[job_id] = {"status": "running", "progress": 0, "message": "Preparing launch…"}
    try:
        inst_dir = INSTANCES_DIR / slug
        if not inst_dir.exists():
            raise RuntimeError("Instance not found")

        # Read persisted index (installed during .mrpack extraction)
        index_path = inst_dir / "modrinth.index.json"
        if not index_path.exists():
            raise RuntimeError("modrinth.index.json missing in instance. Reinstall the modpack.")
        index = json.loads(index_path.read_text("utf-8"))
        deps = index.get("dependencies", {}) or {}
        mc_ver = deps.get("minecraft")
        fabric_loader = deps.get("fabric-loader")
        quilt_loader = deps.get("quilt-loader")
        forge_ver = deps.get("forge")
        neoforge_ver = deps.get("neoforge")

        # Prepare per-instance bundled JVM from Eclipse Adoptium
        if not mc_ver:
            raise RuntimeError("Minecraft version missing from modrinth.index.json")
        JOBS[job_id].update(message="Preparing bundled Java…", progress=5)
        java_feature = _required_java_feature_version(mc_ver)
        java = _ensure_adoptium_jre(java_feature, inst_dir)

        # Require authenticated Microsoft account with Minecraft entitlement
        settings = get_settings()
        auth = _load_auth_payload()
        if not auth:
            raise RuntimeError("Microsoft account required. Please sign in on the Settings page.")
        # Refresh token and verify entitlement
        if ms_account and settings.ms_client_id and auth.get("refresh_token"):
            try:
                refreshed = ms_account.complete_refresh(settings.ms_client_id, None, None, auth["refresh_token"])  # type: ignore[index]
                if isinstance(refreshed, dict):
                    auth.update({
                        "refresh_token": refreshed.get("refresh_token", auth.get("refresh_token")),
                        "access_token": refreshed.get("access_token", auth.get("access_token")),
                        "name": refreshed.get("name", auth.get("name")),
                        "id": refreshed.get("id", auth.get("id")),
                    })
                    _save_auth_payload(auth)
            except Exception as e:
                raise RuntimeError(f"Authentication refresh failed: {e}")
        username = (auth.get("name") or os.getenv("USER") or os.getenv("USERNAME") or "Player")  # type: ignore[union-attr]
        uuid = auth.get("id")  # type: ignore[assignment]
        access_token = auth.get("access_token")
        if not (uuid and access_token):
            raise RuntimeError("Invalid login data. Please sign out and sign in again.")

        if mll_install is None or mll_command is None:
            raise RuntimeError("minecraft-launcher-lib not available. Ensure it's installed (see requirements.txt).")

        # Determine version id based on loader
        version_id = None
        if fabric_loader and mc_ver:
            version_id = f"fabric-loader-{fabric_loader}-{mc_ver}"
        elif quilt_loader and mc_ver:
            version_id = f"quilt-loader-{quilt_loader}-{mc_ver}"
        elif forge_ver and mc_ver:
            version_id = f"forge-{mc_ver}-{forge_ver}"
        elif neoforge_ver:
            version_id = f"neoforge-{neoforge_ver}"
        elif mc_ver:
            version_id = mc_ver
        else:
            raise RuntimeError("Unable to determine Minecraft/loader version from modrinth.index.json")

        JOBS[job_id].update(message=f"Preparing version {version_id}…", progress=10)

        def _set_status(s):
            JOBS[job_id].update(message=str(s))

        def _set_progress(p):
            try:
                p = int(p)
            except Exception:
                p = 0
            JOBS[job_id].update(progress=min(95, max(10, 10 + int(p * 0.8))))

        callbacks = {
            "setStatus": _set_status,
            "setProgress": _set_progress,
            "setMax": lambda m: None,
            "setMessage": _set_status,
        }

        def _version_exists(vid: str) -> bool:
            if not vid:
                return False
            return (MC_DIR / "versions" / vid / f"{vid}.json").exists()

        # 1) Ensure vanilla Minecraft present first
        if mc_ver and not _version_exists(mc_ver):
            try:
                mll_install.install_minecraft_version(mc_ver, str(MC_DIR), callback=callbacks)
            except TypeError:
                mll_install.install_minecraft_version(mc_ver, str(MC_DIR))

        # 2) Install loader-specific version when applicable
        installed_vid = None
        try:
            if mll_install_fabric and fabric_loader and mc_ver:
                # minecraft_launcher_lib 6.x signature: install_fabric(mc_version, mc_dir, loader_version=None, callback=...)
                installed_vid = mll_install_fabric(mc_ver, str(MC_DIR), fabric_loader, callback=callbacks)
            elif mll_install_quilt and quilt_loader and mc_ver:
                # Signature: install_quilt(mc_version, mc_dir, loader_version=None, callback=...)
                installed_vid = mll_install_quilt(mc_ver, str(MC_DIR), quilt_loader, callback=callbacks)
            elif mll_install_forge and forge_ver and mc_ver:
                installed_vid = mll_install_forge(forge_ver, str(MC_DIR), callback=callbacks)
            elif mll_install_neoforge and neoforge_ver:
                installed_vid = mll_install_neoforge(neoforge_ver, str(MC_DIR), callback=callbacks)
        except TypeError:
            # Older lib versions may not accept callback kw
            if mll_install_fabric and fabric_loader and mc_ver:
                installed_vid = mll_install_fabric(mc_ver, str(MC_DIR), fabric_loader)
            elif mll_install_quilt and quilt_loader and mc_ver:
                installed_vid = mll_install_quilt(mc_ver, str(MC_DIR), quilt_loader)
            elif mll_install_forge and forge_ver and mc_ver:
                installed_vid = mll_install_forge(forge_ver, str(MC_DIR))
            elif mll_install_neoforge and neoforge_ver:
                installed_vid = mll_install_neoforge(neoforge_ver, str(MC_DIR))

        if isinstance(installed_vid, str) and installed_vid:
            version_id = installed_vid

        # 3) Final fallback: try generic installer for the computed version_id
        if not _version_exists(version_id):
            try:
                mll_install.install_minecraft_version(version_id, str(MC_DIR), callback=callbacks)
            except TypeError:
                mll_install.install_minecraft_version(version_id, str(MC_DIR))

        # 4) Final fallback: discover any installed loader version matching the MC version
        if not _version_exists(version_id):
            versions_dir = MC_DIR / "versions"
            discovered = None
            if versions_dir.exists():
                candidates = []
                for d in versions_dir.iterdir():
                    if not d.is_dir():
                        continue
                    name = d.name
                    if name.endswith(f"-{mc_ver}") and (name.startswith("fabric-loader") or name.startswith("quilt-loader") or name.startswith("forge-") or name.startswith("neoforge-")):
                        candidates.append(name)
                candidates.sort(key=lambda n: (versions_dir / n).stat().st_mtime, reverse=True)
                if candidates:
                    discovered = candidates[0]
            if discovered:
                version_id = discovered

        if not _version_exists(version_id):
            raise RuntimeError(f"Version '{version_id}' was not installed or found after installation attempts")

        # Prune duplicate ASM libraries from disk to avoid classpath duplicates
        try:
            removed = _prune_asm_libraries(MC_DIR)
            if removed:
                JOBS[job_id].update(message="Pruned duplicate ASM libs…", progress=96)
        except Exception:
            pass

        JOBS[job_id].update(message="Building launch command…", progress=96)
        opts = {
            "username": str(username),
            "uuid": str(uuid),
            "token": str(access_token),
            "executablePath": str(java),
            "defaultExecutablePath": str(java),
            "gameDirectory": str(inst_dir),
        }
        cmd = mll_command.get_minecraft_command(version_id, str(MC_DIR), opts)
        # Sanitize classpath to avoid duplicate ASM versions crashing Fabric
        cmd = _dedupe_asm_in_cmd(cmd)

        # Start the game process
        logfile = inst_dir / "latest-launch.log"
        with logfile.open("w", encoding="utf-8", errors="replace") as log:
            subprocess.Popen(cmd, cwd=str(inst_dir), stdout=log, stderr=log)

        JOBS[job_id].update(status="completed", progress=100, message="Game process started")
    except Exception as e:
        JOBS[job_id].update(status="failed", message=str(e))


@router.post("/api/instances/{slug}/launch", response_class=HTMLResponse)
async def launch_instance(slug: str, background_tasks: BackgroundTasks):
    # Only launching requires sign-in. If not logged in, show an inline prompt.
    settings = get_settings()
    status = _auth_status(settings)
    if not status.get("logged_in"):
        html = (
            '<div class="text-amber-300 text-sm">'
            'Sign in with your Microsoft account to launch Minecraft. '
            '<a href="/auth/login" class="underline">Sign in</a> '
            'or manage accounts in <a href="/settings" class="underline">Settings</a>.'
            '</div>'
        )
        return HTMLResponse(content=html)

    job_id = secrets.token_hex(8)
    JOBS[job_id] = {"status": "queued", "progress": 0, "message": "Queued"}
    background_tasks.add_task(_launch_instance_job, job_id, slug)
    html = f'''<div class="text-sm text-slate-300">Launching…
      <div id="launch-{job_id}" hx-get="/api/jobs/{job_id}" hx-trigger="load, every 1s" hx-swap="innerHTML"></div>
    </div>'''
    return HTMLResponse(content=html)


def _instance_card_html(d: Path, meta: dict) -> str:
    name = meta.get("instance_name") or d.name
    slug = meta.get("slug") or d.name
    created = meta.get("created_at") or ""
    return f'''<div class="border border-slate-800 rounded-md p-4 bg-slate-900/40">
      <div class="font-medium">{name}</div>
      <div class="text-xs text-slate-400">{slug}</div>
      <div class="text-xs text-slate-500 mt-1">{created}</div>
      <div class="mt-2 text-sm text-slate-400">Install folder: <span class="font-mono">{d}</span></div>
      <form class="mt-3" hx-post="/api/instances/{slug}/launch" hx-target="#launch-progress-{slug}" hx-swap="innerHTML">
        <button type="submit" class="px-3 py-1.5 rounded border border-slate-700 hover:bg-slate-800" hx-swap-oob="true">Launch</button>
      </form>
      <div class="mt-2">
        <a href="/instances/{slug}" class="text-sm px-3 py-1.5 rounded border border-slate-700 hover:bg-slate-800">Manage</a>
      </div>
      <div id="launch-progress-{slug}" class="mt-2 text-xs text-slate-400"></div>
    </div>'''


@router.get("/api/instances", response_class=HTMLResponse)
async def list_instances():
    cards = []
    if INSTANCES_DIR.exists():
        for d in sorted(INSTANCES_DIR.iterdir()):
            if not d.is_dir():
                continue
            meta = {}
            info_path = d / "instance.json"
            if info_path.exists():
                try:
                    meta = json.loads(info_path.read_text("utf-8"))
                except Exception:
                    meta = {}
            cards.append(_instance_card_html(d, meta))
    html = (
        '<div class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">' + ("".join(cards) or '<div class="text-slate-400">No instances installed yet.</div>') + "</div>"
    )
    return HTMLResponse(content=html)


# ---------------- Instance Management (mods, updates) ----------------

def _read_instance(slug: str) -> tuple[Path, dict]:
    inst_dir = INSTANCES_DIR / slug
    if not inst_dir.exists():
        raise HTTPException(status_code=404, detail="Instance not found")
    meta = {}
    info_path = inst_dir / "instance.json"
    if info_path.exists():
        try:
            meta = json.loads(info_path.read_text("utf-8"))
        except Exception:
            meta = {}
    return inst_dir, meta


def _instance_mods(inst_dir: Path) -> list[dict]:
    mods_dir = inst_dir / "mods"
    mods_dir.mkdir(parents=True, exist_ok=True)
    items = []
    for p in sorted(mods_dir.glob("*")):
        if not p.is_file():
            continue
        name = p.name
        enabled = not name.endswith(".disabled")
        items.append({"name": name, "enabled": enabled})
    return items


def _packs_list(dir_path: Path) -> list[dict]:
    dir_path.mkdir(parents=True, exist_ok=True)
    items = []
    for p in sorted(dir_path.glob("*")):
        if not p.is_file():
            continue
        name = p.name
        enabled = not name.endswith(".disabled")
        items.append({"name": name, "enabled": enabled})
    return items


def _list_html(slug: str, base: str, items: list[dict], extra_vals: Optional[dict] = None) -> str:
    rows = []
    for it in items:
        name = it["name"]
        enabled = it["enabled"]
        toggle_label = "Disable" if enabled else "Enable"
        vals = {"filename": name}
        if extra_vals:
            vals.update(extra_vals)
        hx_vals = json.dumps(vals)
        rows.append(
            f'''<div class="flex items-center justify-between py-1">
  <div class="font-mono text-sm truncate w-2/3">{name}</div>
  <div class="flex items-center gap-2">
    <button class="text-xs px-2 py-1 rounded border border-slate-700 hover:bg-slate-800" hx-post="/api/instances/{slug}/{base}/toggle" hx-vals='{hx_vals}' hx-target="#{base}-list" hx-swap="outerHTML">{toggle_label}</button>
    <button class="text-xs px-2 py-1 rounded border border-red-800 text-red-200 hover:bg-red-900" hx-post="/api/instances/{slug}/{base}/delete" hx-vals='{hx_vals}' hx-target="#{base}-list" hx-swap="outerHTML">Delete</button>
  </div>
</div>'''
        )
    body = "\n".join(rows) or '<div class="text-slate-400 text-sm">Nothing here yet.</div>'
    return f'''<div id="{base}-list" class="divide-y divide-slate-800">{body}</div>'''


@router.get("/api/instances/{slug}/mods/list", response_class=HTMLResponse)
async def list_mods(slug: str, q: str = Query("")):
    inst_dir, _ = _read_instance(slug)
    mods = _instance_mods(inst_dir)
    if q:
        ql = q.lower()
        mods = [m for m in mods if ql in m["name"].lower()]
    return HTMLResponse(_list_html(slug, "mods", mods))


@router.post("/api/instances/{slug}/mods/upload", response_class=HTMLResponse)
async def upload_mod(slug: str, file: UploadFile = File(...)):
    inst_dir, _ = _read_instance(slug)
    if not file.filename or not file.filename.endswith(".jar"):
        return HTMLResponse('<div class="text-red-400 text-sm">Please upload a .jar file.</div>')
    mods_dir = inst_dir / "mods"
    mods_dir.mkdir(parents=True, exist_ok=True)
    target = mods_dir / file.filename
    data = await file.read()
    target.write_bytes(data)
    return HTMLResponse(_list_html(slug, "mods", _instance_mods(inst_dir)))


@router.post("/api/instances/{slug}/mods/toggle", response_class=HTMLResponse)
async def toggle_mod(slug: str, filename: str = Form(...)):
    inst_dir, _ = _read_instance(slug)
    p = inst_dir / "mods" / filename
    if p.exists():
        new = p.with_name(p.stem) if p.suffix == ".disabled" else p.with_name(p.name + ".disabled")
        try:
            p.rename(new)
        except Exception:
            pass
    return HTMLResponse(_list_html(slug, "mods", _instance_mods(inst_dir)))


@router.post("/api/instances/{slug}/mods/delete", response_class=HTMLResponse)
async def delete_mod(slug: str, filename: str = Form(...)):
    inst_dir, _ = _read_instance(slug)
    p = inst_dir / "mods" / filename
    try:
        if p.exists():
            p.unlink()
    except Exception:
        pass
    return HTMLResponse(_list_html(slug, "mods", _instance_mods(inst_dir)))


@router.get("/api/instances/{slug}/resourcepacks/list", response_class=HTMLResponse)
async def list_resourcepacks(slug: str, q: str = Query("")):
    inst_dir, _ = _read_instance(slug)
    items = _packs_list(inst_dir / "resourcepacks")
    if q:
        ql = q.lower()
        items = [x for x in items if ql in x["name"].lower()]
    return HTMLResponse(_list_html(slug, "resourcepacks", items))


@router.post("/api/instances/{slug}/resourcepacks/upload", response_class=HTMLResponse)
async def upload_resourcepack(slug: str, file: UploadFile = File(...)):
    inst_dir, _ = _read_instance(slug)
    if not file.filename or not (file.filename.endswith(".zip") or file.filename.endswith(".jar")):
        return HTMLResponse('<div class="text-red-400 text-sm">Please upload a .zip or .jar file.</div>')
    target = (inst_dir / "resourcepacks") / file.filename
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(await file.read())
    return HTMLResponse(_list_html(slug, "resourcepacks", _packs_list(inst_dir / "resourcepacks")))


@router.post("/api/instances/{slug}/resourcepacks/toggle", response_class=HTMLResponse)
async def toggle_resourcepack(slug: str, filename: str = Form(...)):
    inst_dir, _ = _read_instance(slug)
    p = (inst_dir / "resourcepacks") / filename
    if p.exists():
        new = p.with_name(p.stem) if p.suffix == ".disabled" else p.with_name(p.name + ".disabled")
        try:
            p.rename(new)
        except Exception:
            pass
    return HTMLResponse(_list_html(slug, "resourcepacks", _packs_list(inst_dir / "resourcepacks")))


@router.post("/api/instances/{slug}/resourcepacks/delete", response_class=HTMLResponse)
async def delete_resourcepack(slug: str, filename: str = Form(...)):
    inst_dir, _ = _read_instance(slug)
    p = (inst_dir / "resourcepacks") / filename
    try:
        if p.exists():
            p.unlink()
    except Exception:
        pass
    return HTMLResponse(_list_html(slug, "resourcepacks", _packs_list(inst_dir / "resourcepacks")))


@router.get("/api/instances/{slug}/shaderpacks/list", response_class=HTMLResponse)
async def list_shaderpacks(slug: str, q: str = Query("")):
    inst_dir, _ = _read_instance(slug)
    items = _packs_list(inst_dir / "shaderpacks")
    if q:
        ql = q.lower()
        items = [x for x in items if ql in x["name"].lower()]
    return HTMLResponse(_list_html(slug, "shaderpacks", items))


@router.post("/api/instances/{slug}/shaderpacks/upload", response_class=HTMLResponse)
async def upload_shaderpack(slug: str, file: UploadFile = File(...)):
    inst_dir, _ = _read_instance(slug)
    if not file.filename or not file.filename.endswith(".zip"):
        return HTMLResponse('<div class="text-red-400 text-sm">Please upload a .zip file.</div>')
    target = (inst_dir / "shaderpacks") / file.filename
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(await file.read())
    return HTMLResponse(_list_html(slug, "shaderpacks", _packs_list(inst_dir / "shaderpacks")))


@router.post("/api/instances/{slug}/shaderpacks/toggle", response_class=HTMLResponse)
async def toggle_shaderpack(slug: str, filename: str = Form(...)):
    inst_dir, _ = _read_instance(slug)
    p = (inst_dir / "shaderpacks") / filename
    if p.exists():
        new = p.with_name(p.stem) if p.suffix == ".disabled" else p.with_name(p.name + ".disabled")
        try:
            p.rename(new)
        except Exception:
            pass
    return HTMLResponse(_list_html(slug, "shaderpacks", _packs_list(inst_dir / "shaderpacks")))


@router.post("/api/instances/{slug}/shaderpacks/delete", response_class=HTMLResponse)
async def delete_shaderpack(slug: str, filename: str = Form(...)):
    inst_dir, _ = _read_instance(slug)
    p = (inst_dir / "shaderpacks") / filename
    try:
        if p.exists():
            p.unlink()
    except Exception:
        pass
    return HTMLResponse(_list_html(slug, "shaderpacks", _packs_list(inst_dir / "shaderpacks")))


def _list_worlds(inst_dir: Path) -> list[str]:
    saves = inst_dir / "saves"
    if not saves.exists():
        return []
    worlds = []
    for d in sorted(saves.iterdir()):
        if d.is_dir():
            worlds.append(d.name)
    return worlds


@router.get("/api/instances/{slug}/worlds", response_class=HTMLResponse)
async def list_worlds(slug: str):
    inst_dir, _ = _read_instance(slug)
    worlds = _list_worlds(inst_dir)
    if not worlds:
        return HTMLResponse('<select id="world-select" class="w-full px-2 py-1.5 rounded bg-slate-900 border border-slate-800 text-sm"><option value="">No worlds found</option></select>')
    opts = "".join([f'<option value="{w}">{w}</option>' for w in worlds])
    html = f'<select id="world-select" name="world" class="w-full px-2 py-1.5 rounded bg-slate-900 border border-slate-800 text-sm">{opts}</select>'
    return HTMLResponse(html)


def _datapacks_list(inst_dir: Path, world: str) -> list[dict]:
    dp_dir = inst_dir / "saves" / world / "datapacks"
    return _packs_list(dp_dir)


@router.get("/api/instances/{slug}/datapacks/list", response_class=HTMLResponse)
async def list_datapacks(slug: str, world: str = Query(""), q: str = Query("")):
    inst_dir, _ = _read_instance(slug)
    if not world:
        return HTMLResponse('<div class="text-slate-400 text-sm">Select a world to view datapacks.</div>')
    items = _datapacks_list(inst_dir, world)
    if q:
        ql = q.lower()
        items = [x for x in items if ql in x["name"].lower()]
    return HTMLResponse(_list_html(slug, "datapacks", items, extra_vals={"world": world}))


@router.post("/api/instances/{slug}/datapacks/upload", response_class=HTMLResponse)
async def upload_datapack(slug: str, world: str = Form(...), file: UploadFile = File(...)):
    inst_dir, _ = _read_instance(slug)
    if not file.filename or not file.filename.endswith(".zip"):
        return HTMLResponse('<div class="text-red-400 text-sm">Please upload a .zip file.</div>')
    target = inst_dir / "saves" / world / "datapacks" / file.filename
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(await file.read())
    items = _datapacks_list(inst_dir, world)
    return HTMLResponse(_list_html(slug, "datapacks", items, extra_vals={"world": world}))


@router.post("/api/instances/{slug}/datapacks/delete", response_class=HTMLResponse)
async def delete_datapack(slug: str, filename: str = Form(...), world: str = Form(...)):
    inst_dir, _ = _read_instance(slug)
    p = inst_dir / "saves" / world / "datapacks" / filename
    try:
        if p.exists():
            p.unlink()
    except Exception:
        pass
    return HTMLResponse(_list_html(slug, "datapacks", _datapacks_list(inst_dir, world), extra_vals={"world": world}))


@router.post("/api/instances/{slug}/datapacks/toggle", response_class=HTMLResponse)
async def toggle_datapack(slug: str, filename: str = Form(...), world: str = Form(...)):
    inst_dir, _ = _read_instance(slug)
    p = inst_dir / "saves" / world / "datapacks" / filename
    if p.exists():
        new = p.with_name(p.stem) if p.suffix == ".disabled" else p.with_name(p.name + ".disabled")
        try:
            p.rename(new)
        except Exception:
            pass
    return HTMLResponse(_list_html(slug, "datapacks", _datapacks_list(inst_dir, world), extra_vals={"world": world}))


@router.get("/instances/{slug}/catalog", response_class=HTMLResponse)
async def instance_catalog_page(request: Request, slug: str):
    _, meta = _read_instance(slug)
    return templates.TemplateResponse(
        "instance_catalog.html",
        {"request": request, "slug": slug, "meta": meta},
    )


@router.get("/api/instances/{slug}/catalog/search", response_class=HTMLResponse)
async def catalog_search(slug: str, q: str = Query(""), type: str = Query("mod")):
    settings = get_settings()
    type_map = {
        "mod": "mod",
        "resourcepack": "resourcepack",
        "shader": "shader",
        "datapack": "datapack",
    }
    proj_type = type_map.get(type, "mod")
    items = []
    async with ModrinthClient(user_agent=settings.modrinth_user_agent) as client:
        facets = [[f"project_type:{proj_type}"]]
        projects = await client.search_projects(q, facets=facets, limit=20)
    for p in projects:
        title = p.get("title") or p.get("slug")
        slug_or_id = p.get("slug") or p.get("project_id") or p.get("project_id")
        view_url = f"/projects/{slug_or_id}"
        add_endpoint = {
            "mod": "/api/instances/{slug}/mods/add_modrinth",
            "resourcepack": "/api/instances/{slug}/resourcepacks/add_modrinth",
            "shader": "/api/instances/{slug}/shaderpacks/add_modrinth",
            "datapack": "/api/instances/{slug}/datapacks/add_modrinth",
        }[proj_type]
        hx_include = ' hx-include="#world-select"' if proj_type == "datapack" else ""
        items.append(
            f'''<div class="p-3 border border-slate-800 rounded bg-slate-900/40">
  <div class="font-medium truncate">{title}</div>
  <div class="mt-2 flex items-center gap-2">
    <a href="{view_url}" target="_blank" class="text-xs px-2 py-1 rounded border border-slate-700 hover:bg-slate-800">View</a>
    <button class="text-xs px-2 py-1 rounded bg-emerald-600 hover:bg-emerald-500 text-white" hx-post="{add_endpoint.format(slug=slug)}" hx-vals='{{"id_or_slug": "{slug_or_id}"}}' hx-target="#catalog-flash" hx-swap="innerHTML"{hx_include}>Add</button>
  </div>
</div>'''
        )
    html = "".join(items) or '<div class="text-slate-400">No results.</div>'
    return HTMLResponse(html)


def _download_best_version_file(id_or_slug: str, target_dir: Path, accept_ext: tuple[str, ...]) -> bool:
    """Download the first matching file extension from the latest versions of a Modrinth project.
    Synchronous implementation using httpx to avoid event loop nesting.
    """
    settings = get_settings()
    headers = {"User-Agent": settings.modrinth_user_agent, "Accept": "application/json"}
    with httpx.Client(timeout=120.0, follow_redirects=True, headers=headers) as c:
        r = c.get(f"https://api.modrinth.com/v2/project/{id_or_slug}/version")
        r.raise_for_status()
        versions = r.json() or []
        chosen = None
        for v in versions:
            v_loaders = v.get("loaders", [])
            v_games = v.get("game_versions", [])
            if v_games and v_loaders:
                chosen = v
                break
        if not chosen and versions:
            chosen = versions[0]
        if not chosen:
            return False
        # Find a .jar file
        f_url = None
        fn_out = None
        for f in chosen.get("files", []):
            url = f.get("url") or (f.get("downloads") or [None])[0]
            fn = f.get("filename") or ""
            if url and fn.endswith(accept_ext):
                f_url = url
                fn_out = fn
                break
        if not f_url:
            return False
        target_dir.mkdir(parents=True, exist_ok=True)
        dest = target_dir / (fn_out or ((chosen.get("version_number") or "mod") + ".jar"))
        with c.stream("GET", f_url) as resp:
            resp.raise_for_status()
            with dest.open("wb") as f:
                for chunk in resp.iter_bytes():
                    f.write(chunk)
        return True


@router.post("/api/instances/{slug}/resourcepacks/add_modrinth", response_class=HTMLResponse)
async def add_resourcepack_from_modrinth(slug: str, id_or_slug: str = Form(...)):
    inst_dir, _ = _read_instance(slug)
    ok = _download_best_version_file(id_or_slug, inst_dir / "resourcepacks", (".zip", ".jar"))
    if ok:
        return HTMLResponse('<div class="text-emerald-400 text-sm">Resource pack added.</div>')
    return HTMLResponse('<div class="text-red-400 text-sm">Failed to add resource pack.</div>')


@router.post("/api/instances/{slug}/shaderpacks/add_modrinth", response_class=HTMLResponse)
async def add_shaderpack_from_modrinth(slug: str, id_or_slug: str = Form(...)):
    inst_dir, _ = _read_instance(slug)
    ok = _download_best_version_file(id_or_slug, inst_dir / "shaderpacks", (".zip",))
    if ok:
        return HTMLResponse('<div class="text-emerald-400 text-sm">Shader pack added.</div>')
    return HTMLResponse('<div class="text-red-400 text-sm">Failed to add shader pack.</div>')


@router.post("/api/instances/{slug}/datapacks/add_modrinth", response_class=HTMLResponse)
async def add_datapack_from_modrinth(slug: str, id_or_slug: str = Form(...), world: str = Form(...)):
    inst_dir, _ = _read_instance(slug)
    target = inst_dir / "saves" / world / "datapacks"
    ok = _download_best_version_file(id_or_slug, target, (".zip",))
    if ok:
        return HTMLResponse('<div class="text-emerald-400 text-sm">Data pack added.</div>')
    return HTMLResponse('<div class="text-red-400 text-sm">Failed to add data pack.</div>')


@router.post("/api/instances/{slug}/mods/add_modrinth", response_class=HTMLResponse)
async def add_mod_from_modrinth(slug: str, id_or_slug: str = Form(...)):
    inst_dir, _ = _read_instance(slug)
    mc_ver, loader = _instance_loader_context(inst_dir)
    settings = get_settings()
    # Pick best compatible version and download primary .jar
    async with ModrinthClient(user_agent=settings.modrinth_user_agent) as client:
        versions = await client.get_project_versions(id_or_slug)
    chosen = None
    for v in versions:
        v_loaders = v.get("loaders", [])
        v_games = v.get("game_versions", [])
        if mc_ver and mc_ver not in v_games:
            continue
        if loader and loader not in v_loaders:
            continue
        chosen = v
        break
    if not chosen and versions:
        chosen = versions[0]
    if not chosen:
        return HTMLResponse('<div class="text-red-400 text-sm">No compatible files found.</div>')
    # Find a .jar file
    f_url = None
    fn_out = None
    for f in chosen.get("files", []):
        url = f.get("url") or (f.get("downloads") or [None])[0]
        fn = f.get("filename") or ""
        if url and fn.endswith(".jar"):
            f_url = url
            fn_out = fn
            break
    if not f_url:
        return HTMLResponse('<div class="text-red-400 text-sm">No downloadable jar found.</div>')
    mods_dir = inst_dir / "mods"
    mods_dir.mkdir(parents=True, exist_ok=True)
    target = mods_dir / (fn_out or ((chosen.get("version_number") or "mod") + ".jar"))
    with httpx.Client(timeout=60.0, follow_redirects=True) as client2:
        with client2.stream("GET", f_url) as r:
            r.raise_for_status()
            with target.open("wb") as f_out:
                for chunk in r.iter_bytes():
                    f_out.write(chunk)
    return HTMLResponse('<div class="text-emerald-400 text-sm">Mod added.</div>')

@router.get("/instances/{slug}", response_class=HTMLResponse)
async def instance_detail_page(request: Request, slug: str):
    try:
        inst_dir, meta = _read_instance(slug)
        return templates.TemplateResponse(
            "instance_manage.html",
            {
                "request": request,
                "slug": slug,
                "meta": meta,
            },
        )
    except HTTPException as e:
        if e.status_code != 404:
            raise
        # Friendly 404: show available instance slugs and a back link
        options = []
        if INSTANCES_DIR.exists():
            for d in sorted(INSTANCES_DIR.iterdir()):
                if d.is_dir():
                    options.append(f'<li><a class="text-emerald-400 hover:underline" href="/instances/{d.name}">{d.name}</a></li>')
        body = "".join(options) or '<li class="text-slate-400">No instances found.</li>'
        html = f'''<div class="space-y-3">
  <div class="text-red-400">Instance not found: <span class="font-mono">{slug}</span></div>
  <div><a href="/installed" class="text-sm px-3 py-1.5 rounded border border-slate-700 hover:bg-slate-800">← Back to Installed</a></div>
  <div class="text-sm text-slate-300">Available instances:</div>
  <ul class="list-disc pl-5 text-sm">{body}</ul>
</div>'''
        return HTMLResponse(content=html, status_code=404)

# Optional dependency: Microsoft account login via minecraft-launcher-lib
try:
    from minecraft_launcher_lib import microsoft_account as ms_account  # type: ignore
    from minecraft_launcher_lib import exceptions as mll_exc  # type: ignore
except Exception:  # pragma: no cover
    ms_account = None  # type: ignore
    mll_exc = None  # type: ignore

from cryptography.fernet import Fernet
from typing import Optional

# ---- Auth secure storage helpers (encrypted at rest in ~/.cottage_launcher) ----
AUTH_DIR = Path.home() / ".cottage_launcher"
AUTH_DIR.mkdir(parents=True, exist_ok=True)
KEY_PATH = AUTH_DIR / "secret.key"
AUTH_FILE = AUTH_DIR / "auth.enc"

# In-memory login flow state (PKCE)
AUTH_FLOW: dict[str, Optional[str]] = {"state": None, "code_verifier": None, "redirect_uri": None}

def _get_fernet() -> Fernet:
    """Return a Fernet instance using a locally stored key (generated if missing)."""
    if not KEY_PATH.exists():
        try:
            key = Fernet.generate_key()
            KEY_PATH.write_bytes(key)
            try:
                KEY_PATH.chmod(0o600)
            except Exception:
                pass
        except Exception:
            # As a last resort, use an in-memory key that won't persist
            return Fernet(Fernet.generate_key())
    try:
        key = KEY_PATH.read_bytes()
        return Fernet(key)
    except Exception:
        # Key corrupted; regenerate a fresh one
        key = Fernet.generate_key()
        KEY_PATH.write_bytes(key)
        return Fernet(key)

def _save_auth_payload(data: dict) -> None:
    try:
        f = _get_fernet()
        payload = json.dumps(data).encode("utf-8")
        token = f.encrypt(payload)
        AUTH_FILE.write_bytes(token)
        try:
            AUTH_FILE.chmod(0o600)
        except Exception:
            pass
    except Exception:
        pass

def _load_auth_payload() -> Optional[dict]:
    if not AUTH_FILE.exists():
        return None
    try:
        f = _get_fernet()
        raw = AUTH_FILE.read_bytes()
        payload = f.decrypt(raw)
        return json.loads(payload.decode("utf-8"))
    except Exception:
        return None

def _delete_auth_payload() -> None:
    try:
        if AUTH_FILE.exists():
            AUTH_FILE.unlink()
    except Exception:
        pass

def _auth_status(settings) -> dict:
    """Return current auth status. Validates refresh token if possible."""
    out = {"enabled": bool(settings.ms_client_id), "logged_in": False}
    if not settings.ms_client_id:
        return out
    data = _load_auth_payload()
    if not data:
        return out
    refresh_token = data.get("refresh_token")
    name = data.get("name")
    uuid = data.get("id")
    out.update({"name": name, "id": uuid, "has_minecraft": False})
    if not (ms_account and refresh_token):
        return out
    try:
        # Validate refresh token and entitlements
        refreshed = ms_account.complete_refresh(settings.ms_client_id, None, None, refresh_token)
        # If we got here without exceptions, the account owns Minecraft (per lib semantics)
        out["logged_in"] = True
        out["has_minecraft"] = True
        # Persist updated profile and (possibly rotated) refresh token
        if isinstance(refreshed, dict):
            data.update({
                "refresh_token": refreshed.get("refresh_token", refresh_token),
                "access_token": refreshed.get("access_token"),
                "name": refreshed.get("name", name),
                "id": refreshed.get("id", uuid),
            })
            _save_auth_payload(data)
            out.update({"name": data.get("name"), "id": data.get("id")})
    except Exception:
        # Invalid/expired token
        _delete_auth_payload()
        out.update({"logged_in": False, "has_minecraft": False})
    return out

def _render_account_card_html(status: dict) -> str:
    if not status.get("enabled"):
        return (
            '<div class="text-sm text-amber-400">Microsoft login is not configured. '
            'Set <span class="font-mono">MS_CLIENT_ID</span> in your .env to enable sign-in.</div>'
        )
    if status.get("logged_in"):
        name = status.get("name") or "Player"
        uuid = status.get("id") or ""
        return f'''
        <div class="flex items-center justify-between">
          <div>
            <div class="font-medium">Signed in as <span class="font-mono">{name}</span></div>
            <div class="text-xs text-slate-400">UUID: <span class="font-mono">{uuid}</span></div>
          </div>
          <form hx-post="/auth/logout" hx-target="#account-panel" hx-swap="innerHTML">
            <button type="submit" class="px-3 py-1.5 rounded bg-rose-600 hover:bg-rose-500 text-white text-sm">Sign out</button>
          </form>
        </div>
        '''
    else:
        return (
            '<div class="flex items-center justify-between">'
            '<div class="text-sm text-slate-300">Not signed in</div>'
            '<a href="/auth/login" class="px-3 py-1.5 rounded bg-emerald-600 hover:bg-emerald-500 text-white text-sm">Sign in with Microsoft</a>'
            '</div>'
        )

# ---------------- Microsoft Account Login & UI ----------------

@router.get("/auth/login")
async def auth_login(request: Request):
    settings = get_settings()
    if not settings.ms_client_id:
        return HTMLResponse('<div class="text-amber-400">MS_CLIENT_ID is not configured.</div>', status_code=500)
    if not ms_account:
        return HTMLResponse('<div class="text-amber-400">minecraft-launcher-lib is not available.</div>', status_code=500)
    redirect_uri = f"http://localhost:{settings.app_port}/auth/callback"
    try:
        login_url, state, code_verifier = ms_account.get_secure_login_data(settings.ms_client_id, redirect_uri)
        AUTH_FLOW["state"] = state
        AUTH_FLOW["code_verifier"] = code_verifier
        AUTH_FLOW["redirect_uri"] = redirect_uri
        return RedirectResponse(login_url, status_code=302)
    except Exception as e:
        return HTMLResponse(f'<div class="text-rose-400">Failed to start login: {str(e)}</div>', status_code=500)

@router.get("/auth/callback")
async def auth_callback(request: Request):
    settings = get_settings()
    if not settings.ms_client_id or not ms_account:
        return HTMLResponse('<div class="text-amber-400">Login is not available.</div>', status_code=500)
    # Parse auth code and verify state
    try:
        params = request.query_params
        code = params.get("code")
        state = params.get("state")
        expected_state = AUTH_FLOW.get("state")
        if not code:
            raise ValueError("Missing 'code' in callback URL")
        if expected_state and state != expected_state:
            raise AssertionError("State mismatch; please try signing in again")
        redirect_uri = AUTH_FLOW.get("redirect_uri") or f"http://localhost:{settings.app_port}/auth/callback"
        # Complete login with PKCE using the stored redirect_uri
        try:
            data = ms_account.complete_login(settings.ms_client_id, None, redirect_uri, code, AUTH_FLOW.get("code_verifier"))
        except Exception as e:
            # Map known library exceptions to friendly messages
            if mll_exc:
                if isinstance(e, getattr(mll_exc, "AzureAppNotPermitted", tuple())):
                    msg = (
                        "Your Azure App is not permitted to use the Minecraft API yet. "
                        "Submit the permission form referenced in the minecraft-launcher-lib docs, then retry."
                    )
                    return HTMLResponse(f'<div class="text-rose-400 p-4">Login failed: {msg}</div>', status_code=400)
                if isinstance(e, getattr(mll_exc, "AccountNotOwnMinecraft", tuple())):
                    msg = "This Microsoft account does not own Minecraft. Please use an account that owns the game."
                    return HTMLResponse(f'<div class="text-rose-400 p-4">Login failed: {msg}</div>', status_code=400)
                if isinstance(e, getattr(mll_exc, "InvalidRefreshToken", tuple())):
                    msg = "Invalid or expired refresh token. Please sign in again."
                    return HTMLResponse(f'<div class="text-rose-400 p-4">Login failed: {msg}</div>', status_code=400)
            # Re-raise to be handled by generic error processing below
            raise
        # Validate response contains tokens
        if not isinstance(data, dict) or not data.get("access_token") or not data.get("refresh_token"):
            raise KeyError("access_token")
        # Persist refresh token + profile (encrypted)
        if isinstance(data, dict):
            payload = {
                "refresh_token": data.get("refresh_token"),
                "access_token": data.get("access_token"),
                "name": data.get("name"),
                "id": data.get("id"),
            }
            _save_auth_payload(payload)
        # Clear flow state
        AUTH_FLOW["state"] = None
        AUTH_FLOW["code_verifier"] = None
        AUTH_FLOW["redirect_uri"] = None
        # Friendly success page (user is in system browser)
        html = (
            '<div class="max-w-xl mx-auto mt-10 p-6 rounded border border-slate-300">'
            '<div class="text-xl font-semibold">Signed in successfully</div>'
            '<div class="mt-2 text-slate-700">You can now return to Cottage Launcher. This window can be closed.</div>'
            '<div class="mt-4"><a href="/settings" class="px-3 py-1.5 rounded bg-emerald-600 text-white">Go to Settings</a></div>'
            '</div>'
        )
        return HTMLResponse(html)
    except KeyError:
        # Common case: access_token missing if Azure app isn't permitted for Minecraft API (or not public client)
        try:
            print("[Auth] Callback access_token missing; ensure Azure app is public client and approved for Minecraft API.")
        except Exception:
            pass
        msg = (
            "Your Azure application could not obtain an access token. "
            f"Ensure it is configured as a Public client with redirect http://localhost:{settings.app_port}/auth/callback "
            "and that it has been granted access to the Minecraft API."
        )
        return HTMLResponse(
            f'<div class="text-rose-400 p-4">Login failed: {msg}<div class="mt-3"><a class="underline" href="/auth/login">Try again</a></div></div>',
            status_code=400,
        )
    except Exception as e:
        # Log server-side for diagnostics
        try:
            print("[Auth] Callback error:", repr(e))
            if settings.dev_mode:
                print("[Auth] Stored state:", AUTH_FLOW)
                print("[Auth] Callback params:", dict(request.query_params))
        except Exception:
            pass
        return HTMLResponse(f'<div class="text-rose-400 p-4">Login failed: {e}<div class="mt-3"><a class="underline" href="/auth/login">Try again</a></div></div>', status_code=400)

@router.get("/auth/status")
async def auth_status():
    settings = get_settings()
    status = _auth_status(settings)
    return JSONResponse(status)

@router.post("/auth/logout", response_class=HTMLResponse)
async def auth_logout():
    settings = get_settings()
    _delete_auth_payload()
    status = _auth_status(settings)
    return HTMLResponse(_render_account_card_html(status))

@router.get("/auth/banner", response_class=HTMLResponse)
async def auth_banner():
    settings = get_settings()
    status = _auth_status(settings)
    if status.get("logged_in"):
        return HTMLResponse("")
    # Dismissible top banner
    html = (
        '<div class="bg-amber-500/20 border-b border-amber-600/40 text-amber-200 text-sm">'
        '<div class="max-w-7xl mx-auto px-4 py-2 flex items-center justify-between">'
        '<div>Not signed in. Some features may be limited. Please sign in with your Microsoft account.</div>'
        '<a href="/auth/login" class="px-2.5 py-1 rounded bg-amber-600 hover:bg-amber-500 text-white">Sign in</a>'
        '</div>'
        '</div>'
    )
    return HTMLResponse(html)

@router.get("/auth/popup", response_class=HTMLResponse)
async def auth_popup():
    settings = get_settings()
    status = _auth_status(settings)
    if status.get("logged_in"):
        return HTMLResponse("")
    html = (
        '<div class="fixed inset-0 z-50 flex items-center justify-center">'
        '  <div class="absolute inset-0 bg-black/60" onclick="this.parentElement.remove()"></div>'
        '  <div class="relative bg-slate-800 border border-slate-700 rounded-lg p-5 w-[28rem] shadow-xl">'
        '    <div class="text-lg font-semibold">Sign in required</div>'
        '    <div class="mt-2 text-sm text-slate-300">To download and launch modpacks, please sign in with your Microsoft account that owns Minecraft.</div>'
        '    <div class="mt-4 flex items-center justify-end gap-2">'
        '      <button class="text-xs px-2 py-1 rounded border border-slate-600 hover:bg-slate-700" onclick="document.getElementById(\'auth-modal\').remove()">Later</button>'
        '      <a href="/auth/login" class="px-3 py-1.5 rounded bg-emerald-600 hover:bg-emerald-500 text-white">Sign in</a>'
        '    </div>'
        '  </div>'
        '</div>'
    )
    return HTMLResponse(html)

@router.get("/settings/account-panel", response_class=HTMLResponse)
async def settings_account_panel():
    settings = get_settings()
    status = _auth_status(settings)
    return HTMLResponse(_render_account_card_html(status))
