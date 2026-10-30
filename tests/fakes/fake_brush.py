#!/usr/bin/env python3
"""Stand-in for the `brush` binary in tests: logs progress and writes a tiny PLY."""

import struct
import sys
from pathlib import Path

HELP = """Brush - universal splats
Usage: brush [OPTIONS] [PATH_OR_URL]
      --with-viewer
      --total-train-iters <TOTAL_TRAIN_ITERS>
      --max-resolution <MAX_RESOLUTION>
      --sh-degree <SH_DEGREE>
      --max-splats <MAX_SPLATS>
      --export-every <EXPORT_EVERY>
      --export-path <EXPORT_PATH>
      --export-name <EXPORT_NAME>
"""

PROPS = ["x", "y", "z", "f_dc_0", "f_dc_1", "f_dc_2", "opacity",
         "scale_0", "scale_1", "scale_2", "rot_0", "rot_1", "rot_2", "rot_3"]


def write_ply(path: Path, n: int) -> None:
    header = ["ply", "format binary_little_endian 1.0", "comment Exported from Brush",
              "comment Vertical axis: 0 -1 0", f"element vertex {n}"]
    header += [f"property float {p}" for p in PROPS] + ["end_header"]
    with open(path, "wb") as fh:
        fh.write(("\n".join(header) + "\n").encode())
        for i in range(n):
            fh.write(struct.pack(f"<{len(PROPS)}f", *([float(i)] * len(PROPS))))


def main(argv):
    if "--help" in argv:
        print(HELP)
        return 0
    a = {}
    i = 1
    while i < len(argv):
        if argv[i].startswith("--") and i + 1 < len(argv):
            a[argv[i][2:]] = argv[i + 1]
            i += 2
        else:
            i += 1
    dataset = Path(argv[0])
    if not list(dataset.glob("**/cameras.bin")):
        print("no colmap data", file=sys.stderr)
        return 3
    iters = int(a["total-train-iters"])
    for it in range(200, iters + 1, max(200, iters // 5)):
        print(f"INFO Refine iter {it}, {it * 10} splats.", flush=True)
    out = Path(a["export-path"])
    out.mkdir(parents=True, exist_ok=True)
    write_ply(out / a["export-name"].replace("{iter}", f"{iters:05d}"), 50)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
