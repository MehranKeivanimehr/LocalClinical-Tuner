#!/usr/bin/env python
"""Build (prompt, chosen, rejected) pairs for the preference stage (ORPO/DPO).

For every prompt in the held-out ``pref`` split (disjoint from SFT training data):
  1. Sample ``preference.n_samples`` answers from the SFT model (on-policy) through an OpenAI-compatible server
     (vLLM serving ``outputs/sft/merged``).
  2. Score each sample with a task reward (``clinical_tuner.metrics.reward``: entity F1 + negation accuracy for
     extraction; ROUGE-L + key-entity coverage + structure/length - medication-hallucination penalty for prose).
  3. chosen = gold reference; rejected = the worst sample if it is at least ``min_margin`` below the gold (~1.0).
  4. If the SFT model is already good on that prompt, synthesize a *targeted hard negative* from the gold answer
     instead (hallucinated medication, flipped negation, dropped plan/diagnosis, wrong SOAP section), so the
     preference stage still teaches the clinically critical distinctions.

Outputs: data/processed/pref_pairs_{train,val}.jsonl and pref_stats.json.

Usage:
    scripts/vllm_server.sh start sft outputs/sft/merged 8301 7 && scripts/vllm_server.sh wait 8301
    uv run python scripts/build_preference_data.py
"""

from __future__ import annotations

import argparse
import asyncio
import copy
import json
import logging
import random
import re
from collections import Counter
from typing import Any

from clinical_tuner.config import add_config_args, load_config
from clinical_tuner.llm import gather_with_progress, make_client
from clinical_tuner.metrics import reward
from clinical_tuner.prompts import render_entities
from clinical_tuner.text import contains_term, normalize, sentences
from clinical_tuner.utils import read_jsonl, resolve, setup_logging, write_json, write_jsonl

log = logging.getLogger("prefs")

# Common outpatient drugs with a plausible dose, used to inject *hallucinated* medications into hard negatives.
_DISTRACTOR_DRUGS = [("lisinopril", "10 mg", "daily"), ("metformin", "500 mg", "twice daily"),
                     ("atorvastatin", "40 mg", "daily"), ("amlodipine", "5 mg", "daily"),
                     ("sertraline", "50 mg", "daily"), ("omeprazole", "20 mg", "daily"),
                     ("levothyroxine", "50 mcg", "daily"), ("gabapentin", "300 mg", "three times daily"),
                     ("prednisone", "20 mg", "daily"), ("azithromycin", "250 mg", "daily"),
                     ("furosemide", "20 mg", "daily"), ("apixaban", "5 mg", "twice daily")]


def _distractor(note: str, rng: random.Random) -> tuple[str, str, str]:
    note_norm = normalize(note)
    pool = [d for d in _DISTRACTOR_DRUGS if not contains_term(note_norm, d[0])]
    return rng.choice(pool or _DISTRACTOR_DRUGS)


def hard_negative(ex: dict[str, Any], rng: random.Random) -> tuple[str, str] | None:
    """Return (corruption_type, corrupted_answer) derived from the gold answer, or None if not applicable."""
    task, ref, note = ex["task"], ex["reference"], ex["note"]
    drug, dose, freq = _distractor(note, rng)
    options: list[tuple[str, str]] = []
    if task == "entities":
        ent = json.loads(ref)
        if ent["symptoms"]:
            e = copy.deepcopy(ent)
            i = rng.randrange(len(e["symptoms"]))
            e["symptoms"][i]["negated"] = not e["symptoms"][i]["negated"]
            options.append(("flipped_negation", render_entities(e)))
        e = copy.deepcopy(ent)
        e["medications"].append({"name": drug, "dose": dose, "frequency": freq})
        options.append(("hallucinated_medication", render_entities(e)))
        if len(ent["diagnoses"]) >= 1:
            e = copy.deepcopy(ent)
            e["diagnoses"].pop(rng.randrange(len(e["diagnoses"])))
            options.append(("dropped_diagnosis", render_entities(e)))
    elif task == "summary":
        sents = sentences(ref)
        options.append(("hallucinated_medication",
                         ref.rstrip() + f" The patient was also started on {drug} {dose} {freq}."))
        if len(sents) >= 4:
            options.append(("dropped_plan", " ".join(sents[:-2])))
        neg = re.search(r"\b(denies|denied|no)\s+([a-z][a-z ]{2,30}?)(?=[,.;])", ref)
        if neg:
            flipped = ref[: neg.start()] + f"reports {neg.group(2)}" + ref[neg.end():]
            options.append(("flipped_negation", flipped))
    elif task == "soap":
        m = re.search(r"PLAN:\n(.*)$", ref, re.S)
        if m:
            n_items = len(re.findall(r"^\d+\.", m.group(1), re.M))
            options.append(("hallucinated_medication",
                            ref.rstrip() + f"\n{n_items + 1}. Start {drug} {dose} {freq}."))
            options.append(("dropped_plan", ref[: m.start()] + "PLAN:\nNot documented."))
        s = re.search(r"SUBJECTIVE:\n(.*?)\n\nOBJECTIVE:\n(.*?)\n\nASSESSMENT:", ref, re.S)
        if s and "not documented" not in s.group(2).lower():
            swapped = ref.replace(s.group(0), f"SUBJECTIVE:\n{s.group(2)}\n\nOBJECTIVE:\n{s.group(1)}\n\nASSESSMENT:")
            options.append(("wrong_section", swapped))
    options = [o for o in options if o[1].strip() != ref.strip()]
    return rng.choice(options) if options else None


async def main_async(args: argparse.Namespace) -> None:
    cfg = load_config(args.config, args.overrides)
    pc = cfg.preference
    rng = random.Random(cfg.project.seed)
    proc = resolve(cfg.data.processed_dir)
    rows = read_jsonl(args.input or proc / "pref.jsonl")
    client = make_client(cfg.endpoint(pc.policy_endpoint))
    gen_kwargs = dict(temperature=pc.temperature, top_p=pc.top_p, max_tokens=pc.max_tokens, n=pc.n_samples)

    async def sample(ex: dict[str, Any]) -> list[str]:
        return await client.chat(ex["messages"], **gen_kwargs)

    samples = await gather_with_progress(rows, sample, desc="on-policy sampling")
    await client.close()

    pairs, stats = [], Counter()
    margins = []
    for ex, outs in zip(rows, samples, strict=True):
        stats[f"task:{ex['task']}"] += 1
        best_neg = None
        if isinstance(outs, BaseException):
            stats["sampling_errors"] += 1
        else:
            scored = sorted(((reward(ex["task"], o, ex), o) for o in outs if o.strip()), key=lambda x: x[0])
            if scored and 1.0 - scored[0][0] >= pc.min_margin:
                best_neg = ("on_policy", scored[0][1], scored[0][0])
        if best_neg is None and pc.hard_negatives:
            hn = hard_negative(ex, rng)
            if hn is not None:
                best_neg = (f"hard_negative:{hn[0]}", hn[1], reward(ex["task"], hn[1], ex))
        if best_neg is None:
            stats["no_pair"] += 1
            continue
        kind, rejected, r = best_neg
        stats[kind] += 1
        if kind.startswith("hard_negative:"):
            stats["hard_negative_total"] += 1
        margins.append(1.0 - r)
        pairs.append({"id": ex["id"], "encounter_id": ex["encounter_id"], "task": ex["task"], "source": ex["source"],
                      "messages": ex["messages"], "chosen": ex["reference"], "rejected": rejected,
                      "rejected_kind": kind, "rejected_reward": round(r, 4)})

    enc = sorted({p["encounter_id"] for p in pairs})
    rng.shuffle(enc)
    val_enc = set(enc[: max(1, round(len(enc) * pc.val_fraction))])
    train = [p for p in pairs if p["encounter_id"] not in val_enc]
    val = [p for p in pairs if p["encounter_id"] in val_enc]
    write_jsonl(proc / "pref_pairs_train.jsonl", train)
    write_jsonl(proc / "pref_pairs_val.jsonl", val)
    out = {"prompts": len(rows), "pairs": len(pairs), "train_pairs": len(train), "val_pairs": len(val),
           "mean_margin": round(sum(margins) / max(1, len(margins)), 4), **dict(stats)}
    write_json(proc / "pref_stats.json", out)
    log.info("Preference pairs: %s", out)


def main() -> None:
    parser = add_config_args(argparse.ArgumentParser(description=__doc__,
                                                     formatter_class=argparse.RawDescriptionHelpFormatter))
    parser.add_argument("--input", default=None, help="default: <processed_dir>/pref.jsonl")
    args = parser.parse_args()
    setup_logging()
    asyncio.run(main_async(args))


if __name__ == "__main__":
    main()
