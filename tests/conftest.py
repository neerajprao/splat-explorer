import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from splat_explorer import config as config_mod  # noqa: E402
from splat_explorer.pipeline import colmap as colmap_mod  # noqa: E402
from splat_explorer.pipeline import train as train_mod  # noqa: E402

FAKES = Path(__file__).resolve().parent / "fakes"


def _wrapper(tmp: Path, name: str, script: Path) -> str:
    """An executable shell wrapper that calls a fake tool with this Python."""
    path = tmp / name
    path.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{script}" "$@"\n')
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return str(path)


@pytest.fixture
def settings(tmp_path, monkeypatch):
    bins = tmp_path / "bin"
    bins.mkdir()
    for key in list(os.environ):
        if key.startswith("SPLAT_") or key.endswith("_BIN"):
            monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("SPLAT_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("COLMAP_BIN", _wrapper(bins, "colmap", FAKES / "fake_colmap.py"))
    monkeypatch.setenv("BRUSH_BIN", _wrapper(bins, "brush", FAKES / "fake_brush.py"))
    monkeypatch.setenv("SPLAT_MIN_REGISTERED_IMAGES", "5")
    monkeypatch.setenv("SPLAT_MIN_DURATION_S", "5")
    monkeypatch.setenv("SPLAT_MIN_SHORT_SIDE_PX", "480")
    monkeypatch.setattr(config_mod, "load_dotenv", lambda path: None)
    colmap_mod._help.cache_clear()
    colmap_mod._commands.cache_clear()
    train_mod.brush_help.cache_clear()
    s = config_mod.get_settings()
    s.worker_poll_s = 0.05
    return s


@pytest.fixture(scope="session")
def sample_video(tmp_path_factory):
    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg not installed")
    out = tmp_path_factory.mktemp("video") / "room.mp4"
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi",
         "-i", "testsrc2=size=1280x720:rate=30:duration=12", "-pix_fmt", "yuv420p",
         "-c:v", "libx264", "-preset", "ultrafast", str(out)],
        check=True,
    )
    return out
