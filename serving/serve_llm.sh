#!/usr/bin/env bash
# SPARQL generator on GPU 0 -> OpenAI-compatible API on :8001 (default Qwen3.8-27B FP8; MODEL=... to compare others).
# Thinking is switched per request (chat_template_kwargs). Weights, venv and JIT caches live on scratch.
# Shared GPUs: vLLM sleep mode is on and a watcher puts the engine to sleep after 15 idle minutes;
# nl2q.translation.llm wakes it on the next call (POST /wake_up). IDLE_MINUTES=0 keeps it awake (judging).
set -euo pipefail
S=${SCRATCH:-$([ -d "/data/scratch/$USER" ] && echo "/data/scratch/$USER" || echo "$HOME/.cache/nl2q")}  # weights, venvs, caches
export PATH=$S/venvs/vllm/bin:$PATH                          # vLLM shells out to ninja for kernel JIT
export CUDA_VISIBLE_DEVICES=${GPU:-0} HF_HOME=$S/cache/huggingface XDG_CACHE_HOME=$S/cache \
       VLLM_CACHE_ROOT=$S/cache/vllm TRITON_CACHE_DIR=$S/cache/triton VLLM_SERVER_DEV_MODE=1
PORT=${PORT:-8001}
[ "${IDLE_MINUTES:-15}" = 0 ] || python3 "$(dirname "$0")/idle_sleep.py" "http://localhost:$PORT" --minutes "${IDLE_MINUTES:-15}" &
exec vllm serve "${MODEL:-Qwen/Qwen3.8-27B-FP8}" --served-model-name "${SERVED_NAME:-qwen3.8-27b}" --port "$PORT" \
  --language-model-only --max-model-len 32768 --max-num-seqs 64 --gpu-memory-utilization 0.90 \
  --reasoning-parser qwen3 --speculative-config '{"method":"mtp","num_speculative_tokens":3}' --enable-sleep-mode
# --max-num-seqs: the default (1024) exceeds the hybrid model's Mamba cache blocks (~995) and aborts startup.
