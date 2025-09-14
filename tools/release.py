#!/usr/bin/env python3
"""
Release packager for Cottage Launcher (Linux).

This script is for developers only. It builds:
- Backend + wrapper as a single binary using PyInstaller
- Electron desktop front-end as an AppImage via electron-builder
- Assembles a release/ folder with a launcher script and SHA256 checksums

Usage:
  python tools/release.py

Options:
  --skip-electron        Skip building the Electron AppImage
  --skip-backend         Skip building the backend binary
  --output-dir DIR       Directory to place final release artifacts (default: release/)
  --version VERSION      Override version (default read from desktop/electron/package.json)

Requirements:
  - Linux (this script exits on non-Linux)
  - Python 3.9+
  - Node.js + npm
  - Internet access to install dev tools if missing (PyInstaller/electron-builder)
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path
import hashlib
import platform

ROOT = Path(__file__).resolve().parents[1]
ELECTRON_DIR = ROOT / "desktop" / "electron"
DIST_DIR = ROOT / "dist"
BUILD_DIR = ROOT / "build"
RELEASE_DIR = ROOT / "release"


def run(cmd: list[str], cwd: Path | None = None, env: dict | None = None) -> None:
    print(f"[release] $ {' '.join(cmd)} (cwd={cwd or Path.cwd()})")
    subprocess.check_call(cmd, cwd=str(cwd) if cwd else None, env=env or os.environ.copy())


def ensure_pyinstaller() -> None:
    try:
        import PyInstaller  # noqa: F401
        return
    except Exception:
        pass
    print("[release] Installing PyInstaller…")
    run([sys.executable, "-m", "pip", "install", "pyinstaller==6.10.0"])  # pinned for reproducibility


def read_version(override: str | None) -> str:
    if override:
        return override
    pkg = ELECTRON_DIR / "package.json"
    data = json.loads(pkg.read_text("utf-8"))
    return data.get("version", "0.0.0")


def build_electron_appimage() -> Path:
    # Ensure dependencies present
    print("[release] Building Electron AppImage…")
    # Install node deps if needed
    node_modules = ELECTRON_DIR / "node_modules"
    if not node_modules.exists():
        run(["npm", "install"], cwd=ELECTRON_DIR)
    # Ensure electron-builder installed
    pkg = json.loads((ELECTRON_DIR / "package.json").read_text("utf-8"))
    dev_deps = pkg.get("devDependencies", {})
    if "electron-builder" not in dev_deps:
        run(["npm", "install", "-D", "electron-builder@^24.13.3"], cwd=ELECTRON_DIR)
    # Build AppImage
    run(["npm", "run", "build:linux"], cwd=ELECTRON_DIR)
    dist = ELECTRON_DIR / "dist"
    candidates = sorted(dist.glob("*.AppImage"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not candidates:
        raise RuntimeError("electron-builder did not produce an AppImage in desktop/electron/dist/")
    return candidates[0]


def build_backend_binary() -> Path:
    print("[release] Building backend wrapper binary with PyInstaller…")
    ensure_pyinstaller()
    # Clean old builds to avoid confusion
    if DIST_DIR.exists():
        shutil.rmtree(DIST_DIR)
    if BUILD_DIR.exists():
        shutil.rmtree(BUILD_DIR)
    # Package desktop/wrapper.py as single binary and include templates/static
    cmd = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--onefile",
        "--name",
        "cottage-launcher",
        "--add-data",
        f"app/templates{os.pathsep}app/templates",
        "--add-data",
        f"app/static{os.pathsep}app/static",
        str(ROOT / "desktop" / "wrapper.py"),
    ]
    run(cmd, cwd=ROOT)
    exe = DIST_DIR / "cottage-launcher"
    if not exe.exists():
        # On Windows it would be .exe, but we're on Linux only per README
        raise RuntimeError("PyInstaller did not produce dist/cottage-launcher")
    # Ensure executable bit
    exe.chmod(exe.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return exe


def write_launcher_script(dst_dir: Path, appimage_name: str) -> Path:
    script = dst_dir / "run.sh"
    script.write_text(
        f"""#!/usr/bin/env bash
set -euo pipefail
DIR="$(cd "$(dirname "${{BASH_SOURCE[0]}}")" && pwd)"
# Ensure binaries are executable
chmod +x "$DIR/cottage-launcher" || true
chmod +x "$DIR/{appimage_name}" || true
# Launch backend + Electron
"$DIR/cottage-launcher" --electron-binary "$DIR/{appimage_name}"
""",
        encoding="utf-8",
    )
    script.chmod(script.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return script


def write_release_readme(dst_dir: Path, version: str) -> Path:
    """Write a README into the release directory with launch instructions.

    Explains that the AppImage (Electron frontend) and the backend executable are
    not independent and must be launched together via the provided run.sh script.
    """
    readme = dst_dir / "README.txt"
    readme.write_text(
        (
            "Cottage Launcher\n"
            f"Version: {version}\n\n"
            "This directory contains the Linux release bundle.\n\n"
            "Contents:\n"
            "  - run.sh                 -> Start the backend and the Electron AppImage together (recommended)\n"
            "  - cottage-launcher       -> Backend server wrapper only (FastAPI + Uvicorn)\n"
            "  - CottageLauncher-<ver>.AppImage -> Electron frontend only\n\n"
            "How to run (recommended):\n"
            "  1) Double-click or execute ./run.sh\n"
            "     This starts the backend and launches the Electron AppImage with the correct\n"
            "     environment so the frontend can talk to the backend.\n\n"
            "Important:\n"
            "  - The AppImage and backend are NOT independent.\n"
            "    Running the AppImage directly usually will not work, because it expects\n"
            "    the BACKEND_URL environment to be set by run.sh.\n"
            "  - Running the backend (cottage-launcher) alone only starts the local web server;\n"
            "    it does not include the desktop UI unless you open the URL in a browser yourself.\n\n"
            "Advanced usage (optional):\n"
            "  - To use a system browser instead of Electron: ./cottage-launcher --frontend browser\n"
            "  - To point the AppImage to an already-running backend: set BACKEND_URL, e.g.\n"
            "      BACKEND_URL=http://127.0.0.1:8000 ./CottageLauncher-<ver>.AppImage\n"
            "\n"
            "If you encounter issues, run from a terminal to see logs.\n"
        ),
        encoding="utf-8",
    )
    return readme


def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def write_checksums(dst_dir: Path) -> Path:
    out = dst_dir / "sha256sums.txt"
    lines: list[str] = []
    for p in sorted(dst_dir.iterdir()):
        if p.is_file() and p.name != out.name:
            lines.append(f"{sha256_file(p)}  {p.name}")
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out


def make_tarball(src_dir: Path, version: str) -> Path:
    tar_name = f"CottageLauncher-{version}-linux-x64.tar.gz"
    tar_path = RELEASE_DIR / tar_name
    if not RELEASE_DIR.exists():
        RELEASE_DIR.mkdir(parents=True, exist_ok=True)
    # Create tar.gz
    run(["tar", "-czf", str(tar_path), "-C", str(src_dir.parent), src_dir.name])
    return tar_path


def main() -> None:
    # Enforce Linux-only environment
    if platform.system().lower() != "linux":
        print("[release] This release script only supports Linux. Aborting.")
        sys.exit(2)

    ap = argparse.ArgumentParser(description="Cottage Launcher release packager (Linux)")
    ap.add_argument("--skip-electron", action="store_true")
    ap.add_argument("--skip-backend", action="store_true")
    ap.add_argument("--output-dir", default=str(RELEASE_DIR))
    ap.add_argument("--version", default=None)
    args = ap.parse_args()

    version = read_version(args.version)
    print(f"[release] Version: {version}")

    appimage_path: Path | None = None
    if not args.skip_electron:
        appimage_path = build_electron_appimage()
    else:
        print("[release] Skipping Electron build as requested")

    backend_bin: Path | None = None
    if not args.skip_backend:
        backend_bin = build_backend_binary()
    else:
        print("[release] Skipping backend build as requested")

    # Assemble release directory
    out_root = Path(args.output_dir)
    out_root.mkdir(parents=True, exist_ok=True)
    bundle_dir = out_root / f"CottageLauncher-{version}-linux-x64"
    if bundle_dir.exists():
        shutil.rmtree(bundle_dir)
    bundle_dir.mkdir(parents=True, exist_ok=True)

    # Copy artifacts
    appimage_name = None
    if appimage_path:
        appimage_name = f"CottageLauncher-{version}.AppImage"
        shutil.copy2(appimage_path, bundle_dir / appimage_name)
    if backend_bin:
        shutil.copy2(backend_bin, bundle_dir / "cottage-launcher")

    # Launcher script
    if appimage_name and backend_bin:
        write_launcher_script(bundle_dir, appimage_name)

    # README with instructions about run.sh and component coupling
    write_release_readme(bundle_dir, version)

    # Checksums
    write_checksums(bundle_dir)

    # Tarball
    tarball = make_tarball(bundle_dir, version)

    print("\n[release] Done!")
    print(f"[release] Bundle dir: {bundle_dir}")
    print(f"[release] Tarball   : {tarball}")
    print("[release] Contents:")
    for p in bundle_dir.iterdir():
        print(f"  - {p.name} ({p.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
