"""Running external tools (ffmpeg, colmap, brush) with logging and cancellation."""

from __future__ import annotations

import os
import shutil
import subprocess
import threading
import time
from collections import deque
from pathlib import Path
from typing import Callable, Sequence

CancelCheck = Callable[[], bool]
LineCallback = Callable[[str], None]


class ToolError(RuntimeError):
    """An external tool failed. `tail` holds the last lines of its output."""

    def __init__(self, message: str, tail: str = ""):
        super().__init__(message)
        self.tail = tail


class JobCancelled(RuntimeError):
    pass


def tool_exists(binary: str) -> bool:
    return bool(binary) and (Path(binary).is_file() or shutil.which(binary) is not None)


def log_line(log_path: Path, text: str) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%H:%M:%S")
    with open(log_path, "a", encoding="utf-8") as fh:
        for line in text.rstrip("\n").splitlines() or [""]:
            fh.write(f"[{stamp}] {line}\n")


def run(cmd: Sequence[str], log_path: Path, *, cancel_check: CancelCheck | None = None,
        on_line: LineCallback | None = None, env: dict[str, str] | None = None,
        cwd: Path | None = None, description: str = "") -> None:
    """Run `cmd`, streaming stdout+stderr into `log_path`.

    Raises ToolError on a non-zero exit and JobCancelled if `cancel_check()`
    becomes true while the tool is running (the process is terminated).
    """
    cmd = [str(c) for c in cmd]
    if not tool_exists(cmd[0]):
        raise ToolError(f"Tool not found: {cmd[0]}. Run scripts/doctor.py to check your setup.")
    log_line(log_path, f"$ {' '.join(cmd)}" + (f"   # {description}" if description else ""))
    full_env = dict(os.environ)
    if env:
        full_env.update(env)

    tail: deque[str] = deque(maxlen=40)
    proc = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, cwd=str(cwd) if cwd else None,
        env=full_env, text=True, errors="replace", bufsize=1,
    )

    cancelled = threading.Event()

    def watch_cancel() -> None:
        while proc.poll() is None:
            if cancel_check is not None and cancel_check():
                cancelled.set()
                proc.terminate()
                try:
                    proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    proc.kill()
                return
            time.sleep(1.0)

    watcher = threading.Thread(target=watch_cancel, daemon=True)
    watcher.start()

    assert proc.stdout is not None
    with open(log_path, "a", encoding="utf-8") as log:
        for raw in proc.stdout:
            # Progress bars use carriage returns; keep only the latest state.
            line = raw.rstrip("\n").split("\r")[-1]
            if not line.strip():
                continue
            log.write(line + "\n")
            log.flush()
            tail.append(line)
            if on_line is not None:
                try:
                    on_line(line)
                except Exception:  # progress parsing must never kill a job
                    pass
    code = proc.wait()
    watcher.join(timeout=2)

    if cancelled.is_set():
        raise JobCancelled("Cancelled by user")
    if code != 0:
        name = Path(cmd[0]).name
        raise ToolError(f"{name} {cmd[1] if len(cmd) > 1 else ''} failed (exit code {code})".strip(),
                        "\n".join(tail))


def capture(cmd: Sequence[str], timeout: float = 60) -> str:
    """Run a quick command and return stdout+stderr (used for -h / version probes)."""
    try:
        out = subprocess.run([str(c) for c in cmd], capture_output=True, text=True,
                             timeout=timeout, errors="replace")
        return (out.stdout or "") + (out.stderr or "")
    except (OSError, subprocess.TimeoutExpired) as exc:
        return f"<error: {exc}>"
