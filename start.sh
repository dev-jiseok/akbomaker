#!/usr/bin/env bash
# Install the full server environment, build the UI, then serve both together.
set -eo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"

if [[ "${1:-}" == "--help" ]]; then
  echo 'Usage: ./start.sh [--setup-only | uvicorn options]'
  echo 'Example: ./start.sh --host 192.168.100.15 --port 8000'
  exit 0
fi

export NVM_DIR="${NVM_DIR:-$HOME/.nvm}"
if [[ ! -s "$NVM_DIR/nvm.sh" ]]; then
  echo 'nvm is required. Install nvm, then run this script again.' >&2
  exit 1
fi
source "$NVM_DIR/nvm.sh" --no-use
nvm use --silent || nvm install
if ! command -v uv >/dev/null && [[ -x "$HOME/.local/bin/uv" ]]; then
  export PATH="$HOME/.local/bin:$PATH"
fi
if ! command -v uv >/dev/null; then
  echo 'uv is required. Install uv, then run this script again.' >&2
  exit 1
fi
for tool in git ffmpeg ffprobe; do
  if ! command -v "$tool" >/dev/null; then
    echo "Missing $tool. On Ubuntu: sudo apt-get install git ffmpeg libsndfile1" >&2
    exit 1
  fi
done

if [[ ! -d .venv ]]; then
  uv venv --python 3.11 .venv
fi
uv run --no-project --python .venv/bin/python python -c \
  'import sys; sys.exit(0 if sys.version_info[:2] == (3, 11) else "The existing .venv must use Python 3.11. Move it aside and retry.")'

# Upstream audiotools test recordings are Git LFS files, not runtime assets.
GIT_LFS_SKIP_SMUDGE=1 uv pip install --python .venv/bin/python \
  --torch-backend cu128 -r backend/requirements-server.lock
npm ci
npm run build

if [[ "${1:-}" == "--setup-only" ]]; then
  echo 'Dependencies and frontend ready. Model initialization is checked when the server starts.'
  exit 0
fi
echo 'Checking engines and loading models before opening the server (first run may download weights)...'
exec uv run --no-project --python .venv/bin/python python -m uvicorn backend.app:app \
  --host 127.0.0.1 --port 8000 --workers 1 "$@"
