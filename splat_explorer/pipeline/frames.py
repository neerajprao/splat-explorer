"""Frame extraction (stage 'frames').

FFmpeg extracts frames at `target_fps * sharp_group`; then, from every group of
`sharp_group` consecutive frames, only the sharpest (highest variance of the
Laplacian) is kept. Motion-blurred frames are the main cause of failed
reconstructions, so this matters more than any training setting.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Callable

from ..config import Settings
from .probe import VideoInfo
from .tools import CancelCheck, run


def plan_extraction(duration_s: float, target_fps: float, max_frames: int,
                    sharp_group: int) -> tuple[float, int]:
    """Return (ffmpeg fps, expected number of kept frames)."""
    wanted = max(1, min(max_frames, int(round(duration_s * target_fps))))
    keep_fps = wanted / duration_s if duration_s > 0 else target_fps
    return keep_fps * max(1, sharp_group), wanted


def sharpness(image_path: Path) -> float:
    import cv2  # local import keeps the API process light

    img = cv2.imread(str(image_path), cv2.IMREAD_GRAYSCALE)
    if img is None:
        return -1.0
    h, w = img.shape[:2]
    scale = 640.0 / max(h, w)
    if scale < 1.0:
        img = cv2.resize(img, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA)
    return float(cv2.Laplacian(img, cv2.CV_64F).var())


def select_sharpest(raw_frames: list[Path], group: int,
                    score: Callable[[Path], float] = sharpness) -> list[Path]:
    """Keep the best-scoring frame of each consecutive group."""
    if group <= 1:
        return list(raw_frames)
    kept = []
    for i in range(0, len(raw_frames), group):
        chunk = raw_frames[i:i + group]
        if chunk:
            kept.append(max(chunk, key=score))
    return kept


def extract_frames(video: Path, info: VideoInfo, work_dir: Path, images_dir: Path,
                   settings: Settings, log_path: Path, cancel_check: CancelCheck,
                   on_progress: Callable[[float, str], None]) -> int:
    raw_dir = work_dir / "frames_raw"
    for d in (raw_dir, images_dir):
        if d.exists():
            shutil.rmtree(d)
        d.mkdir(parents=True)

    ffmpeg_fps, expected = plan_extraction(info.duration_s, settings.target_fps,
                                           settings.max_frames, settings.sharp_group)
    long_side = settings.frame_long_side_px
    # Scale the longer side down to `long_side` (never upscale). FFmpeg applies
    # phone rotation metadata automatically, so frames come out upright.
    vf = (f"fps={ffmpeg_fps:.5f},"
          f"scale='if(gt(iw,ih),min({long_side},iw),-2)':'if(gt(iw,ih),-2,min({long_side},ih))'")
    on_progress(0.05, f"Extracting ~{expected * settings.sharp_group} frames with FFmpeg")
    run([settings.ffmpeg_bin, "-hide_banner", "-loglevel", "error", "-y", "-i", str(video),
         "-vf", vf, "-qscale:v", "2", str(raw_dir / "raw_%05d.jpg")],
        log_path, cancel_check=cancel_check, description="extract frames")

    raw_frames = sorted(raw_dir.glob("raw_*.jpg"))
    if not raw_frames:
        raise RuntimeError("FFmpeg produced no frames from this video.")
    on_progress(0.6, f"Selecting the sharpest of {len(raw_frames)} frames")
    kept = select_sharpest(raw_frames, settings.sharp_group)[: settings.max_frames]
    for i, src in enumerate(kept):
        shutil.copy2(src, images_dir / f"frame_{i:05d}.jpg")
    if not settings.keep_intermediates:
        shutil.rmtree(raw_dir, ignore_errors=True)
    on_progress(1.0, f"Kept {len(kept)} sharp frames")
    return len(kept)
