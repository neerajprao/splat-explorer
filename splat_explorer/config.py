"""Settings, read from environment variables (and an optional `.env` file).

Every setting has a sensible default for an Apple Silicon Mac, so the app runs
with no configuration at all once the tools are installed by
`scripts/setup_mac.sh`.
"""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def load_dotenv(path: Path) -> None:
    """Minimal .env reader (KEY=VALUE lines). Existing env vars win."""
    if not path.is_file():
        return
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def _env(name: str, default: str) -> str:
    value = os.environ.get(name, "").strip()
    return value or default


def _env_int(name: str, default: int) -> int:
    try:
        return int(_env(name, str(default)))
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(_env(name, str(default)))
    except ValueError:
        return default


def _env_bool(name: str, default: bool) -> bool:
    return _env(name, "1" if default else "0").lower() in {"1", "true", "yes", "on"}


def _find_brush() -> str:
    """Brush binary: $BRUSH_BIN, then tools/brush build output, then PATH."""
    explicit = os.environ.get("BRUSH_BIN", "").strip()
    if explicit:
        return explicit
    built = PROJECT_ROOT / "tools" / "brush" / "target" / "release" / "brush"
    if built.exists():
        return str(built)
    return shutil.which("brush") or "brush"


# Training presets. "preview" is a fast sanity check, "standard" the normal
# result, "high" spends more time and allows more splats.
QUALITY_PRESETS: dict[str, dict[str, int]] = {
    "preview": {"iters": 7000, "max_resolution": 1280, "max_splats": 1_500_000},
    "standard": {"iters": 30000, "max_resolution": 1600, "max_splats": 3_000_000},
    "high": {"iters": 30000, "max_resolution": 1920, "max_splats": 6_000_000},
}


@dataclass
class Settings:
    data_dir: Path
    ffmpeg_bin: str
    ffprobe_bin: str
    colmap_bin: str
    brush_bin: str

    # Upload validation
    max_upload_mb: int
    min_duration_s: float
    max_duration_s: float
    min_short_side_px: int

    # Frame extraction
    target_fps: float  # frames per second of video kept for reconstruction
    max_frames: int
    sharp_group: int  # extract target_fps * sharp_group, keep sharpest of each group
    frame_long_side_px: int

    # COLMAP
    colmap_matcher_overlap: int
    use_global_mapper: bool
    min_registered_ratio: float  # below this the job fails ("re-shoot")
    warn_registered_ratio: float  # below this the job warns
    min_registered_images: int

    # Training
    default_quality: str
    sh_degree: int

    # Housekeeping
    keep_intermediates: bool
    worker_poll_s: float

    presets: dict[str, dict[str, int]] = field(default_factory=lambda: dict(QUALITY_PRESETS))

    @property
    def jobs_dir(self) -> Path:
        return self.data_dir / "jobs"

    @property
    def db_path(self) -> Path:
        return self.data_dir / "jobs.sqlite3"

    def job_dir(self, job_id: str) -> Path:
        return self.jobs_dir / job_id

    def ensure_dirs(self) -> None:
        self.jobs_dir.mkdir(parents=True, exist_ok=True)


def get_settings() -> Settings:
    load_dotenv(PROJECT_ROOT / ".env")
    data_dir = Path(_env("SPLAT_DATA_DIR", str(PROJECT_ROOT / "data"))).expanduser().resolve()
    quality = _env("SPLAT_DEFAULT_QUALITY", "standard")
    if quality not in QUALITY_PRESETS:
        quality = "standard"
    settings = Settings(
        data_dir=data_dir,
        ffmpeg_bin=_env("FFMPEG_BIN", shutil.which("ffmpeg") or "ffmpeg"),
        ffprobe_bin=_env("FFPROBE_BIN", shutil.which("ffprobe") or "ffprobe"),
        colmap_bin=_env("COLMAP_BIN", shutil.which("colmap") or "colmap"),
        brush_bin=_find_brush(),
        max_upload_mb=_env_int("SPLAT_MAX_UPLOAD_MB", 4096),
        min_duration_s=_env_float("SPLAT_MIN_DURATION_S", 10.0),
        max_duration_s=_env_float("SPLAT_MAX_DURATION_S", 600.0),
        min_short_side_px=_env_int("SPLAT_MIN_SHORT_SIDE_PX", 720),
        target_fps=_env_float("SPLAT_TARGET_FPS", 2.0),
        max_frames=_env_int("SPLAT_MAX_FRAMES", 300),
        sharp_group=max(1, _env_int("SPLAT_SHARP_GROUP", 3)),
        frame_long_side_px=_env_int("SPLAT_FRAME_LONG_SIDE_PX", 1920),
        colmap_matcher_overlap=_env_int("SPLAT_COLMAP_OVERLAP", 12),
        use_global_mapper=_env_bool("SPLAT_USE_GLOBAL_MAPPER", True),
        min_registered_ratio=_env_float("SPLAT_MIN_REGISTERED_RATIO", 0.3),
        warn_registered_ratio=_env_float("SPLAT_WARN_REGISTERED_RATIO", 0.7),
        min_registered_images=_env_int("SPLAT_MIN_REGISTERED_IMAGES", 20),
        default_quality=quality,
        sh_degree=_env_int("SPLAT_SH_DEGREE", 3),
        keep_intermediates=_env_bool("SPLAT_KEEP_INTERMEDIATES", False),
        worker_poll_s=_env_float("SPLAT_WORKER_POLL_S", 2.0),
    )
    settings.ensure_dirs()
    return settings
