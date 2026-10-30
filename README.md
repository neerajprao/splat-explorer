# Splat Explorer

Upload a video of a real place, get back a photoreal **3D Gaussian Splat**, and walk through it in **Unity** with a free fly camera.

Everything runs locally on an **Apple Silicon Mac**: no NVIDIA, no CUDA, no cloud.

> **Just pulled this repo? Start with [AFTER_PULL.md](AFTER_PULL.md).**

```
 Browser (web/)            FastAPI (splat_explorer/api.py)            Worker (splat_explorer/worker.py)
 ───────────────           ───────────────────────────────            ─────────────────────────────────
 upload video  ──────────▶ validate with ffprobe, save job  ──queue──▶ 1 probe    ffprobe
 watch progress ◀───────── SQLite job table (data/)                    2 frames   FFmpeg + sharpest-frame pick
                                                                       3 colmap   COLMAP 4 (CPU): SIFT → sequential
                                                                                  matching → global mapper → undistort
                                                                       4 train    Brush (Metal GPU) → .ply
                                                                       5 export   scene.ply + scene.json + preview.jpg

 Unity 6 viewer (unity/SplatViewer)
 ──────────────────────────────────
 GET /api/scenes/<id>/scene.ply ─▶ UnitySplats runtime loader (Metal) ─▶ FlyCamera (WASD, mouse, Space/Ctrl, Shift)
```

## Stack

| Part | Choice | Why |
| --- | --- | --- |
| Frame extraction | FFmpeg + variance-of-Laplacian sharp-frame filter | Blurred frames are the #1 cause of bad scenes |
| Camera poses | [COLMAP 4.x](https://colmap.github.io/) (CPU, global mapper) | Mature, native on macOS via Homebrew |
| Splat training | [Brush](https://github.com/ArthurBrussee/brush) (Apache-2.0) | Trains on the Mac GPU via wgpu/Metal |
| Backend | FastAPI + SQLite + one worker process | No extra services |
| Frontend | Static HTML/CSS/JS served by FastAPI | Upload, preview, progress, logs |
| Unity renderer | [UnitySplats](https://github.com/arloopa/UnitySplats) (MIT) | Metal GPU sorting and runtime `.ply` loading |
| Camera | `FlyCamera.cs` | No physics, colliders or NavMesh, as specified |

## Repository layout

```
AFTER_PULL.md                 step-by-step setup on your Mac  ← start here
scripts/setup_mac.sh          installs ffmpeg/colmap/rust, builds Brush, creates .venv
scripts/start.sh              runs web app + worker (with caffeinate)
scripts/doctor.py             checks the machine and tools
splat_explorer/
  api.py                      HTTP API + serves web/
  worker.py                   background job runner
  cli.py                      process one video from the terminal
  config.py                   settings (.env / environment), quality presets
  db.py                       SQLite job store
  pipeline/                   probe, frames, colmap, train, export, runner, tools
web/                          index.html, app.js, styles.css
unity/SplatViewer/            Unity 6 project (Assets/SplatExplorer + Packages/manifest.json)
tests/                        pytest suite with stand-in COLMAP/Brush executables
docs/BLUEPRINT.md             summary of the planning research and decisions
```

## Quick start (details in AFTER_PULL.md)

```bash
scripts/setup_mac.sh                      # once
scripts/start.sh                          # open http://127.0.0.1:8000
# or, without the web app:
source .venv/bin/activate
python -m splat_explorer.cli room.mov --quality preview
```

Quality presets (`splat_explorer/config.py`):

| Preset | Iterations | Max image side | Max splats | Use |
| --- | --- | --- | --- | --- |
| preview | 7,000 | 1280 px | 1.5 M | quick check that a capture works |
| standard | 30,000 | 1600 px | 3 M | normal result |
| high | 30,000 | 1920 px | 6 M | more detail, more memory and time |

## API

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/api/health` | tool availability |
| POST | `/api/jobs` | upload (`file`, `name`, `quality`, `auto_start`) |
| GET | `/api/jobs`, `/api/jobs/{id}` | status, stage, progress, warnings |
| POST | `/api/jobs/{id}/start` | queue (or retry) a job |
| POST | `/api/jobs/{id}/retrain` | re-run training with another quality |
| POST | `/api/jobs/{id}/cancel` | stop a queued or running job |
| DELETE | `/api/jobs/{id}` | delete job and files |
| GET | `/api/jobs/{id}/video`, `/api/jobs/{id}/log` | uploaded video, log tail |
| GET | `/api/scenes` | finished scenes (used by the Unity viewer) |
| GET | `/api/scenes/{id}/scene.ply`, `scene.json`, `preview.jpg` | scene files |

Interactive docs: http://127.0.0.1:8000/docs

## Job folder

```
data/jobs/<id>/
  input.mov        uploaded video
  probe.json       ffprobe summary
  images/          kept frames
  dataset/         COLMAP undistorted dataset (images + sparse) that Brush trains on
  scene/           scene.ply, scene.json, preview.jpg   ← the result
  job.log          full tool output
  .done_<stage>    markers; a retry skips finished stages
```

## Limits to know

- 3DGS reproduces only what the camera saw. Unseen areas are empty or smeared, and views far from the walked path degrade.
- Static scenes only: people or objects moving during capture become blur or floaters.
- Mirrors, glass and glossy floors produce artefacts.
- COLMAP's world has no gravity. The viewer levels the scene using the average phone orientation, which suits handheld walk-throughs.

## Third-party licences

Brush: Apache-2.0 · COLMAP: BSD · FFmpeg: LGPL/GPL (installed via Homebrew, not bundled) · UnitySplats: MIT · Unity: Unity Personal is free under US$200k revenue/funding per year. The original INRIA 3DGS trainer (non-commercial) is **not** used.
