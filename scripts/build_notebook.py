#!/usr/bin/env python
"""Build notebooks/full_pipeline_demo.ipynb (a guided, executable tour of the pipeline).

The notebook is light by default: it inspects the data, quality filter, prompts, training curves, results and the
local GGUF model produced by ``scripts/run_full_pipeline.sh``. Set ``RUN_HEAVY = True`` in its first code cell to
re-run a tiny version of each training stage from inside the notebook.

Usage:
    uv run python scripts/build_notebook.py                 # write the notebook
    uv run python scripts/build_notebook.py --execute       # write + execute (outputs embedded)
"""

from __future__ import annotations

import argparse
from pathlib import Path

import nbformat as nbf

ROOT = Path(__file__).resolve().parents[1]

CELLS: list[tuple[str, str]] = [
    ("md", """# LocalClinical-Tuner - full pipeline walkthrough

This notebook walks through every stage of the project on the artifacts produced by
`bash scripts/run_full_pipeline.sh`:

1. Scenario sampling for synthetic notes
2. The dataset (70% synthetic / 30% public, splits, label provenance)
3. The quality filter (rule-based clinical checks + LLM critic)
4. The exact prompt format the model is trained and served with
5. Training (QLoRA SFT -> ORPO): curves and resources
6. Evaluation: base vs SFT vs SFT+ORPO (+ quantized GGUF)
7. Local inference through Ollama's OpenAI-compatible API

Everything runs locally. Cells only *read* pipeline outputs unless you set `RUN_HEAVY = True`."""),
    ("code", """import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path.cwd().resolve()
if ROOT.name == "notebooks":
    ROOT = ROOT.parent
os.chdir(ROOT)
sys.path.insert(0, str(ROOT / "src"))

from IPython.display import Markdown, display  # noqa: E402

from clinical_tuner.config import load_config  # noqa: E402
from clinical_tuner.utils import read_json, read_jsonl  # noqa: E402

RUN_HEAVY = False          # True: re-run tiny training stages from the notebook (needs a GPU)
cfg = load_config()
print("base model:", cfg.model.base_model, "| results dir:", cfg.project.results_dir)"""),
    ("md", """## 1. Scenario sampling

Each synthetic note starts from a sampled scenario (specialty x presenting problem x note style x difficulty x
setting x demographics x complicating factors). The same seeded sampler drives the API path and the chat-app
batches, so scenario ids are identical across both."""),
    ("code", """import random

from clinical_tuner.taxonomy import DIFFICULTIES, NOTE_STYLES, all_problems, sample_scenario

rng = random.Random(cfg.project.seed)
rows = [sample_scenario(rng, f"syn-{i:05d}") for i in range(6)]
table = "| id | specialty | patient | problem | style | difficulty |\\n|---|---|---|---|---|---|\\n"
table += "\\n".join(f"| {s.encounter_id} | {s.specialty} | {s.age}{s.sex} | {s.problem} | {s.style} | {s.difficulty} |"
                    for s in rows)
display(Markdown(table))
print(len(all_problems()), "presenting problems,", len(NOTE_STYLES), "note styles,", len(DIFFICULTIES), "difficulty levels")"""),
    ("md", """## 2. The dataset

Composition, split sizes, and one held-out test encounter with its three reference outputs."""),
    ("code", """split = read_json("data/processed/split_stats.json")
display(Markdown("| split | " + " | ".join(split["sources"]) + " |\\n|---|" + "---|" * len(split["sources"]) + "\\n" +
                 "\\n".join(f"| {s} | " + " | ".join(str(split["by_split_source"].get(s, {}).get(src, 0))
                                                  for src in split["sources"]) + " |" for s in split["splits"])))
print(f"{split['total_encounters']} encounters -> {split['total_examples']} examples, "
      f"{split['achieved_synthetic_fraction']:.1%} synthetic; note writers: {split.get('generators')}")"""),
    ("code", """test = read_jsonl("data/processed/test.jsonl")
ex = {r["task"]: r for r in test if r["encounter_id"] == test[0]["encounter_id"]}
print(ex["summary"]["note"][:1500], "...\\n")
for task in ("summary", "soap", "entities"):
    print(f"--- reference: {task} ---\\n{ex[task]['reference'][:900]}\\n")"""),
    ("md", """## 3. The quality filter

Every encounter (synthetic and public) passes rule-based clinical sanity checks and an independent LLM critic.
Below: the rule engine on a deliberately flawed record, then the real filter statistics."""),
    ("code", """from types import SimpleNamespace

from clinical_tuner.quality.rules import run_rules

flawed = {"source": "synthetic", "style": "narrative_progress_note", "patient": {"age": 58, "sex": "M"},
          "note": ("58-year-old man. Allergies: penicillin (hives). Chest pain for 2 days, denies fever. "
                   "Metformin 5000 mg twice daily. Started amoxicillin 500 mg three times daily. ECG done."),
          "annotation": {"summary": "A 58-year-old man with chest pain was started on amoxicillin and lisinopril. " * 2,
                         "soap": {"subjective": "Chest pain.", "objective": "Not documented.", "assessment": "Chest pain.",
                                  "plan": ["Amoxicillin", "Start lisinopril"]},
                         "entities": {"symptoms": [{"text": "chest pain", "negated": False}, {"text": "fever", "negated": True}],
                                      "diagnoses": [], "procedures": ["ECG"],
                                      "medications": [{"name": "metformin", "dose": "5000 mg", "frequency": "twice daily"},
                                                      {"name": "amoxicillin", "dose": "500 mg", "frequency": "three times daily"}]}}}
res = run_rules(flawed, SimpleNamespace(max_major=1, min_entities=3, max_ungrounded_entity_frac=0.2, summary_words=(25, 160)))
for f in res.findings:
    print(f"{f.severity:8s} {f.check:22s} {f.message}")"""),
    ("code", """qs = read_json("data/filtered/quality_stats.json")
print("funnel by source:", json.dumps(qs["funnel_by_source"], indent=1))
print("top rule rejections:", dict(list(qs["rejection_reasons"].get("rules", {}).items())[:8]))
print("top critic rejections:", dict(list(qs["rejection_reasons"].get("critic", {}).items())[:8]))
print("critic mean scores:", qs.get("critic_score_means"))
print("critic models used:", qs.get("critic_models"))"""),
    ("md", """## 4. Prompt format

Training, evaluation, the Gradio app and the Ollama Modelfile all use `build_messages(task, note)`; the model's
chat template turns it into the exact token sequence below."""),
    ("code", """from transformers import AutoTokenizer

from clinical_tuner.prompts import build_messages

tok = AutoTokenizer.from_pretrained(cfg.model.base_model)
msgs = build_messages("soap", "Pt is a 54yo M c/o 2d of productive cough, denies fever. Lungs: scattered rhonchi.")
print(tok.apply_chat_template(msgs, tokenize=False, add_generation_prompt=True))"""),
    ("md", """## 5. Training: QLoRA SFT -> ORPO

Set `RUN_HEAVY = True` to run a few steps of each stage here; otherwise the curves of the full run are shown."""),
    ("code", """if RUN_HEAVY:
    py = [sys.executable]
    subprocess.run(py + ["training/sft_train.py", "--set", "sft.max_steps=10", "--set", "sft.output_dir=outputs/nb_sft"], check=True)
    subprocess.run(py + ["training/orpo_train.py", "--set", "orpo.max_steps=5", "--set", "sft.output_dir=outputs/nb_sft",
                         "--set", "orpo.output_dir=outputs/nb_orpo"], check=True)
for stage in ("sft", "orpo"):
    p = Path(f"outputs/{stage}/train_metrics.json")
    if p.exists():
        m = read_json(p)
        print(stage.upper(), {k: m.get(k) for k in ("final_train_loss", "best_eval_loss", "final_eval_reward_accuracy",
                                                     "peak_reserved_gb", "train_seconds", "global_steps")})"""),
    ("code", """import matplotlib.pyplot as plt

INK, MUTED, GRID = "#0b0b0b", "#52514e", "#e4e3df"
fig, axes = plt.subplots(1, 2, figsize=(11, 3.6))
for ax, stage, key, title in ((axes[0], "sft", "loss", "SFT loss"), (axes[1], "orpo", "rewards/accuracies", "ORPO reward accuracy")):
    p = Path(f"outputs/{stage}/log_history.json")
    if not p.exists():
        ax.set_visible(False)
        continue
    hist = read_json(p)
    tr = [(h["step"], h[key]) for h in hist if key in h]
    ev = [(h["step"], h["eval_" + key]) for h in hist if "eval_" + key in h]
    if tr:
        ax.plot(*zip(*tr, strict=True), color="#2a78d6", linewidth=2, label="train")
    if ev:
        ax.plot(*zip(*ev, strict=True), color="#eb6834", linewidth=2, marker="o", markersize=5, label="validation")
    ax.set_title(title, color=INK, loc="left", fontsize=11)
    ax.set_xlabel("step", color=MUTED)
    ax.grid(color=GRID, linewidth=0.8)
    ax.spines[["top", "right"]].set_visible(False)
    ax.legend(frameon=False)
plt.tight_layout()
plt.show()"""),
    ("md", """## 6. Evaluation: base vs fine-tuned

Reference-based metrics with bootstrap 95% CIs, reference-free metrics, and the independent LLM judge.
(`results/comparison_table.md` has the per-source breakdown.)"""),
    ("code", """table = Path("results/comparison_table.md").read_text()
display(Markdown(table.split("## By data source")[0]))"""),
    ("code", """from IPython.display import Image

fig_path = Path("results/figures/key_metrics_light.png")
if fig_path.exists():
    display(Image(filename=str(fig_path)))"""),
    ("md", """## 7. Local inference (GGUF via Ollama)

Start the demo with `bash scripts/run_demo.sh` (or `ollama serve` + `ollama create localclinical -f
serving/ollama_modelfile`). The cell below calls the OpenAI-compatible endpoint on localhost; it is skipped if the
server is not running."""),
    ("code", """from openai import OpenAI

note = test[3]["note"]
try:
    client = OpenAI(base_url="http://127.0.0.1:11434/v1", api_key="ollama")
    out = client.chat.completions.create(model="localclinical", messages=build_messages("summary", note),
                                         temperature=0, max_tokens=300, timeout=60)
    print(out.choices[0].message.content)
except Exception as e:
    print("Ollama is not running here - start it with `bash scripts/run_demo.sh`.", type(e).__name__)
    print("\\nSample outputs from the evaluation run instead:\\n")
    print(Path("results/sample_outputs.md").read_text()[:2500])"""),
]


def build() -> nbf.NotebookNode:
    nb = nbf.v4.new_notebook()
    nb.metadata["kernelspec"] = {"display_name": "Python 3", "language": "python", "name": "python3"}
    nb.cells = [nbf.v4.new_markdown_cell(src) if kind == "md" else nbf.v4.new_code_cell(src) for kind, src in CELLS]
    return nb


def strip_stderr(nb: nbf.NotebookNode) -> nbf.NotebookNode:
    """Drop captured stderr (library warnings): it is noise and embeds machine-specific paths."""
    for cell in nb.cells:
        if cell.cell_type == "code":
            cell.outputs = [o for o in cell.get("outputs", []) if not (o.get("output_type") == "stream"
                                                                        and o.get("name") == "stderr")]
    return nb


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    out = ROOT / "notebooks" / "full_pipeline_demo.ipynb"
    out.parent.mkdir(parents=True, exist_ok=True)
    nb = build()
    if args.execute:
        from nbclient import NotebookClient

        NotebookClient(nb, timeout=600, kernel_name="python3", resources={"metadata": {"path": str(ROOT)}}).execute()
        strip_stderr(nb)
    nbf.write(nb, out)
    print(f"wrote {out}{' (executed)' if args.execute else ''}")


if __name__ == "__main__":
    main()
