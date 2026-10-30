"""Export (stage 'export'): put the final files where the API and Unity expect them.

scene/
  scene.ply      standard 3DGS PLY (binary little-endian) from Brush
  scene.json     splat count + camera path summary used by the Unity viewer
  preview.jpg    thumbnail from the first frame
"""

from __future__ import annotations

import json
import shutil
import time
from pathlib import Path

from .colmap import camera_summary


def read_ply_header(path: Path) -> dict:
    """Vertex count, format and comments from a PLY header (no full read)."""
    info: dict = {"vertex_count": 0, "format": None, "comments": [], "properties": []}
    with open(path, "rb") as fh:
        first = fh.readline().strip()
        if first != b"ply":
            raise ValueError(f"{path} is not a PLY file")
        for _ in range(512):
            line = fh.readline()
            if not line:
                break
            text = line.decode("ascii", errors="replace").strip()
            if text == "end_header":
                break
            parts = text.split()
            if not parts:
                continue
            if parts[0] == "format":
                info["format"] = parts[1] if len(parts) > 1 else None
            elif parts[0] == "comment":
                info["comments"].append(text[len("comment"):].strip())
            elif parts[:2] == ["element", "vertex"] and len(parts) > 2:
                info["vertex_count"] = int(parts[2])
            elif parts[0] == "property":
                info["properties"].append(parts[-1])
    return info


def brush_vertical_axis(comments: list[str]) -> list[float] | None:
    for c in comments:
        if c.lower().startswith("vertical axis:"):
            vals = c.split(":", 1)[1].split()
            try:
                if len(vals) == 3:
                    return [float(v) for v in vals]
            except ValueError:
                return None
    return None


def make_preview(images_dir: Path, out_path: Path, width: int = 640) -> bool:
    frames = sorted(images_dir.glob("*.jpg"))
    if not frames:
        return False
    try:
        import cv2

        img = cv2.imread(str(frames[len(frames) // 2]))
        if img is None:
            return False
        h, w = img.shape[:2]
        if w > width:
            img = cv2.resize(img, (width, int(h * width / w)), interpolation=cv2.INTER_AREA)
        return bool(cv2.imwrite(str(out_path), img, [int(cv2.IMWRITE_JPEG_QUALITY), 85]))
    except Exception:
        shutil.copy2(frames[0], out_path)
        return True


def export_scene(job_id: str, name: str, trained_ply: Path, dataset_dir: Path,
                 images_dir: Path, scene_dir: Path, quality: str) -> dict:
    scene_dir.mkdir(parents=True, exist_ok=True)
    final_ply = scene_dir / "scene.ply"
    shutil.copy2(trained_ply, final_ply)
    header = read_ply_header(final_ply)
    if header["format"] != "binary_little_endian":
        raise ValueError(f"Unexpected PLY format {header['format']}; the Unity loader needs "
                         "binary_little_endian.")
    meta = {
        "id": job_id,
        "name": name,
        "quality": quality,
        "created_at": time.time(),
        "splat_count": header["vertex_count"],
        "ply_size_bytes": final_ply.stat().st_size,
        "ply_comments": header["comments"],
        "brush_vertical_axis": brush_vertical_axis(header["comments"]),
        "unity_source_coordinates": "RDF",
        "cameras": camera_summary(dataset_dir),
    }
    (scene_dir / "scene.json").write_text(json.dumps(meta, indent=2))
    make_preview(images_dir, scene_dir / "preview.jpg")
    return meta
