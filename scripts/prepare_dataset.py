#!/usr/bin/env python
"""Compose the final dataset (70% synthetic / 30% public), split it by encounter (stratified by source), and expand
every encounter into three instruction examples (summary, SOAP, entities).

Inputs : data/filtered/accepted.jsonl (output of data/quality_filter.py)
Outputs: data/processed/{train,val,pref,test}.jsonl, data/processed/split_stats.json, refreshed data card.

Each example row:
    {"id", "encounter_id", "task", "source", "style", "specialty", "difficulty",
     "messages": [system, user],          # exactly what the student model sees (clinical_tuner.prompts)
     "reference": str,                    # gold assistant answer
     "gold_entities": {...}, "note": str} # used by evaluation metrics

Usage:
    uv run python scripts/prepare_dataset.py
"""

from __future__ import annotations

import argparse
import logging
import random
from collections import Counter, defaultdict
from typing import Any

from clinical_tuner.config import add_config_args, load_config
from clinical_tuner.datacard import write_data_card
from clinical_tuner.prompts import TASKS, build_messages, render_entities, render_soap
from clinical_tuner.utils import read_jsonl, resolve, setup_logging, write_json, write_jsonl

log = logging.getLogger("prepare")
SPLITS = ("test", "val", "pref", "train")


def largest_remainder(total: int, weights: dict[str, int]) -> dict[str, int]:
    """Allocate ``total`` items across keys proportionally to ``weights`` with integer counts summing to total."""
    s = sum(weights.values())
    raw = {k: total * w / s for k, w in weights.items()}
    alloc = {k: int(v) for k, v in raw.items()}
    for k in sorted(raw, key=lambda k: raw[k] - alloc[k], reverse=True)[: total - sum(alloc.values())]:
        alloc[k] += 1
    return alloc


def compose(records: list[dict[str, Any]], total: int, syn_frac: float, rng: random.Random,
            public_sources: set[str] | None = None) -> tuple[list[dict[str, Any]], str | None]:
    """Pick encounters so that synthetic (generated or user-provided) vs public data follows ``syn_frac``; if one side
    is short, the whole dataset is scaled down rather than breaking the ratio."""
    is_pub = (lambda r: r["source"] in public_sources) if public_sources is not None else \
        (lambda r: r["source"] != "synthetic")
    syn = [r for r in records if not is_pub(r)]
    pub = [r for r in records if is_pub(r)]
    t_syn, t_pub = round(total * syn_frac), total - round(total * syn_frac)
    scale = min(1.0, len(syn) / t_syn if t_syn else 1.0, len(pub) / t_pub if t_pub else 1.0)
    n_syn, n_pub = round(t_syn * scale), round(t_pub * scale)
    note = None
    if scale < 1.0:
        note = (f"Fewer accepted encounters than targeted (synthetic {len(syn)}, public {len(pub)}); the dataset was "
                f"scaled to {n_syn + n_pub} encounters to keep the {syn_frac:.0%}/{1 - syn_frac:.0%} mix.")
        log.warning(note)
    by_src: dict[str, list] = defaultdict(list)
    for r in pub:
        by_src[r["source"]].append(r)
    pub_alloc = largest_remainder(n_pub, {k: len(v) for k, v in by_src.items()}) if pub else {}
    chosen = sorted(syn, key=lambda r: r["encounter_id"])
    chosen = rng.sample(chosen, n_syn)
    for src, k in pub_alloc.items():
        chosen += rng.sample(by_src[src], k)
    return chosen, note


def split_by_source(records: list[dict[str, Any]], sizes: dict[str, int], rng: random.Random
                    ) -> dict[str, list[dict[str, Any]]]:
    by_src: dict[str, list] = defaultdict(list)
    for r in records:
        by_src[r["source"]].append(r)
    counts = {k: len(v) for k, v in by_src.items()}
    per_split = {s: largest_remainder(n, counts) for s, n in sizes.items()}
    out: dict[str, list] = {s: [] for s in SPLITS}
    for src, items in sorted(by_src.items()):
        items = sorted(items, key=lambda r: r["encounter_id"])
        rng.shuffle(items)
        i = 0
        for s in ("test", "val", "pref"):
            k = per_split[s].get(src, 0)
            out[s] += items[i : i + k]
            i += k
        out["train"] += items[i:]
    return out


def to_examples(r: dict[str, Any]) -> list[dict[str, Any]]:
    ann = r["annotation"]
    refs = {"summary": ann["summary"], "soap": render_soap(ann["soap"]), "entities": render_entities(ann["entities"])}
    sc = r.get("scenario") or {}
    return [{
        "id": f"{r['encounter_id']}::{task}", "encounter_id": r["encounter_id"], "task": task, "source": r["source"],
        "style": r.get("style"), "specialty": sc.get("specialty"), "difficulty": sc.get("difficulty"),
        "messages": build_messages(task, r["note"]), "reference": refs[task], "gold_entities": ann["entities"],
        "note": r["note"],
    } for task in TASKS]


def main() -> None:
    parser = add_config_args(argparse.ArgumentParser(description=__doc__,
                                                     formatter_class=argparse.RawDescriptionHelpFormatter))
    args = parser.parse_args()
    setup_logging()
    cfg = load_config(args.config, args.overrides)
    rng = random.Random(cfg.project.seed)

    accepted = read_jsonl(resolve(cfg.data.filtered_dir) / "accepted.jsonl")
    chosen, note = compose(accepted, cfg.data.composition.total_encounters, cfg.data.composition.synthetic_fraction,
                           rng, public_sources={s.name for s in cfg.data.hf.sources})
    sizes = cfg.data.splits.counts(len(chosen))
    if sum(sizes.values()) >= len(chosen):
        raise SystemExit(f"Not enough encounters ({len(chosen)}) for test/val/pref sizes {sizes}")
    splits = split_by_source(chosen, sizes, rng)

    # Leakage guard: an encounter (and therefore its note) must live in exactly one split.
    seen: dict[str, str] = {}
    for s, items in splits.items():
        for r in items:
            assert r["encounter_id"] not in seen, f"{r['encounter_id']} in {seen[r['encounter_id']]} and {s}"
            seen[r["encounter_id"]] = s

    out_dir = resolve(cfg.data.processed_dir)
    total_examples = 0
    for s, items in splits.items():
        rows = [ex for r in items for ex in to_examples(r)]
        total_examples += write_jsonl(out_dir / f"{s}.jsonl", rows)
    sources = sorted({r["source"] for r in chosen})
    stats = {
        "split_sizes_encounters": {s: len(items) for s, items in splits.items()},
        "splits": list(SPLITS),
        "sources": sources,
        "by_split_source": {s: dict(Counter(r["source"] for r in items)) for s, items in splits.items()},
        "total_encounters": len(chosen),
        "total_examples": total_examples,
        "achieved_synthetic_fraction": sum(r["source"] not in {s.name for s in cfg.data.hf.sources}
                                           for r in chosen) / max(1, len(chosen)),
        "generators": dict(Counter(r["provenance"]["note"] for r in chosen)),
        "note": note,
    }
    write_json(out_dir / "split_stats.json", stats)
    write_data_card(cfg)
    log.info("Wrote %d examples from %d encounters: %s", total_examples, len(chosen), stats["by_split_source"])


if __name__ == "__main__":
    main()
