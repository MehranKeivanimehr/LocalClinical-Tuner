# FAQ

Short answers with links to the evidence. All numbers come from the 300-example held-out test set
([results/comparison_table.md](../results/comparison_table.md), [results/metrics.json](../results/metrics.json)).

## Is this for real clinical use?

**No.** LocalClinical-Tuner is a research and portfolio project. It is not a medical device, has no regulatory
clearance and has not been validated in any clinical setting. Its outputs can be incomplete or wrong. Do not use it
to diagnose, treat, triage or document the care of real patients, or to support any clinical decision. See the
[Disclaimer](../README.md#disclaimer).

## Did you use real patient data?

**No.** Training and evaluation use only:

* **Synthetic notes (70%)**, written by an LLM from sampled fictional scenarios (specialty × presenting problem × note
  style × difficulty).
* **Public, role-played doctor-patient dialogues (30%)** from ACI-Bench and MTS-Dialog, released under CC BY 4.0,
  license-checked and scrubbed of identifier patterns.

Credentialed real-patient datasets (MIMIC, n2c2/i2b2) were excluded by policy. Sources, licenses and filtering
statistics are in the [data card](../data/DATA_CARD.md). Do not enter real patient information into the demo.

## Are the gold labels clinician-verified?

No. Labels are model-generated (or, for some public records, reused human-written references) and filtered
automatically by clinical rule checks and an independent LLM critic. They were not reviewed by clinicians. A seeded
random sample for human spot-checks is in [data/review/review_sample.md](../data/review/review_sample.md). Because
the model learns from these labels, it also learns their mistakes; the README's
[qualitative examples](../README.md#qualitative-examples-side-by-side) show one.

## Why is ORPO in the pipeline if it didn't help?

Because a negative result is still a result, and the stage is a working, reusable part of the pipeline:

* ORPO was tuned on the validation split, where it looked slightly better than SFT. On the test set it is
  **equivalent to SFT** on every reference metric (all differences inside the 95% CIs), and the LLM judge slightly
  prefers plain SFT (SFT+ORPO vs SFT: win 5%, tie 74%, loss 21%).
* The README explains [why it added little](../README.md#why-the-orpo-stage-added-little): after SFT the model already
  makes almost none of the errors the preference pairs target (0% medication hallucination, 100% negation accuracy),
  and the chosen/rejected answers differ in only a small part of each answer.
* The pipeline also fixes a real problem in TRL's ORPO loss (the NLL term covered the prompt as well as the response),
  which made ORPO *worse* than SFT before the fix.
* You can ship the SFT model instead: `uv run python serving/export_gguf.py --merged outputs/sft/merged`.

## SFT vs RAG vs 2-shot prompting: when is fine-tuning worth it here?

All three were run on the same base model, prompts, decoding and test set:

| | Base | 2-shot | RAG (2 retrieved training examples) | SFT |
|---|---|---|---|---|
| Entity F1 (relaxed) | 0.630 | 0.677 | 0.738 | 0.829 |
| SOAP ROUGE-L | 0.583 | 0.635 | 0.677 | 0.749 |
| Summary ROUGE-L | 0.383 | 0.427 | 0.454 | 0.558 |
| Summaries within the length limit | 98% | 89% | 80% | 99% |
| SOAP notes with a medication not in the note | 0% | 3% | 0% | 0% |

* **RAG closes roughly half of the gap** without any training. If you cannot train, it is the better of the two
  training-free options.
* **Fine-tuning still wins** on every reference metric and on format: examples in the prompt push summaries past the
  length limit, and 2-shot prompting copied a medication (*Toprol*) from its worked examples into 3 of 100 SOAP notes.
* **Fine-tuned prompts are shorter**: about 750 tokens on average, against about 2,200 (2-shot) and 3,300 (RAG), so
  inference is faster and needs a smaller context window.
* Fine-tuning is worth it when the task format is fixed, you run the model many times, and you can afford one 24 GB
  GPU for about 40 minutes of SFT (measured on an RTX A6000; 55 minutes with ORPO). It is not worth it if your labels are poor: the model copies their conventions
  and their mistakes.

## What hardware do I need?

| What | Hardware | Measured on the reference run |
|---|---|---|
| **Run the demo** (Q4_K_M GGUF with Ollama) | ~5 GB of GPU memory | 5.1 GB at an 8,192-token context; a summary in 3.8 s, a SOAP note or entity extraction in about 10 s (RTX A6000) |
| Run on CPU or Apple Silicon | Ollama supports it | not benchmarked here; expect it to be slower |
| **SFT** (QLoRA) | one 24 GB GPU | 13.7 GB peak, 39 min |
| **ORPO** (QLoRA) | one 24 GB GPU | 18.5 GB peak, 16 min |
| Data generation, critic, LLM judge (optional) | 2 × 48 GB for the 32B/72B models | swap in smaller local models via `training/config.yaml` |

On a 16 GB GPU, train SFT with `--set sft.per_device_batch_size=1 --set sft.gradient_accumulation_steps=16`. Details:
[Hardware](../README.md#hardware).

## Where do I get the model weights?

The weights are not stored in git (the Q4_K_M GGUF is 4.4 GB) and are not published yet. Build them with the
pipeline ([Reproduce everything](../README.md#reproduce-everything)) or copy `localclinical-q4_k_m.gguf` from a
machine where the pipeline ran into `outputs/gguf/`. The reference run's SHA-256 checksums are listed in the
[Quickstart](../README.md#quickstart-run-the-demo).

## Can I bring my own notes?

Yes. Put JSONL files with a `note` field (labels optional) in a folder and import them:

```bash
uv run python data/import_external_notes.py --inputs my_notes.jsonl --private --source-name private
```

* `--private` is for real or sensitive text: identifiers are scrubbed (not rejected), missing labels are produced
  only by the **local** teacher model, and the quality filter never sends these records to a remote API.
* `--relabel` ignores any labels in the files and labels everything with the local teacher.
* Keep the whole workflow on hardware you control: point every endpoint in `training/config.yaml` at local servers,
  and remember that local inference does not by itself satisfy your privacy obligations (access control, logs,
  storage and backups still matter).
* Scrubbing is best-effort pattern matching, not certified de-identification. Using real patient notes requires the
  approvals your institution and jurisdiction require.

## Does anything leave my machine when I run the demo?

Not in the reference setup. Ollama and the Gradio UI bind to `127.0.0.1`, Gradio analytics are disabled and no share
link is created, and `scripts/run_demo.sh` starts Ollama with `OLLAMA_NO_CLOUD=1`. If an Ollama server is already
running, the script reuses it as it is, so set `OLLAMA_NO_CLOUD=1` for that server yourself. See
[How this repository keeps inference local](../README.md#how-this-repository-keeps-inference-local-verified).

## Can I use a different base model?

Yes. Set `model.base_model` in `training/config.yaml` (for example `meta-llama/Llama-3.1-8B-Instruct`) and re-run from
the SFT stage. The chat markers for response-only loss are detected from the tokenizer's chat template. Check the
base model's license before you redistribute the result.
