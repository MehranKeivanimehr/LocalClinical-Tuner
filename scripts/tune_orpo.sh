#!/usr/bin/env bash
# Choose ORPO's odds-ratio weight (lambda, `orpo.beta`) on the VALIDATION split - never on the test split.
#
#   GPUS=0,1 VLLM_BIN=~/vllm-env/bin/vllm LAMBDAS="0.1 0.5" bash scripts/tune_orpo.sh
#
# 1. Trains one ORPO model per lambda into outputs/orpo_l<lambda> (an existing merged model is reused), one GPU each.
# 2. Generates validation-split outputs for SFT and every candidate with vLLM.
# 3. Scores them with the reference metrics (results/metrics_val.json) and runs the LLM judge pairwise against SFT,
#    in both presentation orders (results/judge_val/).
# 4. Prints the comparison. Selection rule: highest judge (win - loss) vs SFT; reference metrics break ties.
#    Adopt the winner by setting `orpo.beta` in training/config.yaml and moving its directory to outputs/orpo.
#
# EXTRA="tag=path ..." adds models that are already trained (e.g. a run with `orpo.nll_on_prompt=true`).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
CONFIG="${CONFIG:-training/config.yaml}"
GPUS="${GPUS:-0,1}"
LAMBDAS="${LAMBDAS:-0.1 0.5}"
EXTRA="${EXTRA:-}"
JUDGE_MODEL="${JUDGE_MODEL:-Qwen/Qwen2.5-72B-Instruct}"
export VLLM_BIN="${VLLM_BIN:-vllm}" TRL_EXPERIMENTAL_SILENCE=1
PY=(uv run --all-extras python)
SRV=scripts/vllm_server.sh
LOG_DIR="$ROOT/outputs/logs"
mkdir -p "$LOG_DIR"
SMALL_ARGS=(--max-model-len 8192 --gpu-memory-utilization 0.85 --max-num-seqs 128 --generation-config vllm)
JUDGE_ARGS=(--quantization fp8 --max-model-len 12288 --gpu-memory-utilization 0.92 --max-num-seqs 32 --generation-config vllm)
IFS=, read -ra GPU_LIST <<<"$GPUS"

STARTED=()
cleanup() { for name in "${STARTED[@]:-}"; do [[ -n "$name" ]] && $SRV stop "$name" >/dev/null 2>&1 || true; done; }
trap cleanup EXIT
banner() { printf '\n==================== %s [%s] ====================\n' "$1" "$(date '+%H:%M:%S')"; }

# --- 1. train one model per lambda (parallel across GPUs) ---------------------------------------------------------
banner "TRAIN ORPO candidates (lambda: $LAMBDAS) on GPUs $GPUS"
TAGS=(); MODELS=(); pids=(); i=0
for lam in $LAMBDAS; do
  tag="orpo_l$lam"; dir="outputs/$tag"
  TAGS+=("$tag"); MODELS+=("$dir/merged")
  if [[ -f "$dir/merged/config.json" ]]; then echo "$tag: reusing $dir/merged"; continue; fi
  gpu="${GPU_LIST[$((i % ${#GPU_LIST[@]}))]}"; i=$((i + 1))
  echo "$tag: training on GPU $gpu (log: $LOG_DIR/$tag.log)"
  CUDA_VISIBLE_DEVICES="$gpu" "${PY[@]}" training/orpo_train.py --config "$CONFIG" \
    --set orpo.beta="$lam" --set orpo.output_dir="$dir" > "$LOG_DIR/$tag.log" 2>&1 &
  pids+=($!)
  if (( ${#pids[@]} == ${#GPU_LIST[@]} )); then wait "${pids[@]}"; pids=(); fi
done
if (( ${#pids[@]} )); then wait "${pids[@]}"; fi
for e in $EXTRA; do TAGS+=("${e%%=*}"); MODELS+=("${e#*=}"); done

# --- 2. validation generations ------------------------------------------------------------------------------------
banner "GENERATE validation outputs"
serve_gen() {  # serve_gen <endpoint> <port> <model_path> <tag>
  $SRV start "$1" "$3" "$2" "${GPU_LIST[0]}" "${SMALL_ARGS[@]}"; STARTED+=("$1")
  $SRV wait "$2" 1800
  "${PY[@]}" training/evaluate.py --config "$CONFIG" generate --model "$1" --tag "$4" --split val
  $SRV stop "$1"; sleep 5
}
serve_gen sft 8301 outputs/sft/merged sft
for k in "${!TAGS[@]}"; do serve_gen orpo 8302 "${MODELS[$k]}" "${TAGS[$k]}"; done

# --- 3. score + judge ---------------------------------------------------------------------------------------------
banner "SCORE + JUDGE on the validation split"
CUDA_VISIBLE_DEVICES="${GPU_LIST[0]}" "${PY[@]}" training/evaluate.py --config "$CONFIG" score --split val \
  --models sft "${TAGS[@]}"
$SRV start judge "$JUDGE_MODEL" 8200 "$GPUS" "${JUDGE_ARGS[@]}"; STARTED+=(judge)
$SRV wait 8200 2400
PAIRS=(); for t in "${TAGS[@]}"; do PAIRS+=("$t:sft"); done
"${PY[@]}" training/evaluate.py --config "$CONFIG" judge --split val --models sft "${TAGS[@]}" --pairs "${PAIRS[@]}"
$SRV stop judge

# --- 4. report ----------------------------------------------------------------------------------------------------
banner "VALIDATION COMPARISON"
"${PY[@]}" - "${TAGS[@]}" <<'EOF'
import json
import sys

m = json.load(open("results/metrics_val.json"))
judge = m.get("judge", {})
rows = [("summary", "rougeL"), ("soap", "rougeL"), ("entities", "entity_f1_relaxed"), ("entities", "negation_accuracy")]


def val(model, task, key):
    v = m["models"].get(model, {}).get("by_task", {}).get(task, {}).get(key)
    return v["mean"] if isinstance(v, dict) else v


print("| model | summary R-L | SOAP R-L | entity F1 | negation | judge overall | vs SFT win/tie/loss |")
print("|---|---|---|---|---|---|---|")
ranked = []
for model in ["sft", *sys.argv[1:]]:
    refs = [val(model, t, k) for t, k in rows]
    absolute = (judge.get("absolute", {}).get(model) or {}).get("overall", {}).get("mean")
    pair = judge.get("pairwise", {}).get(f"{model}_vs_sft")
    wtl = f"{100 * pair['win']:.0f}/{100 * pair['tie']:.0f}/{100 * pair['loss']:.0f}" if pair else "-"
    score = f"{absolute:.2f}" if absolute is not None else "-"
    print(f"| {model} | " + " | ".join(f"{x:.3f}" if x is not None else "-" for x in refs) + f" | {score} | {wtl} |")
    if pair:
        ranked.append((pair["win"] - pair["loss"], sum(x or 0 for x in refs[:3]), model))
if ranked:
    best = max(ranked)
    verdict = "beats" if best[0] > 0 else "does NOT beat"
    print(f"\nBest candidate on validation: {best[2]} (judge win-loss vs SFT {best[0]:+.2f}) - it {verdict} SFT.")
EOF
