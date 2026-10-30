#!/usr/bin/env bash
# One-time setup on an Apple Silicon Mac. Safe to re-run.
#
#   - Homebrew packages: ffmpeg, colmap, python@3.12, rustup
#   - Builds Brush (Gaussian splat trainer, Metal GPU) into tools/brush
#   - Creates .venv with the Python dependencies
#   - Creates .env from .env.example
#
# Nothing here uses CUDA or NVIDIA.

set -euo pipefail
cd "$(dirname "$0")/.."
ROOT="$(pwd)"

# Pinned Brush commit (Oct 2026). Its CLI flags were checked when this project was written.
BRUSH_REPO="https://github.com/ArthurBrussee/brush.git"
BRUSH_COMMIT="${BRUSH_COMMIT:-1388f74c6fe0236f68ee4915564bf00e9d2e3747}"

say() { printf "\n\033[1;32m==> %s\033[0m\n" "$*"; }
die() { printf "\n\033[1;31mERROR: %s\033[0m\n" "$*" >&2; exit 1; }

[[ "$(uname -s)" == "Darwin" ]] || die "This script is for macOS."
[[ "$(uname -m)" == "arm64" ]] || die "This project needs an Apple Silicon (M-series) Mac."

say "Checking Xcode command line tools (git, compilers)"
if ! xcode-select -p >/dev/null 2>&1; then
  xcode-select --install || true
  die "Finish the Xcode Command Line Tools install that just opened, then re-run this script."
fi

say "Checking Homebrew"
if ! command -v brew >/dev/null 2>&1; then
  die "Homebrew is not installed. Install it from https://brew.sh then re-run this script."
fi

say "Installing ffmpeg, colmap, python@3.12, rustup (brew)"
brew install ffmpeg colmap python@3.12 rustup

say "Setting up Rust (needed to build Brush)"
RUSTUP_BIN="$(brew --prefix rustup)/bin"
export PATH="$RUSTUP_BIN:$HOME/.cargo/bin:$PATH"
if ! command -v rustc >/dev/null 2>&1 || ! rustup show active-toolchain >/dev/null 2>&1; then
  rustup default stable
fi
rustup update stable
rustc --version

say "Building Brush at ${BRUSH_COMMIT:0:12} (first build takes ~5-15 minutes)"
mkdir -p tools
if [[ ! -d tools/brush/.git ]]; then
  git clone "$BRUSH_REPO" tools/brush
fi
git -C tools/brush fetch --quiet origin
git -C tools/brush checkout --quiet "$BRUSH_COMMIT"
(cd tools/brush && cargo build --release -p brush-app)
BRUSH_BIN="$ROOT/tools/brush/target/release/brush"
[[ -x "$BRUSH_BIN" ]] || die "Brush build finished but $BRUSH_BIN is missing."
"$BRUSH_BIN" --help >/dev/null || die "Brush was built but does not run."

say "Creating Python virtual environment (.venv)"
PY="$(brew --prefix python@3.12)/bin/python3.12"
[[ -x "$PY" ]] || PY="$(command -v python3)"
if [[ ! -d .venv ]]; then
  "$PY" -m venv .venv
fi
# shellcheck disable=SC1091
source .venv/bin/activate
python -m pip install --upgrade pip --quiet
python -m pip install -r requirements.txt --quiet

say "Writing .env"
if [[ ! -f .env ]]; then
  cp .env.example .env
fi
if grep -q '^BRUSH_BIN=' .env; then
  sed -i '' "s|^BRUSH_BIN=.*|BRUSH_BIN=$BRUSH_BIN|" .env
else
  echo "BRUSH_BIN=$BRUSH_BIN" >> .env
fi

say "Running the doctor"
python scripts/doctor.py || true

say "Setup finished. Next: scripts/start.sh  (then open http://127.0.0.1:8000)"
