#!/usr/bin/env python
"""Write copy-paste prompts for generating the synthetic notes in a chat app (e.g. the Claude desktop/web app)
instead of through an API key.

It produces:
  data/external/claude_app/INSTRUCTIONS.md     -> paste ONCE into a Claude Project's custom instructions
  data/external/claude_app/batches/batch_NNN.md -> paste ONE per new chat in that Project
  data/external/claude_app/scenarios.jsonl     -> the sampled scenarios (used by the importer to validate)

Scenarios come from the same seeded sampler as the API path (``clinical_tuner.taxonomy``), and the instructions are
built from the same prompt constants, so app-made and API-made data follow identical specifications. Save each
reply (the ```jsonl block) as data/external/claude_app/responses/batch_NNN.jsonl, then run
``data/import_external_notes.py``.

Usage:
    uv run python data/make_external_batches.py                       # 1,750 scenarios, 15 per batch
    uv run python data/make_external_batches.py --n 900 --batch-size 10
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path

from clinical_tuner.config import add_config_args, load_config
from clinical_tuner.taxonomy import DIFFICULTIES, NOTE_STYLES, sample_scenario
from clinical_tuner.teacher import ANNOTATION_SYSTEM
from clinical_tuner.utils import resolve, setup_logging, write_jsonl

sys.path.insert(0, str(Path(__file__).resolve().parent))
from generate_synthetic_notes import NOTE_SYSTEM, describe_patient  # noqa: E402

OUTPUT_SPEC = """## Output format (strict)

Reply with ONE ```jsonl code block and nothing else. One JSON object per line, one line per scenario, in the order given:

{"encounter_id": "<id from the scenario>", "note": "<the note; escape line breaks as \\n>", "summary": "<summary>", "soap": {"subjective": "...", "objective": "...", "assessment": "...", "plan": ["step 1", "step 2"]}, "entities": {"symptoms": [{"text": "...", "negated": false}], "diagnoses": ["..."], "medications": [{"name": "...", "dose": "... or null", "frequency": "... or null"}], "procedures": ["..."]}}

- Every line must be valid JSON (double quotes; escape quotes inside strings; line breaks inside the note as \\n).
- Use JSON null (not the string "null") when a dose or frequency is not stated.
- Do not skip, merge, or renumber scenarios. If you run out of space, stop at the end of a complete line; I will
  say "continue" and you resume with the next scenario on a new line.
- If code execution/file creation is enabled, you may instead save the lines to a file named after the batch
  (e.g. batch_007.jsonl) and give me the download."""


def instructions() -> str:
    styles = "\n".join(f"- **{k.replace('_', ' ')}**: {v['description']}" for k, v in NOTE_STYLES.items())
    diffs = "\n".join(f"- **{k}**: {v['description']}" for k, v in DIFFICULTIES.items())
    return f"""# LocalClinical-Tuner - synthetic clinical notes (instructions for a Claude Project)

You generate fictional training data for a small, locally-run clinical documentation model. Each message I send
contains a numbered list of scenarios. For EVERY scenario do two steps:

1. **Write the note** for that scenario, following "Note writing" below.
2. **Annotate the note you just wrote**, following "Annotation" below. Derive the labels ONLY from the note text -
   never from the scenario description (if the note does not say it, the labels must not say it).

## Note writing

{NOTE_SYSTEM}

Note styles:
{styles}

Complexity levels:
{diffs}

## Annotation

{ANNOTATION_SYSTEM.split("Rules for all fields:", 1)[1].join(["Rules for all fields:", ""])}

{OUTPUT_SPEC}
"""


def batch_message(title: str, scenarios) -> str:
    lines = [f"{title}: generate {len(scenarios)} encounters, one per scenario, following the project instructions. "
             "Reply with a single ```jsonl block.\n"]
    for i, s in enumerate(scenarios, 1):
        lines.append(
            f"{i}. encounter_id: {s.encounter_id} | specialty: {s.specialty} | setting: {s.setting} | patient: "
            f"{describe_patient(s)} | main problem: {s.problem} | complicating factors: "
            f"{'; '.join(s.complicating_factors) or 'none'} | style: {s.style.replace('_', ' ')} | complexity: "
            f"{s.difficulty} | length: about {s.target_words[0]}-{s.target_words[1]} words")
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = add_config_args(argparse.ArgumentParser(description=__doc__,
                                                     formatter_class=argparse.RawDescriptionHelpFormatter))
    parser.add_argument("--n", type=int, default=None, help="number of scenarios (default: data.synthetic.n_generate)")
    parser.add_argument("--batch-size", type=int, default=15)
    parser.add_argument("--out", default="data/external/claude_app")
    args = parser.parse_args()
    setup_logging()
    cfg = load_config(args.config, args.overrides)
    n = args.n or cfg.data.synthetic.n_generate
    rng = random.Random(cfg.project.seed)  # same seed + sampler as the API path -> same scenarios/ids
    scenarios = [sample_scenario(rng, f"syn-{i:05d}") for i in range(n)]

    out = resolve(args.out)
    (out / "batches").mkdir(parents=True, exist_ok=True)
    (out / "responses").mkdir(parents=True, exist_ok=True)
    (out / "INSTRUCTIONS.md").write_text(instructions(), encoding="utf-8")
    batches = [scenarios[i : i + args.batch_size] for i in range(0, n, args.batch_size)]
    write_jsonl(out / "scenarios.jsonl", [{**s.model_dump(), "batch": f"batch_{b:03d}"}
                                          for b, chunk in enumerate(batches, 1) for s in chunk])
    for b, chunk in enumerate(batches, 1):
        (out / "batches" / f"batch_{b:03d}.md").write_text(batch_message(f"Batch {b:03d} of {len(batches):03d}", chunk),
                                                         encoding="utf-8")
    (out / "README.md").write_text(
        "# Claude app workflow\n\n"
        "1. In the Claude app create a Project and paste `INSTRUCTIONS.md` into its custom instructions.\n"
        "2. For each file in `batches/`, start a NEW chat in that Project and paste the file's content.\n"
        "3. Save the reply's ```jsonl block as `responses/batch_NNN.jsonl` (same number as the batch).\n"
        "4. Run `uv run python data/import_external_notes.py --generator-label \"<model you used> (Claude app)\"`.\n"
        "   It validates every line, reports missing encounter ids, and can write a make-up batch for them.\n",
        encoding="utf-8")
    print(json.dumps({"scenarios": n, "batches": len(batches), "batch_size": args.batch_size, "dir": str(out)}))


if __name__ == "__main__":
    main()
