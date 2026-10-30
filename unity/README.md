# SplatViewer (Unity 6)

A minimal Unity project that loads scenes produced by the Splat Explorer backend and lets you fly through them. Setup steps are in [../AFTER_PULL.md](../AFTER_PULL.md#6-unity-viewer).

## What's in the project

| File | Purpose |
| --- | --- |
| `Packages/manifest.json` | UnitySplats (pinned commit), its Unity.WebP dependency via OpenUPM, Input System, built-in modules |
| `Assets/SplatExplorer/Scripts/FlyCamera.cs` | WASD / mouse / Space / Ctrl / Shift fly camera; supports both input backends |
| `Assets/SplatExplorer/Scripts/SplatSceneLoader.cs` | Picks a scene (deep link, command line, inspector or on-screen list), downloads `scene.ply`, loads it with `GsplatRuntimeLoader`, levels the scene and places the camera |
| `Assets/SplatExplorer/Scripts/SceneMeta.cs` | Data classes for `scene.json` |
| `Assets/SplatExplorer/Editor/SplatExplorerMenu.cs` | **Splat Explorer** menu: create the viewer scene, build the macOS app |

`ProjectSettings/` and `.meta` files are created by Unity the first time you open the folder.

## How a scene is placed

Brush exports splats in COLMAP's world frame (x right, y down, z forward), which has no notion of gravity. The loader:

1. Loads the PLY with `SourceCoordinates.RDF`, so UnitySplats flips Y into Unity's frame.
2. Reads `scene.json` (written by the pipeline): the average camera "up" vector and the first camera pose.
3. Rotates the `Splat` object so that average "up" becomes Unity's +Y. Handheld videos keep the phone upright, so this levels the room.
4. Puts the fly camera at the first video frame's position, looking the same way.
5. Sets movement speed and clip planes from the size of the walked path, because COLMAP's units are arbitrary.

## Running

- **In the Editor:** open `Assets/Scenes/SplatViewer.unity` (created by the menu), start `scripts/start.sh`, press Play, choose a scene.
- **Built app:** `Splat Explorer → 2. Build macOS Viewer` → copy `Builds/SplatViewer.app` to `/Applications`.
  - `open -a SplatViewer --args -scene <job id> -server http://127.0.0.1:8000`
  - `open -a SplatViewer --args -ply /path/to/scene.ply` (no server needed)
  - `splatexplorer://open?scene=<job id>&server=http://127.0.0.1:8000` (the web app's button)

## Fallback renderer

If UnitySplats misbehaves on your Mac, [aras-p/UnityGaussianSplatting](https://github.com/aras-p/UnityGaussianSplatting) also renders on Metal. It only imports PLY in the Editor (Tools → Gaussian Splats → Create GaussianSplatAsset), not at runtime, so use it for manual checks of a `scene.ply`.
