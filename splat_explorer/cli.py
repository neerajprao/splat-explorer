"""Command-line entry point: process one video without the web app.

    python -m splat_explorer.cli path/to/room.mp4 --name "My room" --quality preview

The result appears in the web app and the Unity viewer like any uploaded job.
"""

from __future__ import annotations

import argparse
import shutil
import sys
import threading
import time
from pathlib import Path

from .config import QUALITY_PRESETS, get_settings
from .db import JobStore
from .pipeline import probe as probe_stage
from .pipeline.runner import run_job


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Turn a video into a Gaussian splat scene")
    parser.add_argument("video", type=Path)
    parser.add_argument("--name", default="")
    parser.add_argument("--quality", choices=list(QUALITY_PRESETS), default=None)
    args = parser.parse_args(argv)

    settings = get_settings()
    store = JobStore(settings.db_path)
    video: Path = args.video.expanduser().resolve()
    if not video.is_file():
        print(f"Not a file: {video}", file=sys.stderr)
        return 2
    try:
        info = probe_stage.probe_video(video, settings)
        warnings = probe_stage.validate(info, settings)
    except probe_stage.InvalidVideo as exc:
        print(f"Invalid video: {exc}", file=sys.stderr)
        return 2

    job = store.create(args.name or video.stem, args.quality or settings.default_quality,
                       video_filename=video.name, video_info=info.to_dict(), status="running")
    for w in warnings:
        print(f"warning: {w}")
    job_dir = settings.job_dir(job["id"])
    job_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(video, job_dir / f"input{video.suffix.lower()}")
    print(f"Job {job['id']}  ->  {job_dir}")
    print(f"Log: {job_dir / 'job.log'}")

    done = threading.Event()

    def report() -> None:
        last = None
        while not done.is_set():
            j = store.get(job["id"]) or {}
            line = f"[{(j.get('progress') or 0) * 100:5.1f}%] {j.get('stage') or ''}: {j.get('message') or ''}"
            if line != last:
                print(line, flush=True)
                last = line
            time.sleep(2)

    t = threading.Thread(target=report, daemon=True)
    t.start()
    started = time.time()
    try:
        ok = run_job(job["id"], store, settings)
    except KeyboardInterrupt:
        store.update(job["id"], status="cancelled", message="Interrupted")
        ok = False
    done.set()
    t.join(timeout=3)
    final = store.get(job["id"]) or {}
    mins = (time.time() - started) / 60
    if ok:
        print(f"\nDone in {mins:.1f} min: {job_dir / 'scene' / 'scene.ply'}")
    else:
        print(f"\n{final.get('status')} after {mins:.1f} min: {final.get('error') or ''}",
              file=sys.stderr)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
