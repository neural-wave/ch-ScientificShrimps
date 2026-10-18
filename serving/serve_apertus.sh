#!/usr/bin/env bash
# Question writer for the synthetic evaluation set (nl2q.tools.kgsynth), never the translator: Apertus v1.5 8B,
# Artificialy SA's FP8-DYNAMIC checkpoint, in the swiss-ai vLLM fork (upstream vLLM lacks the architecture).
# One container per GPU, published on 127.0.0.1 only:  GPU=2 PORT=8020 UTIL=0.40 serving/serve_apertus.sh
# The checkpoint ships as one model.safetensors, which the fork's Apertus loader refuses ("Cannot find any model
# weights"): re-split it once with apertus-knowledge/quantization/resplit-apertus-layout.py (MODEL_DIR is the result).
# Shared GPUs: UTIL is a share of the whole card, so size it below the free memory; the idle watcher puts the engine
# to sleep after 15 minutes. Stop it with: docker stop nl2q-apertus-g$GPU
set -euo pipefail
S=${SCRATCH:-/data/scratch/$USER}
GPU=${GPU:?GPU=2 or 3}; PORT=${PORT:-802$GPU}; UTIL=${UTIL:-0.40}
MODEL_DIR=${MODEL_DIR:-$S/models/apertus-v15-8b-fp8-dynamic}
IMAGE=${IMAGE:-ghcr.io/swiss-ai/vllm_apertus_1.5_release:latest-amd64}
CACHE=$S/cache/apertus-g$GPU; mkdir -p "$CACHE"
docker run -d --rm --name "nl2q-apertus-g$GPU" --gpus "\"device=$GPU\"" --ipc=host --user "$(id -u):$(id -g)" \
  -e USER=nl2q -e HOME=/cache -e VLLM_CACHE_ROOT=/cache/vllm -e TRITON_CACHE_DIR=/cache/triton \
  -e TORCHINDUCTOR_CACHE_DIR=/cache/inductor -e HF_HUB_OFFLINE=1 -e VLLM_SERVER_DEV_MODE=1 \
  -p "127.0.0.1:$PORT:$PORT" -v "$MODEL_DIR:/models:ro" -v "$CACHE:/cache" "$IMAGE" \
  vllm serve /models --served-model-name apertus-8b --host 0.0.0.0 --port "$PORT" --chat-template-content-format string \
  --gpu-memory-utilization "$UTIL" --max-model-len 8192 --max-num-seqs 64 --enforce-eager ${SLEEP_MODE---enable-sleep-mode}
# --enforce-eager: the image was only measured on sm_86/sm_89; eager mode skips graph capture on sm_120 (Blackwell).
[ "${IDLE_MINUTES:-15}" = 0 ] || nohup python3 "$(dirname "$0")/idle_sleep.py" "http://localhost:$PORT" \
  --minutes "${IDLE_MINUTES:-15}" > "$S/logs/apertus-g$GPU-idle.log" 2>&1 &
