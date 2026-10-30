#!/usr/bin/env python3
"""Checks that this Mac has everything Splat Explorer needs. Safe to run any time.

    python3 scripts/doctor.py
"""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

OK, WARN, BAD = "✅", "⚠️ ", "❌"
problems = 0


def report(status: str, label: str, detail: str = "", fix: str = "") -> None:
    global problems
    if status == BAD:
        problems += 1
    print(f"{status} {label}" + (f" - {detail}" if detail else ""))
    if fix and status != OK:
        print(f"     fix: {fix}")


def out(cmd: list[str]) -> str:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=60, errors="replace")
        return (r.stdout or "") + (r.stderr or "")
    except (OSError, subprocess.TimeoutExpired):
        return ""


def main() -> int:
    print("Splat Explorer doctor\n")

    # Machine ---------------------------------------------------------------
    system, machine = platform.system(), platform.machine()
    if system == "Darwin" and machine == "arm64":
        report(OK, "Apple Silicon Mac", f"macOS {platform.mac_ver()[0]}")
    elif system == "Darwin":
        report(BAD, "Mac is not Apple Silicon", machine,
               "This project targets M-series Macs (Metal GPU). Intel Macs are not supported.")
    else:
        report(WARN, f"Not macOS ({system} {machine})",
               "this project is built for Apple Silicon Macs only")

    if system == "Darwin":
        mem = out(["sysctl", "-n", "hw.memsize"]).strip()
        if mem.isdigit():
            gb = int(mem) / 1024**3
            report(OK if gb >= 16 else WARN, "Unified memory", f"{gb:.0f} GB",
                   "With 8 GB use quality=preview and keep scenes small.")
    free_gb = shutil.disk_usage(ROOT).free / 1024**3
    report(OK if free_gb >= 20 else WARN, "Free disk space", f"{free_gb:.0f} GB",
           "Each job needs ~3-5 GB while processing.")

    # Python ----------------------------------------------------------------
    v = sys.version_info
    report(OK if v >= (3, 10) else BAD, "Python", f"{v.major}.{v.minor}.{v.micro}",
           "Run scripts/setup_mac.sh (creates .venv with Python 3.12).")
    missing = []
    for mod in ("fastapi", "uvicorn", "multipart", "cv2", "numpy"):
        try:
            __import__(mod)
        except ImportError:
            missing.append(mod)
    report(OK if not missing else BAD, "Python packages",
           "all installed" if not missing else "missing: " + ", ".join(missing),
           "source .venv/bin/activate && pip install -r requirements.txt")

    try:
        from splat_explorer.config import get_settings
        settings = get_settings()
    except Exception as exc:  # pragma: no cover
        report(BAD, "Load settings", str(exc))
        return 1

    # FFmpeg ----------------------------------------------------------------
    for name, binary in (("ffmpeg", settings.ffmpeg_bin), ("ffprobe", settings.ffprobe_bin)):
        found = shutil.which(binary) or (binary if Path(binary).is_file() else None)
        if found:
            first = out([binary, "-version"]).splitlines()[:1]
            report(OK, name, first[0] if first else found)
        else:
            report(BAD, name, "not found", "brew install ffmpeg")

    # COLMAP ----------------------------------------------------------------
    colmap = settings.colmap_bin
    if shutil.which(colmap) or Path(colmap).is_file():
        helptext = out([colmap, "help"])
        version = next((l.strip() for l in helptext.splitlines() if "COLMAP" in l), "found")
        report(OK, "COLMAP", version)
        if "global_mapper" in helptext:
            report(OK, "COLMAP global mapper", "available (COLMAP 4.x)")
        else:
            report(WARN, "COLMAP global mapper", "not available - the slower incremental mapper "
                   "will be used", "brew upgrade colmap (needs COLMAP 4.0+)")
        if "cuda" in helptext.lower() and "without cuda" not in helptext.lower():
            report(WARN, "COLMAP build mentions CUDA", "fine - the pipeline forces CPU mode")
    else:
        report(BAD, "COLMAP", "not found", "brew install colmap")

    # Brush -----------------------------------------------------------------
    brush = settings.brush_bin
    if shutil.which(brush) or Path(brush).is_file():
        helptext = out([brush, "--help"])
        if "--total-train-iters" in helptext or "--total-steps" in helptext:
            report(OK, "Brush", brush)
        else:
            report(WARN, "Brush", f"{brush} ran but --help looks unexpected",
                   "Rebuild with scripts/setup_mac.sh")
    else:
        report(BAD, "Brush", "not built", "scripts/setup_mac.sh (needs Rust; builds tools/brush)")

    # Data dir --------------------------------------------------------------
    try:
        probe = settings.data_dir / ".write_test"
        probe.write_text("ok")
        probe.unlink()
        report(OK, "Data folder writable", str(settings.data_dir))
    except OSError as exc:
        report(BAD, "Data folder writable", str(exc), "Set SPLAT_DATA_DIR in .env")

    # Unity (optional at this point) ----------------------------------------
    hub = Path("/Applications/Unity Hub.app")
    report(OK if hub.exists() else WARN, "Unity Hub", "installed" if hub.exists() else "not found",
           "Install Unity Hub + a Unity 6 (6000.x) editor with 'Mac Build Support' - see AFTER_PULL.md")
    if shutil.which("git") is None:
        report(BAD, "git", "not found (Unity needs it to fetch UnitySplats)", "xcode-select --install")

    print()
    if problems:
        print(f"{problems} problem(s) found. Fix them, then run this again.")
        return 1
    print("All required tools are ready. Start the app with: scripts/start.sh")
    return 0


if __name__ == "__main__":
    os.chdir(ROOT)
    sys.exit(main())
