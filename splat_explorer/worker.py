"""Background worker: takes queued jobs one at a time and runs the pipeline.

Run with:  python -m splat_explorer.worker
(`scripts/start.sh` starts it for you, wrapped in `caffeinate` so the Mac
does not sleep in the middle of a long training run.)
"""

from __future__ import annotations

import argparse
import logging
import signal
import sys
import time

from .config import get_settings
from .db import JobStore
from .pipeline.runner import run_job

log = logging.getLogger("splat_explorer.worker")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Splat Explorer reconstruction worker")
    parser.add_argument("--once", action="store_true", help="process at most one job and exit")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s worker %(message)s",
                        datefmt="%H:%M:%S")
    settings = get_settings()
    store = JobStore(settings.db_path)
    resumed = store.requeue_interrupted()
    if resumed:
        log.info("Re-queued %d job(s) interrupted by a previous shutdown", resumed)
    log.info("Data dir: %s", settings.data_dir)
    log.info("Tools: ffmpeg=%s colmap=%s brush=%s",
             settings.ffmpeg_bin, settings.colmap_bin, settings.brush_bin)

    stopping = False

    def stop(*_: object) -> None:
        nonlocal stopping
        stopping = True
        log.info("Stopping after the current job (Ctrl+C again to force)")
        signal.signal(signal.SIGINT, signal.SIG_DFL)

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)

    log.info("Waiting for jobs...")
    while not stopping:
        job = store.claim_next()
        if job is None:
            if args.once:
                return 0
            time.sleep(settings.worker_poll_s)
            continue
        log.info("Job %s '%s' started", job["id"], job["name"])
        started = time.time()
        ok = run_job(job["id"], store, settings)
        final = store.get(job["id"]) or {}
        log.info("Job %s %s in %.1f min - %s", job["id"], final.get("status"),
                 (time.time() - started) / 60, final.get("error") or final.get("message"))
        if args.once:
            return 0 if ok else 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
