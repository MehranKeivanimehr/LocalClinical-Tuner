#!/usr/bin/env bash
# LocalClinical-Tuner - end-to-end pipeline:
#   data (Claude synthetic + public HF dialogues, quality filter) -> SFT -> preference pairs -> ORPO
#   -> evaluation (base / SFT / ORPO / GGUF) -> LLM judge -> GGUF export -> report
#
# Every stage is idempotent: finished stages are skipped (FORCE=1 re-runs everything; STAGES="sft orpo" runs a
# subset). vLLM servers are started/stopped automatically and only on the GPUs you give it.
#
#   bash scripts/run_full_pipeline.sh
#   GPUS=0,1 TRAIN_GPU=0 SERVE_GPU=1 VLLM_BIN=~/vllm-env/bin/vllm bash scripts/run_full_pipeline.sh
#
# Environment knobs:
#   GPUS       two GPUs for the 2-GPU servers (teacher/critic Qwen3-32B FP8, judge Qwen2.5-72B FP8)   [6,7]
#   TRAIN_GPU  GPU for Unsloth training and BERTScore                                                 [6]
#   SERVE_GPU  GPU for single-GPU vLLM/Ollama inference servers                                        [7]
#   VLLM_BIN   vLLM executable (vLLM runs in its own environment; see README)                          [vllm]
#   TEACHER_MODEL / JUDGE_MODEL   HF ids or local paths                     [Qwen/Qwen3-32B / Qwen/Qwen2.5-72B-Instruct]
#   DATA_SOURCE  where the synthetic 70% comes from:                                                     [api]
#                  api      - Claude via the Anthropic API (ANTHROPIC_API_KEY in .env; falls back to local teacher)
#                  external - notes you generated elsewhere, e.g. in the Claude app (data/external/claude_app/)
#                  local    - the local Qwen3-32B teacher only (no API, no app)
#   GENERATOR_LABEL  provenance label for DATA_SOURCE=external                                          [claude-app]
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
CONFIG="${CONFIG:-training/config.yaml}"
GPUS="${GPUS:-6,7}"
TRAIN_GPU="${TRAIN_GPU:-6}"
SERVE_GPU="${SERVE_GPU:-7}"
export VLLM_BIN="${VLLM_BIN:-vllm}"
TEACHER_MODEL="${TEACHER_MODEL:-Qwen/Qwen3-32B}"
JUDGE_MODEL="${JUDGE_MODEL:-Qwen/Qwen2.5-72B-Instruct}"
FORCE="${FORCE:-0}"
DATA_SOURCE="${DATA_SOURCE:-api}"
GENERATOR_LABEL="${GENERATOR_LABEL:-claude-app}"
STAGES="${STAGES:-data sft prefs orpo eval export judge report}"
PY=(uv run --all-extras python)
SRV=scripts/vllm_server.sh
LOG_DIR="$ROOT/outputs/logs"
mkdir -p "$LOG_DIR"

BASE_MODEL="$("${PY[@]}" -c "from clinical_tuner.config import load_config; print(load_config('$CONFIG').model.base_model)")"
SMALL_ARGS=(--max-model-len 8192 --gpu-memory-utilization 0.85 --max-num-seqs 128 --generation-config vllm)
TEACHER_ARGS=(--quantization fp8 --max-model-len 8192 --gpu-memory-utilization 0.90 --max-num-seqs 128 --generation-config vllm)
JUDGE_ARGS=(--quantization fp8 --max-model-len 12288 --gpu-memory-utilization 0.92 --max-num-seqs 32 --generation-config vllm)

STARTED=()
cleanup() {
  for name in "${STARTED[@]:-}"; do [[ -n "$name" ]] && $SRV stop "$name" >/dev/null 2>&1 || true; done
  if [[ -f "$LOG_DIR/ollama_eval.pid" ]]; then kill "$(cat "$LOG_DIR/ollama_eval.pid")" 2>/dev/null || true; rm -f "$LOG_DIR/ollama_eval.pid"; fi
}
trap cleanup EXIT

banner() { printf '\n==================== %s [%s] ====================\n' "$1" "$(date '+%H:%M:%S')"; }
want() { [[ " $STAGES " == *" $1 "* ]]; }
done_or_force() { [[ "$FORCE" == "1" ]] && return 1; [[ -e "$1" ]]; }

check_gpus() {  # refuse to start on GPUs that other processes are already using heavily
  local ids="$1"
  for id in ${ids//,/ }; do
    used=$(nvidia-smi --query-gpu=memory.used --format=csv,noheader,nounits -i "$id" | tr -d ' ')
    if (( used > 2000 )); then
      echo "GPU $id already has ${used} MiB in use - free it or choose other GPUs (GPUS/TRAIN_GPU/SERVE_GPU)." >&2
      exit 1
    fi
  done
}

serve() {  # serve <name> <model> <port> <gpus> <args...>
  local name="$1" model="$2" port="$3" gpus="$4"; shift 4
  check_gpus "$gpus"
  $SRV start "$name" "$model" "$port" "$gpus" "$@"
  STARTED+=("$name")
  $SRV wait "$port" 1800
}
unserve() { $SRV stop "$1"; }

# ---------------------------------------------------------------------------------------------------------------
if want data; then
  banner "DATA: synthetic (Claude) + public HF dialogues -> quality filter -> splits"
  if done_or_force data/processed/test.jsonl; then echo "data/processed exists - skipping"; else
    serve teacher "$TEACHER_MODEL" 8100 "$GPUS" "${TEACHER_ARGS[@]}"   # local fallback generator + critic
    case "$DATA_SOURCE" in
      api)      "${PY[@]}" data/generate_synthetic_notes.py --config "$CONFIG" ;;
      external) "${PY[@]}" data/validate_batch.py --all --quiet || echo "(invalid lines are rejected by the importer)"
                "${PY[@]}" data/import_external_notes.py --config "$CONFIG" --generator-label "$GENERATOR_LABEL" ;;
      local)    "${PY[@]}" data/generate_synthetic_notes.py --config "$CONFIG" --set data.generation.primary=teacher ;;
      *) echo "Unknown DATA_SOURCE=$DATA_SOURCE (api|external|local)" >&2; exit 2 ;;
    esac
    "${PY[@]}" data/ingest_hf_datasets.py --config "$CONFIG"
    # Critic pass 1 (Qwen3-32B): every record that Qwen3-32B did not write. Results are cached.
    "${PY[@]}" data/quality_filter.py --config "$CONFIG" --cache-only
    unserve teacher
    # Critic pass 2 (Qwen2.5-72B): records written/labelled by the local teacher need a different critic model.
    # Cached verdicts from pass 1 are reused; then the final outputs, review sample and data card are written.
    serve judge "$JUDGE_MODEL" 8200 "$GPUS" "${JUDGE_ARGS[@]}"
    "${PY[@]}" data/quality_filter.py --config "$CONFIG"
    unserve judge
    "${PY[@]}" scripts/prepare_dataset.py --config "$CONFIG"
    "${PY[@]}" scripts/validate_dataset.py --config "$CONFIG"      # leakage / prompt / label / length checks
  fi
fi

if want sft; then
  banner "SFT (Unsloth QLoRA) on GPU $TRAIN_GPU  ||  base-model test generations on GPU $SERVE_GPU"
  if done_or_force outputs/sft/merged/config.json; then echo "SFT model exists - skipping"; else
    check_gpus "$TRAIN_GPU"
    CUDA_VISIBLE_DEVICES="$TRAIN_GPU" "${PY[@]}" training/sft_train.py --config "$CONFIG" > "$LOG_DIR/sft.log" 2>&1 &
    SFT_PID=$!
    if ! done_or_force results/generations/base.jsonl || [[ "$FORCE" == "1" ]]; then
      serve base "$BASE_MODEL" 8300 "$SERVE_GPU" "${SMALL_ARGS[@]}"
      "${PY[@]}" training/evaluate.py --config "$CONFIG" generate --model base
      unserve base
    fi
    wait "$SFT_PID" || { echo "SFT failed - see $LOG_DIR/sft.log" >&2; exit 1; }
  fi
fi

if want prefs; then
  banner "PREFERENCE PAIRS (on-policy SFT samples + hard negatives)  +  SFT test generations"
  if done_or_force data/processed/pref_pairs_train.jsonl && done_or_force results/generations/sft.jsonl; then
    echo "preference pairs and SFT generations exist - skipping"; else
    serve sft outputs/sft/merged 8301 "$SERVE_GPU" "${SMALL_ARGS[@]}"
    "${PY[@]}" scripts/build_preference_data.py --config "$CONFIG"
    "${PY[@]}" scripts/validate_dataset.py --config "$CONFIG" --skip-tokens   # + preference-pair checks
    "${PY[@]}" training/evaluate.py --config "$CONFIG" generate --model sft
    unserve sft
  fi
fi

if want orpo; then
  banner "ORPO (Unsloth QLoRA) on GPU $TRAIN_GPU"
  if done_or_force outputs/orpo/merged/config.json; then echo "ORPO model exists - skipping"; else
    check_gpus "$TRAIN_GPU"
    CUDA_VISIBLE_DEVICES="$TRAIN_GPU" "${PY[@]}" training/orpo_train.py --config "$CONFIG" 2>&1 | tee "$LOG_DIR/orpo.log"
  fi
fi

if want eval; then
  banner "EVAL: SFT+ORPO test generations"
  if done_or_force results/generations/orpo.jsonl && [[ $(wc -l < results/generations/orpo.jsonl) -ge $(wc -l < data/processed/test.jsonl) ]]; then
    echo "ORPO generations exist - skipping"; else
    serve orpo outputs/orpo/merged 8302 "$SERVE_GPU" "${SMALL_ARGS[@]}"
    "${PY[@]}" training/evaluate.py --config "$CONFIG" generate --model orpo
    unserve orpo
  fi
fi

if want export; then
  banner "EXPORT: GGUF (Q4_K_M, Q5_K_M) + Ollama Modelfile  ->  quantized-model test generations"
  "${PY[@]}" serving/export_gguf.py --config "$CONFIG"
  OLLAMA_BIN="${OLLAMA_BIN:-$(command -v ollama || echo "$ROOT/third_party/ollama/bin/ollama")}"
  if [[ -x "$OLLAMA_BIN" ]]; then
    check_gpus "$SERVE_GPU"
    # OLLAMA_VULKAN=0: Ollama's Vulkan backend ignores CUDA_VISIBLE_DEVICES and could grab any GPU.
    OLLAMA_HOST=127.0.0.1:11434 OLLAMA_MODELS="$ROOT/outputs/ollama_models" OLLAMA_VULKAN=0 OLLAMA_NUM_PARALLEL=4 \
      CUDA_VISIBLE_DEVICES="$SERVE_GPU" nohup "$OLLAMA_BIN" serve > "$LOG_DIR/ollama_eval.log" 2>&1 &
    echo $! > "$LOG_DIR/ollama_eval.pid"
    for _ in $(seq 1 60); do curl -sf http://127.0.0.1:11434/api/version >/dev/null && break; sleep 2; done
    OLLAMA_HOST=127.0.0.1:11434 "$OLLAMA_BIN" create localclinical -f serving/ollama_modelfile
    "${PY[@]}" training/evaluate.py --config "$CONFIG" generate --model gguf_q4
    kill "$(cat "$LOG_DIR/ollama_eval.pid")" 2>/dev/null || true; rm -f "$LOG_DIR/ollama_eval.pid"
  else
    echo "Ollama not found - skipping the quantized-model evaluation (install from https://ollama.com)."
  fi
fi

if want judge; then
  banner "SCORE (GPU $TRAIN_GPU) + LLM JUDGE ($JUDGE_MODEL FP8 on GPUs $GPUS)"
  CUDA_VISIBLE_DEVICES="$TRAIN_GPU" "${PY[@]}" training/evaluate.py --config "$CONFIG" score
  serve judge "$JUDGE_MODEL" 8200 "$GPUS" "${JUDGE_ARGS[@]}"
  "${PY[@]}" training/evaluate.py --config "$CONFIG" judge
  unserve judge
fi

if want report; then
  banner "REPORT"
  "${PY[@]}" training/evaluate.py --config "$CONFIG" report
  "${PY[@]}" scripts/plot_results.py --config "$CONFIG"
  "${PY[@]}" scripts/update_readme.py --config "$CONFIG"
  echo "Results: results/comparison_table.md, results/metrics.json, results/sample_outputs.md, results/figures/"
fi
