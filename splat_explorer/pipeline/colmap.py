"""Camera pose estimation with COLMAP (stage 'colmap'). CPU only - no CUDA.

Order of operations:
  1. feature_extractor     SIFT, one shared camera (a video = one lens), OPENCV model
  2. sequential_matcher    video frames are ordered, so match neighbours only
  3. global_mapper         COLMAP 4.x (GLOMAP merged in); falls back to `mapper`
  4. pick the largest      COLMAP numbers models arbitrarily; sparse/0 is often a fragment
  5. image_undistorter     -> dataset/ with PINHOLE cameras, which Brush trains on

Option names changed between COLMAP versions (SiftExtraction.* -> FeatureExtraction.*),
so flags are chosen by reading each command's `-h` output at runtime.
"""

from __future__ import annotations

import math
import shutil
import struct
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Callable

from ..config import Settings
from .tools import CancelCheck, ToolError, capture, log_line, run


# ---------------------------------------------------------------- images.bin --

@dataclass
class ColmapImage:
    image_id: int
    qvec: tuple[float, float, float, float]  # w, x, y, z  (world -> camera)
    tvec: tuple[float, float, float]
    camera_id: int
    name: str

    def rotation(self) -> list[list[float]]:
        w, x, y, z = self.qvec
        n = math.sqrt(w * w + x * x + y * y + z * z) or 1.0
        w, x, y, z = w / n, x / n, y / n, z / n
        return [
            [1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
            [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
            [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)],
        ]

    def _rt_col(self, col: tuple[float, float, float]) -> list[float]:
        """R^T @ col  (camera axis expressed in world coordinates)."""
        r = self.rotation()
        return [sum(r[k][i] * col[k] for k in range(3)) for i in range(3)]

    def center(self) -> list[float]:
        """Camera centre in world coordinates: C = -R^T t."""
        rt_t = self._rt_col(self.tvec)
        return [-v for v in rt_t]

    def forward(self) -> list[float]:
        return self._rt_col((0.0, 0.0, 1.0))  # COLMAP camera looks along +Z

    def up(self) -> list[float]:
        return [-v for v in self._rt_col((0.0, 1.0, 0.0))]  # COLMAP camera +Y points down


def count_registered(images_bin: Path) -> int:
    try:
        with open(images_bin, "rb") as fh:
            data = fh.read(8)
        return struct.unpack("<Q", data)[0] if len(data) == 8 else 0
    except OSError:
        return 0


def read_images_bin(path: Path) -> list[ColmapImage]:
    images = []
    with open(path, "rb") as fh:
        (num,) = struct.unpack("<Q", fh.read(8))
        for _ in range(num):
            image_id, = struct.unpack("<i", fh.read(4))
            qvec = struct.unpack("<4d", fh.read(32))
            tvec = struct.unpack("<3d", fh.read(24))
            camera_id, = struct.unpack("<i", fh.read(4))
            name = bytearray()
            while True:
                ch = fh.read(1)
                if not ch:
                    raise ValueError("Truncated images.bin")
                if ch == b"\x00":
                    break
                name += ch
            num_points, = struct.unpack("<Q", fh.read(8))
            fh.seek(24 * num_points, 1)
            images.append(ColmapImage(image_id, qvec, tvec, camera_id, name.decode("utf-8")))
    return images


def find_largest_model(sparse_root: Path) -> tuple[Path | None, int, int]:
    """Return (model_dir, registered_images, number_of_models)."""
    models = [p.parent for p in sparse_root.glob("**/images.bin")]
    if not models:
        return None, 0, 0
    scored = sorted(((count_registered(m / "images.bin"), str(m)) for m in models), reverse=True)
    best_count, best_dir = scored[0]
    return Path(best_dir), best_count, len(models)


# ------------------------------------------------------------ flag discovery --

@lru_cache(maxsize=32)
def _help(colmap_bin: str, command: str) -> str:
    return capture([colmap_bin, command, "-h"], timeout=60)


@lru_cache(maxsize=4)
def _commands(colmap_bin: str) -> str:
    return capture([colmap_bin, "help"], timeout=60)


def pick_option(help_text: str, *candidates: str) -> str | None:
    for opt in candidates:
        if f"--{opt}" in help_text:
            return opt
    return None


def has_global_mapper(colmap_bin: str) -> bool:
    return "global_mapper" in _commands(colmap_bin)


# ------------------------------------------------------------------ the stage --

@dataclass
class ColmapResult:
    dataset_dir: Path
    registered: int
    total: int
    models: int
    mapper: str


def run_colmap(images_dir: Path, work_dir: Path, dataset_dir: Path, settings: Settings,
               log_path: Path, cancel_check: CancelCheck,
               on_progress: Callable[[float, str], None]) -> ColmapResult:
    colmap = settings.colmap_bin
    colmap_work = work_dir / "colmap_work"
    if colmap_work.exists():
        shutil.rmtree(colmap_work)
    sparse_root = colmap_work / "sparse"
    sparse_root.mkdir(parents=True)
    db = colmap_work / "database.db"
    total = len(list(images_dir.glob("*.jpg")))

    # 1. features ------------------------------------------------------------
    on_progress(0.02, f"Detecting features in {total} frames (CPU)")
    fe_help = _help(colmap, "feature_extractor")
    cmd = [colmap, "feature_extractor", "--database_path", db, "--image_path", images_dir,
           "--ImageReader.single_camera", "1", "--ImageReader.camera_model", "OPENCV"]
    gpu_opt = pick_option(fe_help, "FeatureExtraction.use_gpu", "SiftExtraction.use_gpu")
    if gpu_opt:
        cmd += [f"--{gpu_opt}", "0"]  # no CUDA on a Mac
    run(cmd, log_path, cancel_check=cancel_check, description="SIFT features")

    # 2. matching ------------------------------------------------------------
    on_progress(0.25, "Matching neighbouring frames")
    sm_help = _help(colmap, "sequential_matcher")
    cmd = [colmap, "sequential_matcher", "--database_path", db]
    gpu_opt = pick_option(sm_help, "FeatureMatching.use_gpu", "SiftMatching.use_gpu")
    if gpu_opt:
        cmd += [f"--{gpu_opt}", "0"]
    overlap_opt = pick_option(sm_help, "SequentialMatching.overlap")
    if overlap_opt:
        cmd += [f"--{overlap_opt}", str(settings.colmap_matcher_overlap)]
    run(cmd, log_path, cancel_check=cancel_check, description="sequential matching")

    # 3. mapping -------------------------------------------------------------
    mapper_used = "mapper"
    if settings.use_global_mapper and has_global_mapper(colmap):
        on_progress(0.45, "Solving camera poses (global mapper)")
        try:
            run([colmap, "global_mapper", "--database_path", db, "--image_path", images_dir,
                 "--output_path", sparse_root], log_path, cancel_check=cancel_check,
                description="global SfM")
            mapper_used = "global_mapper"
        except ToolError as exc:
            log_line(log_path, f"global_mapper failed ({exc}); falling back to incremental mapper")
            shutil.rmtree(sparse_root, ignore_errors=True)
            sparse_root.mkdir(parents=True)
    if mapper_used == "mapper":
        on_progress(0.45, "Solving camera poses (incremental mapper, slower)")
        run([colmap, "mapper", "--database_path", db, "--image_path", images_dir,
             "--output_path", sparse_root], log_path, cancel_check=cancel_check,
            description="incremental SfM")

    # 4. pick the largest model ----------------------------------------------
    best, registered, models = find_largest_model(sparse_root)
    if best is None or registered == 0:
        raise ToolError("COLMAP could not reconstruct any camera poses. The video is probably "
                        "too blurry, too dark, or rotates in place without moving. Re-record "
                        "following the capture tips.")
    log_line(log_path, f"Largest model: {best} with {registered}/{total} frames "
                       f"({models} model(s) found)")
    ratio = registered / total if total else 0.0
    if registered < settings.min_registered_images or ratio < settings.min_registered_ratio:
        raise ToolError(f"Only {registered} of {total} frames ({ratio:.0%}) could be placed. "
                        "The capture has gaps or too much blur; re-record moving slowly with "
                        "more overlap.")

    # 5. undistort into the training dataset ---------------------------------
    on_progress(0.85, "Undistorting images for training")
    if dataset_dir.exists():
        shutil.rmtree(dataset_dir)
    run([colmap, "image_undistorter", "--image_path", images_dir, "--input_path", best,
         "--output_path", dataset_dir, "--output_type", "COLMAP"],
        log_path, cancel_check=cancel_check, description="undistort to PINHOLE")
    # Remove MVS leftovers so the trainer only sees images/ and sparse/.
    shutil.rmtree(dataset_dir / "stereo", ignore_errors=True)
    for script in dataset_dir.glob("run-colmap-*.sh"):
        script.unlink(missing_ok=True)

    if not settings.keep_intermediates:
        db.unlink(missing_ok=True)

    on_progress(1.0, f"Placed {registered} of {total} frames")
    return ColmapResult(dataset_dir, registered, total, models, mapper_used)


def camera_summary(dataset_dir: Path) -> dict:
    """Camera path summary in COLMAP world coordinates, used by the Unity viewer
    to orient the scene (average camera 'up' -> world up) and place the start view."""
    images_bin = next(iter(sorted(dataset_dir.glob("**/images.bin"))), None)
    if images_bin is None:
        return {}
    images = sorted(read_images_bin(images_bin), key=lambda im: im.name)
    if not images:
        return {}
    centers = [im.center() for im in images]
    ups = [im.up() for im in images]
    avg_up = [sum(u[i] for u in ups) / len(ups) for i in range(3)]
    norm = math.sqrt(sum(v * v for v in avg_up)) or 1.0
    avg_up = [v / norm for v in avg_up]
    mins = [min(c[i] for c in centers) for i in range(3)]
    maxs = [max(c[i] for c in centers) for i in range(3)]
    extent = math.sqrt(sum((maxs[i] - mins[i]) ** 2 for i in range(3)))
    first = images[0]
    return {
        "coordinate_frame": "COLMAP (x right, y down, z forward)",
        "camera_count": len(images),
        "start": {"position": first.center(), "forward": first.forward(), "up": first.up()},
        "average_up": avg_up,
        "camera_bounds_min": mins,
        "camera_bounds_max": maxs,
        "camera_path_extent": extent,
        # Flat [x0, y0, z0, x1, ...] so Unity's JsonUtility can read it.
        "camera_path_flat": [v for c in centers[:: max(1, len(centers) // 64)] for v in c],
    }
