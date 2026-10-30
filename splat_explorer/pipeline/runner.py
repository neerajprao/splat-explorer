"""Runs one job through all stages, resumably.

Each finished stage writes `.done_<stage>` in the job folder; a retried job
skips those stages. Progress shown in the UI is the stage's share of the whole
pipeline plus progress within the stage.
"""

from __future__ import annotations

import json
import shutil
import traceback
from pathlib import Path

from ..config import Settings
from ..db import JobStore
from . import colmap as colmap_stage
from . import export as export_stage
from . import frames as frames_stage
from . import probe as probe_stage
from . import train as train_stage
from .tools import JobCancelled, ToolError, log_line

# Rough share of total wall time per stage on an M-series Mac.
STAGE_WEIGHTS = {"probe": 0.01, "frames": 0.04, "colmap": 0.25, "train": 0.68, "export": 0.02}
STAGE_ORDER = list(STAGE_WEIGHTS)


class JobPaths:
    def __init__(self, job_dir: Path):
        self.root = job_dir
        self.log = job_dir / "job.log"
        self.images = job_dir / "images"
        self.dataset = job_dir / "dataset"
        self.results = job_dir / "results"
        self.scene = job_dir / "scene"
        self.probe_json = job_dir / "probe.json"

    def video(self) -> Path | None:
        for p in sorted(self.root.glob("input.*")):
            return p
        return None

    def done_marker(self, stage: str) -> Path:
        return self.root / f".done_{stage}"

    def is_done(self, stage: str) -> bool:
        return self.done_marker(stage).exists()

    def mark_done(self, stage: str) -> None:
        self.done_marker(stage).write_text("ok\n")

    def clear_from(self, stage: str) -> None:
        """Invalidate `stage` and every later stage (used when re-running)."""
        for s in STAGE_ORDER[STAGE_ORDER.index(stage):]:
            self.done_marker(s).unlink(missing_ok=True)


def overall_progress(stage: str, within: float) -> float:
    before = sum(STAGE_WEIGHTS[s] for s in STAGE_ORDER[: STAGE_ORDER.index(stage)])
    return round(min(1.0, before + STAGE_WEIGHTS[stage] * max(0.0, min(1.0, within))), 4)


def run_job(job_id: str, store: JobStore, settings: Settings) -> bool:
    """Run every unfinished stage. Returns True on success."""
    job = store.get(job_id)
    if job is None:
        return False
    paths = JobPaths(settings.job_dir(job_id))
    paths.root.mkdir(parents=True, exist_ok=True)
    log_line(paths.log, f"=== Job {job_id} '{job['name']}' (quality: {job['quality']}) ===")

    def cancelled() -> bool:
        current = store.get(job_id)
        return bool(current and current.get("cancel_requested"))

    def progress_for(stage: str):
        def report(within: float, message: str) -> None:
            store.update(job_id, stage=stage, progress=overall_progress(stage, within),
                         message=message)
        return report

    stage = "probe"
    try:
        video = paths.video()
        if video is None:
            raise ToolError("The uploaded video is missing from the job folder.")

        # probe -------------------------------------------------------------
        stage = "probe"
        if not paths.is_done(stage):
            progress_for(stage)(0.0, "Checking the video")
            info = probe_stage.probe_video(video, settings)
            for w in probe_stage.validate(info, settings):
                store.add_warning(job_id, w)
            paths.probe_json.write_text(json.dumps(info.to_dict(), indent=2))
            store.update(job_id, video_info=info.to_dict())
            paths.mark_done(stage)
        info = probe_stage.VideoInfo(**{k: v for k, v in json.loads(paths.probe_json.read_text())
                                        .items() if k in probe_stage.VideoInfo.__annotations__})

        # frames ------------------------------------------------------------
        stage = "frames"
        if not paths.is_done(stage):
            paths.clear_from(stage)
            kept = frames_stage.extract_frames(video, info, paths.root, paths.images, settings,
                                               paths.log, cancelled, progress_for(stage))
            store.update(job_id, frames_total=kept)
            paths.mark_done(stage)

        # colmap ------------------------------------------------------------
        stage = "colmap"
        if not paths.is_done(stage):
            paths.clear_from(stage)
            result = colmap_stage.run_colmap(paths.images, paths.root, paths.dataset, settings,
                                             paths.log, cancelled, progress_for(stage))
            store.update(job_id, frames_total=result.total, frames_registered=result.registered)
            ratio = result.registered / result.total if result.total else 0
            if ratio < settings.warn_registered_ratio:
                store.add_warning(job_id, f"Only {ratio:.0%} of frames were placed; parts of the "
                                          "space may be missing or blurry.")
            if result.models > 1:
                store.add_warning(job_id, f"COLMAP split the capture into {result.models} pieces; "
                                          "only the largest is used. The video probably has a gap.")
            paths.mark_done(stage)

        # train -------------------------------------------------------------
        stage = "train"
        current = store.get(job_id) or job
        if not paths.is_done(stage):
            paths.clear_from(stage)
            train_stage.train(paths.dataset, paths.results, settings, current["quality"],
                              paths.log, cancelled, progress_for(stage))
            paths.mark_done(stage)

        # export ------------------------------------------------------------
        stage = "export"
        plys = sorted(paths.results.glob("scene_*.ply"))
        if not plys:
            raise ToolError("No trained splat found; re-run training.")
        progress_for(stage)(0.1, "Writing scene files")
        meta = export_stage.export_scene(job_id, current["name"], plys[-1], paths.dataset,
                                         paths.images, paths.scene, current["quality"])
        paths.mark_done(stage)
        if not settings.keep_intermediates:
            shutil.rmtree(paths.results, ignore_errors=True)

        store.update(job_id, status="done", stage="export", progress=1.0,
                     splat_count=meta["splat_count"], finished_at=_now(),
                     message=f"Ready - {meta['splat_count']:,} splats")
        log_line(paths.log, "=== Done ===")
        return True

    except JobCancelled:
        log_line(paths.log, f"Cancelled during stage '{stage}'")
        store.update(job_id, status="cancelled", message=f"Cancelled during {stage}",
                     cancel_requested=False, finished_at=_now())
        return False
    except (ToolError, probe_stage.InvalidVideo, ValueError, RuntimeError, OSError) as exc:
        tail = getattr(exc, "tail", "")
        log_line(paths.log, f"FAILED in stage '{stage}': {exc}")
        if tail:
            log_line(paths.log, "Last tool output:\n" + tail)
        store.update(job_id, status="failed", error=str(exc), message=f"Failed during {stage}",
                     finished_at=_now())
        return False
    except Exception as exc:  # unexpected bug: keep the traceback in the log
        log_line(paths.log, f"UNEXPECTED ERROR in stage '{stage}':\n{traceback.format_exc()}")
        store.update(job_id, status="failed", error=f"Unexpected error: {exc}",
                     message=f"Failed during {stage}", finished_at=_now())
        return False


def _now() -> float:
    import time

    return time.time()
