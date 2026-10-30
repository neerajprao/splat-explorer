"""Video validation with ffprobe (stage 'probe')."""

from __future__ import annotations

import json
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path

from ..config import Settings


@dataclass
class VideoInfo:
    duration_s: float
    width: int
    height: int
    fps: float
    codec: str
    rotation: int
    size_mb: float

    @property
    def display_width(self) -> int:
        return self.height if self.rotation in (90, 270) else self.width

    @property
    def display_height(self) -> int:
        return self.width if self.rotation in (90, 270) else self.height

    def to_dict(self) -> dict:
        d = asdict(self)
        d["display_width"] = self.display_width
        d["display_height"] = self.display_height
        return d


class InvalidVideo(ValueError):
    pass


def _parse_rate(rate: str | None) -> float:
    if not rate or rate in ("0/0", "N/A"):
        return 0.0
    if "/" in rate:
        num, den = rate.split("/", 1)
        try:
            return float(num) / float(den) if float(den) else 0.0
        except ValueError:
            return 0.0
    try:
        return float(rate)
    except ValueError:
        return 0.0


def parse_ffprobe(data: dict, size_bytes: int) -> VideoInfo:
    streams = [s for s in data.get("streams", []) if s.get("codec_type") == "video"]
    if not streams:
        raise InvalidVideo("No video stream found in the file.")
    s = streams[0]
    duration = 0.0
    for candidate in (s.get("duration"), data.get("format", {}).get("duration")):
        try:
            duration = float(candidate)
            if duration > 0:
                break
        except (TypeError, ValueError):
            continue
    rotation = 0
    tags = s.get("tags") or {}
    if "rotate" in tags:
        try:
            rotation = int(float(tags["rotate"])) % 360
        except ValueError:
            rotation = 0
    for side in s.get("side_data_list") or []:
        if "rotation" in side:
            try:
                rotation = int(float(side["rotation"])) % 360
            except (TypeError, ValueError):
                pass
    fps = _parse_rate(s.get("avg_frame_rate")) or _parse_rate(s.get("r_frame_rate"))
    return VideoInfo(
        duration_s=round(duration, 3),
        width=int(s.get("width") or 0),
        height=int(s.get("height") or 0),
        fps=round(fps, 3),
        codec=str(s.get("codec_name") or "unknown"),
        rotation=rotation,
        size_mb=round(size_bytes / (1024 * 1024), 2),
    )


def probe_video(path: Path, settings: Settings) -> VideoInfo:
    try:
        out = subprocess.run(
            [settings.ffprobe_bin, "-v", "error", "-print_format", "json",
             "-show_format", "-show_streams", str(path)],
            capture_output=True, text=True, timeout=120,
        )
    except FileNotFoundError as exc:
        raise InvalidVideo(f"ffprobe not found ({settings.ffprobe_bin}). Install ffmpeg.") from exc
    if out.returncode != 0:
        raise InvalidVideo("This file could not be read as a video "
                           f"({(out.stderr or '').strip()[:200]}).")
    try:
        data = json.loads(out.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise InvalidVideo("ffprobe returned unreadable output.") from exc
    return parse_ffprobe(data, path.stat().st_size)


def validate(info: VideoInfo, settings: Settings) -> list[str]:
    """Raise InvalidVideo for hard failures; return soft warnings."""
    if info.duration_s <= 0:
        raise InvalidVideo("Could not determine the video duration.")
    if info.duration_s < settings.min_duration_s:
        raise InvalidVideo(f"Video is {info.duration_s:.0f}s long; record at least "
                           f"{settings.min_duration_s:.0f}s while walking around the space.")
    if info.duration_s > settings.max_duration_s:
        raise InvalidVideo(f"Video is {info.duration_s / 60:.1f} min long; the limit is "
                           f"{settings.max_duration_s / 60:.0f} min. Trim it or split the space.")
    short_side = min(info.width, info.height)
    if short_side < settings.min_short_side_px:
        raise InvalidVideo(f"Resolution {info.width}x{info.height} is too low; record at least "
                           f"{settings.min_short_side_px}p (1080p or 4K recommended).")
    warnings = []
    if short_side < 1080:
        warnings.append("Video is below 1080p; detail will be soft. 4K or 1080p is recommended.")
    if 0 < info.fps < 24:
        warnings.append(f"Low frame rate ({info.fps:.0f} fps) may cause motion blur.")
    return warnings
