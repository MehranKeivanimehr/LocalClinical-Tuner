#!/usr/bin/env bash
# Manage local vLLM OpenAI-compatible servers used by the pipeline (teacher, judge, eval targets).
#
#   scripts/vllm_server.sh start <name> <model_path_or_id> <port> <gpu_ids> [extra vllm args...]
#   scripts/vllm_server.sh stop  <name>
#   scripts/vllm_server.sh wait  <port> [timeout_seconds]
#
# vLLM is deliberately run as a *separate process* (its own environment), so its pinned torch/transformers
# versions never conflict with the Unsloth training stack. Point VLLM_BIN at your vLLM executable, e.g.
#   VLLM_BIN=~/vllm-env/bin/vllm   or   VLLM_BIN="uvx --from vllm vllm"
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOG_DIR="${LOG_DIR:-$ROOT/outputs/logs}"
VLLM_BIN="${VLLM_BIN:-vllm}"
mkdir -p "$LOG_DIR"

wait_ready() {
  local port="$1" timeout="${2:-1800}" waited=0
  until curl -sf "http://127.0.0.1:${port}/v1/models" >/dev/null 2>&1; do
    if (( waited >= timeout )); then
      echo "vLLM on port ${port} not ready after ${timeout}s" >&2
      return 1
    fi
    sleep 5; waited=$((waited + 5))
  done
  echo "vLLM on port ${port} is ready (${waited}s)"
}

cmd="${1:-}"; shift || true
case "$cmd" in
  start)
    name="$1"; model="$2"; port="$3"; gpus="$4"; shift 4
    pid_file="$LOG_DIR/vllm_${name}.pid"
    if [[ -f "$pid_file" ]] && kill -0 "$(cat "$pid_file")" 2>/dev/null; then
      echo "vLLM '${name}' already running (pid $(cat "$pid_file"))"; exit 0
    fi
    n_gpus=$(awk -F, '{print NF}' <<<"$gpus")
    echo "Starting vLLM '${name}': model=${model} port=${port} gpus=${gpus} (TP=${n_gpus})"
    # vLLM JIT-compiles some kernels (FlashInfer) with ninja; make the vLLM env's bin dir visible.
    vllm_exe="${VLLM_BIN%% *}"
    if [[ "$vllm_exe" == */* ]]; then export PATH="$(dirname "$vllm_exe"):$PATH"; fi
    if ! command -v ninja >/dev/null 2>&1; then export VLLM_USE_FLASHINFER_SAMPLER=0; fi
    # shellcheck disable=SC2086
    CUDA_VISIBLE_DEVICES="$gpus" nohup $VLLM_BIN serve "$model" \
      --served-model-name "$name" \
      --port "$port" --host 127.0.0.1 \
      --tensor-parallel-size "$n_gpus" \
      "$@" > "$LOG_DIR/vllm_${name}.log" 2>&1 &
    echo $! > "$pid_file"
    echo "  pid $(cat "$pid_file"), log $LOG_DIR/vllm_${name}.log"
    ;;
  stop)
    name="$1"; pid_file="$LOG_DIR/vllm_${name}.pid"
    if [[ -f "$pid_file" ]]; then
      pid="$(cat "$pid_file")"
      if kill -0 "$pid" 2>/dev/null; then
        kill "$pid"
        for _ in $(seq 1 60); do kill -0 "$pid" 2>/dev/null || break; sleep 1; done
        kill -9 "$pid" 2>/dev/null || true
      fi
      rm -f "$pid_file"
      echo "Stopped vLLM '${name}'"
    else
      echo "No pid file for '${name}'"
    fi
    ;;
  wait)
    wait_ready "$1" "${2:-1800}"
    ;;
  *)
    echo "usage: $0 {start <name> <model> <port> <gpus> [args...]|stop <name>|wait <port> [timeout]}" >&2
    exit 2
    ;;
esac
