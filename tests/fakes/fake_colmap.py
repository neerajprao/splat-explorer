#!/usr/bin/env python3
"""Stand-in for the `colmap` binary in tests. Writes plausible output files."""

import math
import shutil
import struct
import sys
from pathlib import Path

HELP_COMMANDS = """COLMAP 4.2.0 (fake)
Available commands:
  help
  feature_extractor
  sequential_matcher
  mapper
  global_mapper
  image_undistorter
"""

FEATURE_HELP = "--database_path --image_path --ImageReader.single_camera --ImageReader.camera_model --FeatureExtraction.use_gpu"
MATCH_HELP = "--database_path --FeatureMatching.use_gpu --SequentialMatching.overlap"


def args_dict(argv):
    out = {}
    it = iter(range(len(argv)))
    i = 0
    while i < len(argv):
        if argv[i].startswith("--") and i + 1 < len(argv):
            out[argv[i][2:]] = argv[i + 1]
            i += 2
        else:
            i += 1
    return out


def quat_from_yaw(yaw):
    # rotation about the world Y axis, as (w, x, y, z)
    return (math.cos(yaw / 2), 0.0, math.sin(yaw / 2), 0.0)


def write_images_bin(path: Path, names):
    with open(path, "wb") as fh:
        fh.write(struct.pack("<Q", len(names)))
        for i, name in enumerate(names):
            yaw = 2 * math.pi * i / max(1, len(names))
            fh.write(struct.pack("<i", i + 1))
            fh.write(struct.pack("<4d", *quat_from_yaw(yaw)))
            fh.write(struct.pack("<3d", 0.0, 0.0, 2.0))
            fh.write(struct.pack("<i", 1))
            fh.write(name.encode() + b"\x00")
            fh.write(struct.pack("<Q", 1))
            fh.write(struct.pack("<ddq", 1.0, 2.0, -1))


def write_model(model_dir: Path, names):
    model_dir.mkdir(parents=True, exist_ok=True)
    (model_dir / "cameras.bin").write_bytes(b"\x01" + b"\x00" * 7)
    (model_dir / "points3D.bin").write_bytes(b"\x00" * 8)
    write_images_bin(model_dir / "images.bin", names)


def main(argv):
    if not argv or argv[0] == "help":
        print(HELP_COMMANDS)
        return 0
    cmd, rest = argv[0], argv[1:]
    if "-h" in rest:
        print({"feature_extractor": FEATURE_HELP, "sequential_matcher": MATCH_HELP}.get(cmd, ""))
        return 0
    a = args_dict(rest)
    if cmd == "feature_extractor":
        Path(a["database_path"]).write_text("db")
        print("Processed file [1/1]")
        return 0
    if cmd == "sequential_matcher":
        print("Matching block [1/1]")
        return 0
    if cmd in ("global_mapper", "mapper"):
        images = sorted(p.name for p in Path(a["image_path"]).glob("*.jpg"))
        out = Path(a["output_path"])
        write_model(out / "0", images[:3])           # small fragment as sparse/0
        write_model(out / "1", images[: max(1, int(len(images) * 0.9))])  # the real one
        return 0
    if cmd == "image_undistorter":
        out = Path(a["output_path"])
        src_model = Path(a["input_path"])
        (out / "images").mkdir(parents=True, exist_ok=True)
        for img in Path(a["image_path"]).glob("*.jpg"):
            shutil.copy2(img, out / "images" / img.name)
        (out / "sparse").mkdir(parents=True, exist_ok=True)
        for f in src_model.glob("*.bin"):
            shutil.copy2(f, out / "sparse" / f.name)
        (out / "stereo").mkdir(exist_ok=True)
        (out / "run-colmap-geometric.sh").write_text("#")
        return 0
    print(f"fake colmap: unknown command {cmd}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
