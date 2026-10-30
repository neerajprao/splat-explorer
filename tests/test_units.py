import math
import struct
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent / "fakes"))
import fake_colmap  # noqa: E402

from splat_explorer.db import JobStore
from splat_explorer.pipeline import colmap, export, frames, probe, train
from splat_explorer.pipeline.runner import overall_progress


def test_plan_extraction_caps_frames():
    fps, kept = frames.plan_extraction(duration_s=300, target_fps=2, max_frames=300, sharp_group=3)
    assert kept == 300
    assert math.isclose(fps, 3.0)  # 300 frames over 300 s, x3 candidates
    fps, kept = frames.plan_extraction(duration_s=60, target_fps=2, max_frames=300, sharp_group=3)
    assert kept == 120 and math.isclose(fps, 6.0)


def test_select_sharpest_picks_best_in_each_group():
    items = [Path(f"{i}.jpg") for i in range(7)]
    scores = {"0.jpg": 1, "1.jpg": 5, "2.jpg": 2, "3.jpg": 9, "4.jpg": 1, "5.jpg": 0, "6.jpg": 3}
    kept = frames.select_sharpest(items, 3, score=lambda p: scores[p.name])
    assert [p.name for p in kept] == ["1.jpg", "3.jpg", "6.jpg"]


def test_parse_ffprobe_rotation_and_fps():
    data = {"streams": [{"codec_type": "video", "codec_name": "hevc", "width": 3840,
                         "height": 2160, "avg_frame_rate": "30000/1001", "duration": "61.5",
                         "side_data_list": [{"rotation": -90}]}], "format": {}}
    info = probe.parse_ffprobe(data, 50 * 1024 * 1024)
    assert info.rotation == 270
    assert info.display_width == 2160 and info.display_height == 3840
    assert math.isclose(info.fps, 29.97, abs_tol=0.01)


def test_validate_rejects_short_and_low_res(settings):
    ok = probe.VideoInfo(60, 1920, 1080, 30, "h264", 0, 100)
    assert probe.validate(ok, settings) == []
    with pytest.raises(probe.InvalidVideo):
        probe.validate(probe.VideoInfo(2, 1920, 1080, 30, "h264", 0, 1), settings)
    with pytest.raises(probe.InvalidVideo):
        probe.validate(probe.VideoInfo(60, 320, 240, 30, "h264", 0, 1), settings)


def test_images_bin_roundtrip_and_camera_math(tmp_path):
    model = tmp_path / "sparse" / "0"
    fake_colmap.write_model(model, ["a.jpg", "b.jpg", "c.jpg", "d.jpg"])
    images = colmap.read_images_bin(model / "images.bin")
    assert [im.name for im in images] == ["a.jpg", "b.jpg", "c.jpg", "d.jpg"]
    first = images[0]  # identity rotation, t = (0, 0, 2)
    assert first.center() == pytest.approx([0, 0, -2])
    assert first.forward() == pytest.approx([0, 0, 1])
    assert first.up() == pytest.approx([0, -1, 0])  # COLMAP: camera +Y points down
    summary = colmap.camera_summary(tmp_path)
    assert summary["camera_count"] == 4
    assert summary["average_up"] == pytest.approx([0, -1, 0])
    assert summary["camera_path_extent"] > 0


def test_find_largest_model(tmp_path):
    fake_colmap.write_model(tmp_path / "0", ["a.jpg"])
    fake_colmap.write_model(tmp_path / "1", ["a.jpg", "b.jpg", "c.jpg"])
    best, count, models = colmap.find_largest_model(tmp_path)
    assert best.name == "1" and count == 3 and models == 2


def test_pick_option_prefers_new_names():
    assert colmap.pick_option("--FeatureExtraction.use_gpu", "FeatureExtraction.use_gpu",
                              "SiftExtraction.use_gpu") == "FeatureExtraction.use_gpu"
    assert colmap.pick_option("--SiftExtraction.use_gpu", "FeatureExtraction.use_gpu",
                              "SiftExtraction.use_gpu") == "SiftExtraction.use_gpu"
    assert colmap.pick_option("nothing", "A") is None


def test_brush_iters_flag_detection():
    assert train.iters_flag("--total-train-iters <N>") == "--total-train-iters"
    assert train.iters_flag("--total-steps <N>") == "--total-steps"


def test_ply_header(tmp_path):
    sys.path.insert(0, str(Path(__file__).resolve().parent / "fakes"))
    import fake_brush

    p = tmp_path / "s.ply"
    fake_brush.write_ply(p, 7)
    h = export.read_ply_header(p)
    assert h["vertex_count"] == 7 and h["format"] == "binary_little_endian"
    assert export.brush_vertical_axis(h["comments"]) == [0, -1, 0]


def test_overall_progress_monotonic():
    values = [overall_progress(s, w) for s in ("probe", "frames", "colmap", "train", "export")
              for w in (0, 0.5, 1)]
    assert values == sorted(values)
    assert values[-1] == 1.0


def test_jobstore_claim_and_requeue(tmp_path):
    store = JobStore(tmp_path / "db.sqlite3")
    a = store.create("a", "preview")
    b = store.create("b", "preview")
    assert store.claim_next() is None
    store.enqueue(a["id"])
    store.enqueue(b["id"])
    first = store.claim_next()
    assert first["id"] == a["id"] and first["status"] == "running"
    assert store.requeue_interrupted() == 1
    assert store.get(a["id"])["status"] == "queued"
    store.add_warning(b["id"], "w1")
    store.add_warning(b["id"], "w1")
    assert store.get(b["id"])["warnings"] == ["w1"]
