#!/usr/bin/env bash
# One-command local demo: serve the fine-tuned GGUF with Ollama and open the Gradio UI at http://127.0.0.1:7860.
# Everything binds to localhost; no note text leaves the machine.
#
#   bash scripts/run_demo.sh                          # uses serving/ollama_modelfile -> outputs/gguf/*.gguf
#   GGUF=/path/to/localclinical-q4_k_m.gguf bash scripts/run_demo.sh
#   bash scripts/run_demo.sh --compare-endpoint base  # extra args are passed to serving/gradio_app.py
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
MODEL_NAME="${MODEL_NAME:-localclinical}"
export OLLAMA_HOST="${OLLAMA_HOST:-127.0.0.1:11434}"
export OLLAMA_NO_CLOUD="${OLLAMA_NO_CLOUD:-1}"  # strictly local: disable Ollama's cloud features (remote inference)
# On a shared multi-GPU machine, pin Ollama to one GPU: CUDA_VISIBLE_DEVICES=<id> OLLAMA_VULKAN=0 bash scripts/run_demo.sh
# (Ollama's Vulkan backend ignores CUDA_VISIBLE_DEVICES).
OLLAMA_BIN="${OLLAMA_BIN:-$(command -v ollama || echo "$ROOT/third_party/ollama/bin/ollama")}"
mkdir -p outputs/logs

if [[ ! -x "$OLLAMA_BIN" ]]; then
  echo "Ollama is not installed. Get it from https://ollama.com/download  (Linux: curl -fsSL https://ollama.com/install.sh | sh)" >&2
  exit 1
fi

MODELFILE=serving/ollama_modelfile
if [[ -n "${GGUF:-}" ]]; then  # point a temporary Modelfile at a user-supplied GGUF
  MODELFILE="$(mktemp)"
  sed "s|^FROM .*|FROM $(realpath "$GGUF")|" serving/ollama_modelfile > "$MODELFILE"
fi
GGUF_PATH="$(awk '/^FROM /{print $2; exit}' "$MODELFILE")"
[[ "$GGUF_PATH" = /* ]] || GGUF_PATH="$ROOT/serving/$GGUF_PATH"
if [[ ! -f "$GGUF_PATH" ]]; then
  echo "GGUF not found at $GGUF_PATH. Train + export first (bash scripts/run_full_pipeline.sh) or set GGUF=..." >&2
  exit 1
fi

STARTED=0
if ! curl -sf "http://$OLLAMA_HOST/api/version" >/dev/null 2>&1; then
  echo "Starting Ollama on $OLLAMA_HOST ..."
  nohup "$OLLAMA_BIN" serve > outputs/logs/ollama_demo.log 2>&1 &
  OLLAMA_PID=$!
  STARTED=1
  trap '[[ $STARTED == 1 ]] && kill $OLLAMA_PID 2>/dev/null || true' EXIT
  for _ in $(seq 1 60); do curl -sf "http://$OLLAMA_HOST/api/version" >/dev/null 2>&1 && break; sleep 1; done
fi

"$OLLAMA_BIN" create "$MODEL_NAME" -f "$MODELFILE"
echo "Ollama OpenAI-compatible API: http://$OLLAMA_HOST/v1  (model: $MODEL_NAME)"
uv run --extra serve python serving/gradio_app.py --set "endpoints.ollama.model=$MODEL_NAME" \
  --set "endpoints.ollama.base_url=http://$OLLAMA_HOST/v1" "$@"
