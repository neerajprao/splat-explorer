# Blueprint summary

This is a condensed version of the planning research behind this repo. The full report, with sources, was written on 30 Oct 2026 as a Claude Doc: *Video-to-3DGS Explorer — Technical Blueprint*.

## Decision: fully local on Apple Silicon

The project runs entirely on the Mac (no NVIDIA, no CUDA, no cloud GPU). Each stage was chosen because it works on Apple Silicon:

| Stage | Tool | Apple Silicon support |
| --- | --- | --- |
| Frames | FFmpeg | native |
| Camera poses | COLMAP 4.x | native, CPU (its GPU paths are CUDA-only and are disabled) |
| Training | Brush | Metal through wgpu |
| Rendering | UnitySplats in Unity 6 | Metal, GPU radix sort |

## Rejected options and why

| Option | Reason |
| --- | --- |
| Nerfstudio Splatfacto, gsplat | Require NVIDIA CUDA |
| Postshot | Windows + NVIDIA only |
| OpenSplat | Works on Metal, but AGPL-3.0 (a networked service must publish its source); Brush is Apache-2.0 |
| msplat | Fast native Metal trainer, a good **alternative** if Brush is slow; Mac-only |
| Splatline | Default backend makes one splat per frame from a single image, not one explorable scene; some backends are non-commercial |
| "Gaussian Maker" | No official repository or product could be found |
| aras-p UnityGaussianSplatting | Works on Metal, but imports only in the Editor (no runtime loading) and is no longer developed; kept as a fallback |
| INRIA reference 3DGS trainer | Non-commercial licence, CUDA |

## Pipeline order

1. ffprobe validation (duration 10 s–10 min, ≥720p)
2. FFmpeg at `2 fps × 3`, keep the sharpest of every 3 frames, max 300, longest side 1920 px
3. COLMAP `feature_extractor` (single OPENCV camera, CPU) → `sequential_matcher` → `global_mapper` (fallback `mapper`) → choose the model with most registered frames → `image_undistorter`
4. Brush training: 7k (preview) or 30k (standard/high) iterations
5. Export `scene.ply` + `scene.json` (camera path, average up vector) + `preview.jpg`
6. Unity downloads the PLY, loads it at runtime, levels it with the average camera up vector, and starts at the first camera

## Known limits

Static scenes only. Unseen areas cannot be reconstructed. Views far from the walked path degrade. Mirrors, glass and glossy floors cause artefacts. Processing a room on an M3 Pro is estimated at about 30–90 min; measure it in the first test.
