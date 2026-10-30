"""Gaussian Splat training with Brush (stage 'train').

Brush (https://github.com/ArthurBrussee/brush, Apache-2.0) runs on the Mac GPU
through wgpu/Metal - no CUDA. It reads the COLMAP dataset directly and writes
a standard 3DGS .ply.

Flag names are discovered from `brush --help`, because Brush renamed
`--total-steps` to `--total-train-iters` in 2026.
"""

from __future__ import annotations

import re
import shutil
from functools import lru_cache
from pathlib import Path
from typing import Callable

from ..config import Settings
from .tools import CancelCheck, ToolError, capture, run

REFINE_RE = re.compile(r"Refine iter (\d+)(?:, (\d+) splats)?")
EVAL_RE = re.compile(r"Eval iter (\d+)")


@lru_cache(maxsize=4)
def brush_help(brush_bin: str) -> str:
    return capture([brush_bin, "--help"], timeout=60)


def iters_flag(help_text: str) -> str:
    if "--total-train-iters" in help_text:
        return "--total-train-iters"
    if "--total-steps" in help_text:
        return "--total-steps"
    return "--total-train-iters"


def build_command(settings: Settings, dataset_dir: Path, results_dir: Path,
                  preset: dict[str, int]) -> list[str]:
    help_text = brush_help(settings.brush_bin)
    iters = preset["iters"]
    cmd = [settings.brush_bin, str(dataset_dir),
           iters_flag(help_text), str(iters),
           "--max-resolution", str(preset["max_resolution"]),
           "--sh-degree", str(settings.sh_degree),
           "--export-every", str(iters),
           "--export-path", str(results_dir),
           "--export-name", "scene_{iter}.ply"]
    if "--max-splats" in help_text:
        cmd += ["--max-splats", str(preset["max_splats"])]
    # No --with-viewer: when a source path is given Brush runs headless by default.
    return cmd


def train(dataset_dir: Path, results_dir: Path, settings: Settings, quality: str,
          log_path: Path, cancel_check: CancelCheck,
          on_progress: Callable[[float, str], None]) -> Path:
    preset = settings.presets.get(quality) or settings.presets["standard"]
    if results_dir.exists():
        shutil.rmtree(results_dir)
    results_dir.mkdir(parents=True)
    iters = preset["iters"]
    cmd = build_command(settings, dataset_dir, results_dir, preset)
    on_progress(0.01, f"Training on the GPU (Metal), {iters:,} iterations")

    def on_line(line: str) -> None:
        m = REFINE_RE.search(line) or EVAL_RE.search(line)
        if m:
            it = int(m.group(1))
            splats = m.group(2) if m.re is REFINE_RE and m.lastindex and m.group(2) else None
            msg = f"Iteration {it:,} / {iters:,}"
            if splats:
                msg += f" - {int(splats):,} splats"
            on_progress(min(0.99, it / iters), msg)

    run(cmd, log_path, cancel_check=cancel_check, on_line=on_line,
        env={"RUST_LOG": "info", "RUST_BACKTRACE": "1"}, description="Brush training")

    plys = sorted(results_dir.glob("scene_*.ply"))
    if not plys:
        raise ToolError("Brush finished but wrote no .ply file. See the job log.")
    on_progress(1.0, "Training finished")
    return plys[-1]
