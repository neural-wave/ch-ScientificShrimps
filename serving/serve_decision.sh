#!/usr/bin/env bash
# d3 A/B only (NL2Q_CHECKER=d3): decision model vllm-sr/d3 on GPU 1 -> System One API on :8002; drops off the GPU after 15 idle minutes (IDLE_MINUTES=0: never).
# Uses d3's own venv (pinned transformers etc.), kept apart from the project's. Weights and caches on scratch.
set -euo pipefail
S=${SCRATCH:-$([ -d "/data/scratch/$USER" ] && echo "/data/scratch/$USER" || echo "$HOME/.cache/nl2q")}  # weights, venvs, caches
export CUDA_VISIBLE_DEVICES=${GPU:-1} HF_HOME=$S/cache/huggingface XDG_CACHE_HOME=$S/cache TRITON_CACHE_DIR=$S/cache/triton
PKG=$(ls -d "$HF_HOME"/hub/models--vllm-sr--d3/snapshots/* | head -1)
exec "$S/venvs/d3/bin/python" -I "$(dirname "$0")/decision_server.py" --package "$PKG" \
  --port "${PORT:-8002}" --idle-minutes "${IDLE_MINUTES:-15}"
