---
license: apache-2.0
base_model: Qwen/Qwen2.5-7B-Instruct
base_model_relation: finetune
language:
  - en
library_name: gguf
pipeline_tag: text-generation
tags:
  - clinical-nlp
  - medical
  - gguf
  - ollama
  - qlora
  - orpo
  - unsloth
  - local-llm
  - privacy
datasets:
  - mkieffer/ACI-Bench
  - lyumengxian/MTS-Dialog
---

<!--
Paste this file as README.md of the Hugging Face model repo. Before publishing, replace every <hf-user> with the
Hugging Face account name.
-->

# LocalClinical-Tuner 7B (GGUF)

A **research model** for clinical-documentation tasks that runs entirely on local hardware: Qwen2.5-7B-Instruct
fine-tuned with QLoRA SFT followed by ORPO, exported to GGUF for Ollama and llama.cpp.

> **Not for clinical use.** This model is not a medical device, has no regulatory clearance and has not been validated
> on real patients. It was trained and evaluated **without any real patient data**. Do not use it to make or support
> clinical decisions, and do not use it on real patient records.

* **Code, data pipeline and full evaluation:** https://github.com/MehranKeivanimehr/LocalClinical-Tuner
* **Files:** `localclinical-q4_k_m.gguf` (4.36 GB, used for the reported GGUF results), `localclinical-q5_k_m.gguf`
  (5.07 GB, not evaluated separately), `Modelfile` (Ollama)

## Model description

| | |
|---|---|
| Base model | [Qwen/Qwen2.5-7B-Instruct](https://huggingface.co/Qwen/Qwen2.5-7B-Instruct) (Apache-2.0) |
| Stage 1 | Supervised fine-tuning with Unsloth QLoRA (4-bit NF4 base, LoRA r=32 on all attention and MLP projections), loss on response tokens only |
| Stage 2 | ORPO preference optimization (λ = 0.1, NLL term on response tokens only) on 426 pairs: the SFT model's own worst samples plus targeted clinical hard negatives |
| Export | merged to 16-bit, converted with llama.cpp, quantized to Q4_K_M and Q5_K_M |
| Context used in evaluation | 8,192 tokens |
| Language | English |

### Tasks

All three tasks share one system prompt and take an encounter note as input:

| Task | Output |
|---|---|
| **Summary** | 3-6 sentences of plain prose, at most 120 words |
| **SOAP note** | `SUBJECTIVE:` / `OBJECTIVE:` / `ASSESSMENT:` / `PLAN:` (numbered plan) |
| **Entity extraction** | JSON with `symptoms` (with `negated`), `diagnoses`, `medications` (`name` / `dose` / `frequency`), `procedures` |

The model was trained on one exact prompt format per task. For the best results, send the system prompt and task
instruction exactly as `clinical_tuner.prompts.build_messages(task, note)` builds them in the GitHub repository (the
Ollama `Modelfile` already contains the system prompt).

## Intended use

* Research and education: privacy-preserving, on-device language models; comparing fine-tuning with prompting and RAG
  on a narrow domain; local LLM serving.
* Demonstrations on **synthetic or public, de-identified** text.

### Out of scope

* **Any clinical use**: diagnosis, treatment, triage, documentation of real patient care, or clinical decision support.
* **Real protected health information (PHI)**. The model has not been evaluated on real clinical notes, and running it
  locally does not by itself meet privacy obligations (access control, logging, storage and backups still apply).
* Languages other than English, clinical coding (ICD / SNOMED), and open-ended medical question answering.

## Training data

1,000 encounters → 3,000 instruction examples (each encounter yields a summary, a SOAP note and an entity-extraction
example), split by encounter so that no note appears in two splits:

| Split | Encounters | Use |
|---|---|---|
| train | 725 | SFT (2,175 examples) |
| pref | 150 | preference pairs for ORPO (426 training pairs, 24 held out) |
| val | 25 | model and hyperparameter selection |
| test | 100 | reported results (300 examples) |

* **70% synthetic**: notes written by an LLM from sampled fictional scenarios (20 specialties, 164 presenting
  problems, 8 note styles, 3 difficulty levels). **No real patients.**
* **30% public**: role-played doctor-patient dialogues from ACI-Bench and MTS-Dialog (CC BY 4.0), license-checked,
  scrubbed of identifier patterns, filtered and near-deduplicated against the synthetic set.
* **Labels are mostly model-generated and not clinician-verified.** All records passed rule-based clinical checks and
  an independent LLM critic, but errors remain, and the model reproduces the conventions and mistakes of its labels.

Full details, including rejection statistics and label provenance:
[data card](https://github.com/MehranKeivanimehr/LocalClinical-Tuner/blob/main/data/DATA_CARD.md).

## Evaluation

300 held-out test examples (100 encounters: 70 synthetic, 26 MTS-Dialog, 4 ACI-Bench), identical prompts and greedy
decoding for every model.

| Metric | Base Qwen2.5-7B-Instruct | SFT | SFT+ORPO (16-bit) | SFT+ORPO Q4_K_M (this file) |
|---|---|---|---|---|
| Summary ROUGE-L | 0.383 | 0.558 | 0.548 | 0.544 |
| SOAP note ROUGE-L | 0.583 | 0.749 | 0.752 | 0.747 |
| Entity F1 (relaxed) | 0.630 | 0.829 | 0.829 | 0.816 |
| Entity F1 (strict) | 0.544 | 0.798 | 0.796 | 0.774 |
| Denied symptoms kept as denied | 97.6% | 100.0% | 100.0% | 99.9% |
| LLM-judge overall (1-5, 150 examples) | 4.373 | 4.573 | 4.384 | not judged |

Training-free baselines on the same base model reach entity F1 0.677 (2-shot prompting) and 0.738 (RAG with two
retrieved training examples).

**How to read these numbers:**

* Fine-tuning (SFT) produces the gains. The ORPO stage is statistically indistinguishable from SFT on reference
  metrics, and the LLM judge slightly prefers SFT (SFT+ORPO vs SFT: win 5%, tie 74%, loss 21%).
* Reference metrics (ROUGE-L, BERTScore, entity F1) measure agreement with mostly model-written references, not
  clinical correctness.
* The test set is mostly synthetic; gains are largest on the synthetic slice and the ACI-Bench slice has only 4
  encounters.
* The judge (Qwen2.5-72B-Instruct) is from the same model family as the base model and is noisy at small differences;
  treat it as an auxiliary signal.

Confidence intervals, per-source tables, judge details and side-by-side outputs:
[results](https://github.com/MehranKeivanimehr/LocalClinical-Tuner#headline-results).

## Quantization

| File | Quantization | Size | Evaluated |
|---|---|---|---|
| `localclinical-q4_k_m.gguf` | Q4_K_M | 4.36 GB | yes: within the 95% CIs of the 16-bit model on every metric (largest drop: strict entity F1 0.796 → 0.774) |
| `localclinical-q5_k_m.gguf` | Q5_K_M | 5.07 GB | no |

SHA-256:

```text
2fca1270b90fc502e257109ad0e3cc0c508028cf36eafc71a3f6b1480eab300b  localclinical-q4_k_m.gguf
5d4b3fe1ab68d97a8deba534b9e7e111140864e9679758f9f0e7745091095ac4  localclinical-q5_k_m.gguf
```

Measured on an RTX A6000 with Ollama: about 5.1 GB of GPU memory at an 8,192-token context; a summary in 3.8 s and a
SOAP note or entity extraction in about 10 s once the model is loaded.

## How to use

### Ollama

```bash
hf download <hf-user>/LocalClinical-Tuner-7B-GGUF localclinical-q4_k_m.gguf Modelfile --local-dir localclinical
cd localclinical && ollama create localclinical -f Modelfile
```

```bash
ollama run localclinical
```

Ollama also serves an OpenAI-compatible API at `http://127.0.0.1:11434/v1` (model name `localclinical`). For the full
local demo with the Gradio UI, use `scripts/run_demo.sh` from the GitHub repository.

### llama.cpp

```bash
llama-server -m localclinical-q4_k_m.gguf --port 8080
```

The GGUF carries its chat template, so `/v1/chat/completions` works directly. Example request for the summary task
(the system prompt and instruction are the ones used in training):

```bash
curl -s http://127.0.0.1:8080/v1/chat/completions -H "Content-Type: application/json" -d @- <<'EOF'
{
  "temperature": 0,
  "messages": [
    {"role": "system", "content": "You are a clinical documentation assistant that runs entirely on local hardware. You help clinicians summarize encounter notes, write structured SOAP notes, and extract medical entities. Use only information stated in the provided note. Never invent findings, diagnoses, medications, doses, or plans, and preserve negations exactly (for example, a symptom the patient denies must not be reported as present)."},
    {"role": "user", "content": "Summarize the following clinical encounter note for a busy clinician.\nRequirements:\n- Write 3 to 6 sentences of plain prose (maximum 120 words), in the third person.\n- Cover the patient's age/sex and reason for the visit, the key history and findings, the assessment, and the plan.\n- Use only information stated in the note. Do not use headings or bullet points.\n\nClinical note:\n\"\"\"\n<synthetic note text>\n\"\"\""}
  ]
}
EOF
```

Use only synthetic or public example text.

## Reproduce

Everything, from data to GGUF, is reproducible from the GitHub repository with locked dependencies, one seed and the
committed dataset:

```bash
git clone https://github.com/MehranKeivanimehr/LocalClinical-Tuner.git && cd LocalClinical-Tuner
uv sync --locked --all-extras
GPUS=0,1 TRAIN_GPU=0 SERVE_GPU=1 VLLM_BIN=~/vllm-env/bin/vllm bash scripts/run_full_pipeline.sh
```

Training needs one 24 GB GPU (peak 18.5 GB); the optional LLM judge needs two 48 GB GPUs. See
[Reproduce everything](https://github.com/MehranKeivanimehr/LocalClinical-Tuner#reproduce-everything).

## License

* Model weights: Apache-2.0, inherited from [Qwen2.5-7B-Instruct](https://huggingface.co/Qwen/Qwen2.5-7B-Instruct).
* Code: Apache-2.0.
* Public training data: ACI-Bench and MTS-Dialog keep their CC BY 4.0 licenses (attribution below).
* Synthetic training notes were generated with a hosted LLM; anyone regenerating or extending the data should check
  that provider's terms on using outputs to train models.

## Citation

```bibtex
@software{keivanimehr_localclinical_tuner_2026,
  author = {Keivanimehr, Mehran},
  title  = {{LocalClinical-Tuner}: Local fine-tuning of an open LLM for clinical documentation},
  year   = {2026},
  url    = {https://github.com/MehranKeivanimehr/LocalClinical-Tuner}
}
```

Please also cite the datasets and methods this model builds on:

* ACI-Bench: Yim et al., *ACI-BENCH: a Novel Ambient Clinical Intelligence Dataset for Benchmarking Automatic Visit
  Note Generation*, Scientific Data 10, 586 (2023).
* MTS-Dialog: Ben Abacha et al., *An Empirical Study of Clinical Note Generation from Doctor-Patient Encounters*,
  EACL 2023.
* ORPO: Hong, Lee & Thorne, *ORPO: Monolithic Preference Optimization without Reference Model*, EMNLP 2024.
* QLoRA: Dettmers et al., NeurIPS 2023.
* Qwen2.5: Qwen Team, *Qwen2.5 Technical Report*, 2024.
