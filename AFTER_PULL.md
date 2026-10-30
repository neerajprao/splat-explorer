# After you pull: what to do on your Mac

Everything in this repo was written and tested in a cloud Linux machine with stand-in versions of COLMAP and Brush. The real tools only run on your Apple Silicon Mac, so these steps finish the job. Expect about **1 hour of setup** (mostly downloads and one Rust build), then one test video.

Nothing here uses NVIDIA or CUDA. Training runs on the Mac GPU through Metal; COLMAP runs on the CPU.

---

## 0. What you need

| Item | Notes |
| --- | --- |
| Apple Silicon Mac (M1/M2/M3/M4) | Your M3 Pro is fine. 16 GB+ memory recommended. |
| macOS 14 Sonoma or newer | |
| ~30 GB free disk | Tools ~5 GB, each job 3–5 GB while processing |
| [Homebrew](https://brew.sh) | Install it first if `brew --version` fails |
| Xcode Command Line Tools | `xcode-select --install` (the setup script asks if missing) |
| Unity Hub + a **Unity 6** editor (6000.0 or newer) | Only for step 6. Add the **Mac Build Support** module. |

---

## 1. Get the code

```bash
git clone https://github.com/neerajprao/splat-explorer.git
cd splat-explorer
```

(or `git pull` if you already cloned it)

## 2. Run the setup script (one time, ~20–40 min)

```bash
scripts/setup_mac.sh
```

It does, in order:

1. Checks you are on an Apple Silicon Mac.
2. `brew install ffmpeg colmap python@3.12 rustup`
3. Installs the stable Rust toolchain.
4. Clones **Brush** into `tools/brush`, checks out a pinned commit, and builds it (`cargo build --release -p brush-app`). This is the slow part.
5. Creates `.venv` and installs `requirements.txt`.
6. Copies `.env.example` to `.env` and writes the Brush path into it.
7. Runs the doctor.

If anything fails, re-run the script; every step is safe to repeat.

## 3. Check everything is green

```bash
source .venv/bin/activate
python scripts/doctor.py
```

You want ✅ for: Apple Silicon Mac, Python packages, ffmpeg, ffprobe, COLMAP, **COLMAP global mapper**, Brush, data folder. Unity Hub can stay ⚠️ until step 6.

If COLMAP shows "global mapper not available", run `brew upgrade colmap`. The pipeline still works without it, just slower.

## 4. First real test from the terminal (Phase 1 gate)

Record a **1–2 minute video of a small room** following the tips below, AirDrop it to the Mac, then:

```bash
source .venv/bin/activate
python -m splat_explorer.cli ~/Downloads/bedroom.MOV --name "Bedroom" --quality preview
```

You'll see progress lines (`frames`, `colmap`, `train`, `export`). When it ends it prints the path of `scene.ply`.

**Pass criteria before going further:**

- [ ] The job finishes (status `done`).
- [ ] The log reports at least ~70% of frames placed (`Largest model: … with N/M frames`).
- [ ] `scene.ply` opens and looks like your room in a splat viewer. Quickest check: drag it into [SuperSplat](https://superspl.at/editor) in the browser, or open it with the Brush app: `tools/brush/target/release/brush data/jobs/<id>/scene/scene.ply --with-viewer`.
- [ ] Note how long it took and how much memory it used (Activity Monitor). Write these down; they replace the estimates in the blueprint.

If it fails, see **Troubleshooting** at the end. Most failures are capture problems, not code problems.

Then try `--quality standard` on the same video and compare.

## 5. Run the web app

```bash
scripts/start.sh
```

This starts the API + web page at **http://127.0.0.1:8000** and the worker (wrapped in `caffeinate` so the Mac doesn't sleep mid-job). Then:

1. Drop a video into **New scene**, keep "Start reconstruction right after upload" ticked, press **Upload**.
2. Watch the stage strip: Check → Frames → Poses → Train → Export.
3. When it says **Ready**, press **Explore** for the ways to open it in Unity.
4. **Log** shows the full tool output for any job. **Retrain** re-runs only training with another quality (reuses frames and camera poses).

Keep the Mac awake and on power while a job runs. Closing the lid will pause it; it resumes when the Mac wakes, and a restarted worker re-queues unfinished jobs.

## 6. Unity viewer

1. Install **Unity Hub**, then a **Unity 6** editor (6000.0 LTS or newer) with **Mac Build Support (Mono)** ticked.
2. Unity Hub → **Add** → **Add project from disk** → choose `unity/SplatViewer` in this repo.
   - The project pins `6000.3.9f1`. If you have a different Unity 6 version, pick it when the Hub asks and accept the upgrade prompt.
3. Open it. The first import downloads packages:
   - **UnitySplats** (Gaussian splat renderer, from GitHub; needs `git`, which the Xcode tools provide)
   - **Unity.WebP** (from the OpenUPM registry, a UnitySplats dependency)
   - **Input System**. If Unity asks to enable the new input backends and restart, choose **Yes**.
4. Check the **Console** has no red errors. (The C# scripts were type-checked here, but never compiled inside a real Unity, so this is the first real compile. If you see errors, send them to me.)
5. Menu bar: **Splat Explorer → 1. Create Viewer Scene**. This creates `Assets/Scenes/SplatViewer.unity` and sets Player Settings (Metal, linear colour, HTTP allowed for 127.0.0.1, window 1600×900, `splatexplorer://` URL scheme).
6. With `scripts/start.sh` running, press **Play**. A list of finished scenes appears; press **Open**.
7. Controls: **W A S D** move, **mouse** look (click the view to capture the mouse, **Esc** to release), **Space** up, **Ctrl** (or **C**/**Q**) down, hold **Shift** to go faster, **scroll** changes speed, **R** resets to the start view, **L** scene list, **H** hides help.
8. **Splat Explorer → 2. Build macOS Viewer** builds `unity/SplatViewer/Builds/SplatViewer.app`. Copy it to `/Applications`. After that:
   - the web app's **Open in SplatViewer** button launches it directly on the right scene, and
   - Terminal works: `open -a SplatViewer --args -scene <job id> -server http://127.0.0.1:8000`

If the **Open in SplatViewer** link does nothing, the URL scheme wasn't registered automatically: in Unity open **Edit → Project Settings → Player → Other Settings → Mac Configuration → Supported URL schemes**, add `splatexplorer`, rebuild, and copy the app to `/Applications` again.

**Check the scene orientation.** The viewer rotates the scene so that "up" matches how you held the phone, and starts you where the video started. If the room is upside down or tilted in your first test, tell me which way. The fix is one line in `SplatSceneLoader.ApplyAlignment`. To load a non-Brush PLY instead, set **Source Coordinates** to `RUB` on the `SplatViewer` object.

## 7. Record good videos

- Walk slowly; move through the space rather than turning on the spot.
- One loop facing inward, one facing the walls, at two heights. End where you started.
- All lights on, blinds closed against direct sun, nothing moving in view.
- 1–2 min for a bedroom/office, 2–4 min for a living room/classroom.
- Avoid mirrors and glass head-on. Anything the camera never saw cannot appear in 3D.

---

## What was and wasn't verified before pushing

| Area | Status |
| --- | --- |
| Python backend, job queue, API, worker, CLI | ✅ 16 automated tests pass (`pytest`) |
| FFmpeg frame extraction + sharp-frame selection | ✅ tested with real FFmpeg on a generated video |
| COLMAP + Brush orchestration (flags, file layout, largest-model pick, progress, cancel, retrain) | ✅ tested against stand-in executables that mimic their I/O |
| Brush CLI flags (`--total-train-iters`, `--export-path`, …) | ✅ read from Brush source at the pinned commit |
| COLMAP 4 option names | ⚠️ chosen at runtime from `colmap <cmd> -h`; real COLMAP never ran here |
| Web UI | ✅ rendered in headless Chrome, light + dark, no console errors |
| Unity C# scripts | ⚠️ type-checked against stub APIs matching UnitySplats' real signatures; **not yet compiled in Unity** |
| Real reconstruction quality, timings, memory on an M3 Pro | ❌ needs your Mac (step 4) |
| `splatexplorer://` URL-scheme auto-registration | ⚠️ best effort; manual fallback above |

## Running the tests on the Mac

```bash
source .venv/bin/activate
pytest -q
```

These use stand-in COLMAP/Brush, so they pass even before step 2 finishes, but they need FFmpeg.

---

## Troubleshooting

| Symptom | Likely cause | What to do |
| --- | --- | --- |
| Upload rejected: too short / too low resolution | Validation limits | Record ≥10 s at ≥720p (1080p/4K better). Limits live in `.env`. |
| Fails in **Poses**: "Only N of M frames could be placed" | Blur, fast turns, gaps, or plain walls | Re-record slower with more overlap. Try `SPLAT_TARGET_FPS=3` in `.env` for short videos. |
| Warning "COLMAP split the capture into N pieces" | Part of the walk didn't overlap | Only the largest piece is used. Re-record with a continuous loop. |
| Poses stage is very slow | Incremental mapper fallback, or 4K frames | `brew upgrade colmap` for the global mapper; lower `SPLAT_FRAME_LONG_SIDE_PX=1600`. |
| Fails in **Train**: Brush exits or memory pressure | Too many splats / resolution for RAM | Use `preview` quality, close Unity while training, or lower `max_splats` in `QUALITY_PRESETS` (`splat_explorer/config.py`). |
| `brush` not found | Build failed or `.env` path wrong | Re-run `scripts/setup_mac.sh`; check `BRUSH_BIN` in `.env`. |
| Brush says an option is unknown | Brush changed its CLI | Keep the pinned commit (`BRUSH_COMMIT` in `setup_mac.sh`). Flags are read from `brush --help` in `splat_explorer/pipeline/train.py`. |
| Scene looks good near the path, smeared elsewhere | Normal 3DGS behaviour off the captured path | Capture more angles; explore near where you walked. |
| Floating blobs / haze | Floaters from sky, glass, or moving objects | Crop in [SuperSplat](https://superspl.at/editor), export PLY, load it with `-ply`. |
| Unity: "Server not reachable" | `scripts/start.sh` not running, or HTTP blocked | Start the backend; re-run **Create Viewer Scene** (sets "Allow downloads over HTTP"). |
| Unity: package resolve error for `com.netpyoung.webp` | OpenUPM registry unreachable | Check internet; the scoped registry is in `Packages/manifest.json`. |
| Unity: low FPS | Too many splats | Retrain with `preview`, or keep `Compression = Spark` on the loader. |
| Job stuck in "running" after a crash | Worker died | Restart `scripts/start.sh`; unfinished jobs are re-queued and resume from the last finished stage. |
