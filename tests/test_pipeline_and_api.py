"""End-to-end: real ffmpeg/ffprobe, fake COLMAP and Brush."""

import json
import threading

import pytest
from fastapi.testclient import TestClient

from splat_explorer.api import create_app
from splat_explorer.db import JobStore
from splat_explorer.pipeline.runner import run_job


def test_full_pipeline_with_fake_tools(settings, sample_video):
    store = JobStore(settings.db_path)
    job = store.create("Test room", "preview", video_filename="room.mp4", status="running")
    job_dir = settings.job_dir(job["id"])
    job_dir.mkdir(parents=True)
    (job_dir / "input.mp4").write_bytes(sample_video.read_bytes())

    assert run_job(job["id"], store, settings), (job_dir / "job.log").read_text()
    final = store.get(job["id"])
    assert final["status"] == "done" and final["progress"] == 1.0
    assert final["frames_total"] == 24  # 12 s x 2 fps
    assert final["frames_registered"] == int(24 * 0.9)
    assert final["splat_count"] == 50
    assert any("split" in w for w in final["warnings"])  # fake COLMAP makes 2 models

    scene = job_dir / "scene"
    meta = json.loads((scene / "scene.json").read_text())
    assert meta["unity_source_coordinates"] == "RDF"
    assert meta["cameras"]["camera_count"] == int(24 * 0.9)
    assert (scene / "preview.jpg").stat().st_size > 0
    assert not (job_dir / "dataset" / "stereo").exists()

    # Retrain path: only train + export are invalidated and re-run.
    (job_dir / ".done_train").unlink()
    (job_dir / ".done_export").unlink()
    store.update(job["id"], status="running", quality="standard")
    assert run_job(job["id"], store, settings)


def test_low_registration_fails(settings, sample_video, monkeypatch):
    monkeypatch.setattr(settings, "min_registered_images", 1000)
    store = JobStore(settings.db_path)
    job = store.create("Bad", "preview", status="running")
    job_dir = settings.job_dir(job["id"])
    job_dir.mkdir(parents=True)
    (job_dir / "input.mp4").write_bytes(sample_video.read_bytes())
    assert not run_job(job["id"], store, settings)
    failed = store.get(job["id"])
    assert failed["status"] == "failed" and "re-record" in failed["error"]
    assert (job_dir / ".done_frames").exists() and not (job_dir / ".done_colmap").exists()


def test_api_upload_start_and_scene(settings, sample_video):
    app = create_app(settings)
    client = TestClient(app)
    assert client.get("/api/health").json()["tools"]["colmap"] is True

    with open(sample_video, "rb") as fh:
        r = client.post("/api/jobs", files={"file": ("room.mp4", fh, "video/mp4")},
                        data={"name": "Living room", "quality": "preview"})
    assert r.status_code == 201, r.text
    job = r.json()
    assert job["status"] == "uploaded" and job["video_info"]["width"] == 1280

    assert client.get(f"/api/jobs/{job['id']}/video").status_code == 200
    r = client.post(f"/api/jobs/{job['id']}/start", json={})
    assert r.json()["status"] == "queued"
    assert client.post(f"/api/jobs/{job['id']}/start", json={}).status_code == 409

    # Run the worker loop once, as `python -m splat_explorer.worker --once` would.
    store = app.state.store
    claimed = store.claim_next()
    assert run_job(claimed["id"], store, settings)

    done = client.get(f"/api/jobs/{job['id']}").json()
    assert done["status"] == "done" and done["scene_ready"]
    assert done["viewer_link"].startswith("splatexplorer://open?scene=")
    scenes = client.get("/api/scenes").json()
    assert scenes[0]["id"] == job["id"]
    ply = client.get(f"/api/scenes/{job['id']}/scene.ply")
    assert ply.status_code == 200 and ply.content.startswith(b"ply")
    assert client.get(f"/api/scenes/{job['id']}/secrets.txt").status_code == 404
    assert "Done" in client.get(f"/api/jobs/{job['id']}/log").text

    r = client.post(f"/api/jobs/{job['id']}/retrain", json={"quality": "standard"})
    assert r.status_code == 200 and r.json()["quality"] == "standard"

    assert client.delete(f"/api/jobs/{job['id']}").status_code == 200
    assert client.get(f"/api/jobs/{job['id']}").status_code == 404


def test_api_rejects_bad_uploads(settings, tmp_path):
    client = TestClient(create_app(settings))
    r = client.post("/api/jobs", files={"file": ("x.txt", b"hello", "text/plain")})
    assert r.status_code == 415
    r = client.post("/api/jobs", files={"file": ("x.mp4", b"not a video", "video/mp4")})
    assert r.status_code == 422
    assert client.get("/api/jobs").json() == []


def test_cancel_running_job(settings, sample_video, tmp_path, monkeypatch):
    """A slow fake Brush is terminated when the job is cancelled."""
    slow = tmp_path / "bin" / "brush"
    slow.write_text('#!/bin/sh\nif [ "$1" = "--help" ]; then echo "--total-train-iters"; exit 0; fi\n'
                    'sleep 30\n')
    store = JobStore(settings.db_path)
    job = store.create("Slow", "preview", status="running")
    job_dir = settings.job_dir(job["id"])
    job_dir.mkdir(parents=True)
    (job_dir / "input.mp4").write_bytes(sample_video.read_bytes())

    def cancel_when_training():
        import time
        for _ in range(300):
            j = store.get(job["id"])
            if j and j.get("stage") == "train":
                store.update(job["id"], cancel_requested=True)
                return
            time.sleep(0.1)

    threading.Thread(target=cancel_when_training, daemon=True).start()
    assert not run_job(job["id"], store, settings)
    assert store.get(job["id"])["status"] == "cancelled"
