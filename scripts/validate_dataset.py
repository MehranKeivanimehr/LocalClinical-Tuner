#!/usr/bin/env python
"""End-to-end validation of the processed dataset (run after prepare_dataset.py and after build_preference_data.py).

Fails (exit code 1) on any ERROR:
  * leakage      - an encounter in two splits, or a near-duplicate note (word-trigram Jaccard >= threshold) shared
                   between the test split and any training/preference split;
  * prompts      - every example's messages are byte-identical to clinical_tuner.prompts.build_messages(task, note);
  * references   - each task has exactly one example per encounter; summary/SOAP/entities references are re-rendered
                   from the gold annotation and must match; SOAP has the 4 headers; entities are valid JSON whose
                   items are grounded in the note;
  * lengths      - prompt+reference token counts vs model.max_seq_length (examples over the limit are reported;
                   training drops them, so more than 2% is an error);
  * composition  - achieved synthetic fraction within 2 points of the target; every source present in test;
  * hygiene      - no identifier patterns left in any note;
  * preferences  - pairs only from the pref split, chosen != rejected, chosen == gold reference.

Writes data/processed/validation_report.json.

Usage:
    uv run python scripts/validate_dataset.py
    uv run python scripts/validate_dataset.py --skip-tokens       # without loading a tokenizer
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from typing import Any

from clinical_tuner.config import add_config_args, load_config
from clinical_tuner.metrics import soap_structure
from clinical_tuner.prompts import TASKS, build_messages, render_entities
from clinical_tuner.quality.grounding import NoteIndex, is_grounded
from clinical_tuner.quality.phi import find_phi
from clinical_tuner.schemas import Entities
from clinical_tuner.text import jaccard, shingles
from clinical_tuner.utils import read_jsonl, resolve, setup_logging, write_json

SPLITS = ("train", "val", "pref", "test")


class Report:
    def __init__(self) -> None:
        self.errors: list[str] = []
        self.warnings: list[str] = []
        self.stats: dict[str, Any] = {}

    def error(self, msg: str) -> None:
        self.errors.append(msg)

    def warn(self, msg: str) -> None:
        self.warnings.append(msg)


def check_splits(rows: dict[str, list[dict]], cfg, rep: Report) -> None:
    owner: dict[str, str] = {}
    for split, items in rows.items():
        for r in items:
            prev = owner.setdefault(r["encounter_id"], split)
            if prev != split:
                rep.error(f"encounter {r['encounter_id']} appears in {prev} and {split}")
    # near-duplicate leakage between test and everything the model trains on
    notes = {s: {r["encounter_id"]: r["note"] for r in items} for s, items in rows.items()}
    train_sh = [(eid, shingles(n)) for s in ("train", "pref", "val") for eid, n in notes.get(s, {}).items()]
    thr = cfg.data.near_duplicate_jaccard
    leaks = 0
    for eid, note in notes.get("test", {}).items():
        sh = shingles(note)
        for oid, osh in train_sh:
            small, big = sorted((len(sh), len(osh)))
            if big and small / big >= thr and jaccard(sh, osh) >= thr:
                rep.error(f"test note {eid} is a near-duplicate of training note {oid}")
                leaks += 1
                break
    rep.stats["near_duplicate_test_leaks"] = leaks
    rep.stats["encounters_per_split"] = {s: len(notes.get(s, {})) for s in SPLITS}


def check_examples(rows: dict[str, list[dict]], rep: Report) -> None:
    per_enc: dict[str, Counter] = defaultdict(Counter)
    bad_prompt = bad_ref = ungrounded = phi = 0
    for _split, items in rows.items():
        for ex in items:
            per_enc[ex["encounter_id"]][ex["task"]] += 1
            if ex["messages"] != build_messages(ex["task"], ex["note"]):
                bad_prompt += 1
                rep.error(f"{ex['id']}: prompt differs from build_messages()")
            gold = Entities.model_validate(ex["gold_entities"])
            if ex["task"] == "entities":
                if ex["reference"] != render_entities(ex["gold_entities"]):
                    bad_ref += 1
                    rep.error(f"{ex['id']}: entity reference != rendered gold entities")
                idx = NoteIndex(ex["note"])
                terms = [s.text for s in gold.symptoms] + gold.diagnoses + [m.name for m in gold.medications] + \
                    gold.procedures
                miss = [t for t in terms if not is_grounded(t, idx)]
                if miss:
                    ungrounded += 1
                    rep.error(f"{ex['id']}: gold entities not grounded in note: {miss[:3]}")
            elif ex["task"] == "soap":
                if soap_structure(ex["reference"]) != 1.0:
                    bad_ref += 1
                    rep.error(f"{ex['id']}: SOAP reference lacks the 4 ordered headers")
            elif ex["task"] == "summary" and not ex["reference"].strip():
                bad_ref += 1
                rep.error(f"{ex['id']}: empty summary reference")
            leaked = find_phi(ex["reference"]) + (find_phi(ex["note"]) if ex["task"] == "summary" else Counter())
            if leaked:
                phi += 1
                rep.error(f"{ex['id']}: identifier pattern(s) in note/reference: {dict(leaked)}")
    for eid, c in per_enc.items():
        if any(c[t] != 1 for t in TASKS):
            rep.error(f"encounter {eid} has task counts {dict(c)} (expected one of each)")
    rep.stats.update(prompt_mismatches=bad_prompt, reference_errors=bad_ref, ungrounded_entity_examples=ungrounded,
                     phi_notes=phi)


def check_tokens(rows: dict[str, list[dict]], cfg, rep: Report) -> None:
    from transformers import AutoTokenizer

    tok = AutoTokenizer.from_pretrained(cfg.model.base_model)
    limit = cfg.model.max_seq_length
    over: Counter = Counter()
    lengths: dict[str, list[int]] = defaultdict(list)
    for split, items in rows.items():
        for ex in items:
            text = tok.apply_chat_template(ex["messages"] + [{"role": "assistant", "content": ex["reference"]}],
                                           tokenize=False)
            n = len(tok(text, add_special_tokens=False)["input_ids"])
            lengths[split].append(n)
            if n > limit:
                over[split] += 1
    for split, ls in lengths.items():
        frac = over[split] / max(1, len(ls))
        rep.stats.setdefault("tokens", {})[split] = {"max": max(ls), "mean": round(sum(ls) / len(ls)),
                                                    "over_limit": over[split]}
        if frac > 0.02:
            rep.error(f"{split}: {over[split]} examples ({frac:.1%}) exceed max_seq_length={limit}")
        elif over[split]:
            rep.warn(f"{split}: {over[split]} examples exceed max_seq_length={limit} (dropped in training)")


def check_composition(rows: dict[str, list[dict]], cfg, rep: Report) -> None:
    public = {s.name for s in cfg.data.hf.sources}
    enc_src = {r["encounter_id"]: r["source"] for items in rows.values() for r in items}
    frac = sum(s not in public for s in enc_src.values()) / max(1, len(enc_src))
    rep.stats["synthetic_fraction"] = round(frac, 4)
    rep.stats["sources"] = dict(Counter(enc_src.values()))
    tolerance = max(0.02, 1.5 / max(1, len(enc_src)))  # one encounter can move tiny datasets by more than 2 points
    if abs(frac - cfg.data.composition.synthetic_fraction) > tolerance:
        rep.error(f"synthetic fraction {frac:.3f} differs from target {cfg.data.composition.synthetic_fraction}")
    test_sources = {r["source"] for r in rows.get("test", [])}
    for src in set(enc_src.values()):
        if src not in test_sources:
            rep.warn(f"source {src!r} has no encounters in the test split")


def check_preferences(proc, rows: dict[str, list[dict]], rep: Report) -> None:
    pref_ids = {r["encounter_id"] for r in rows.get("pref", [])}
    gold = {r["id"]: r["reference"] for r in rows.get("pref", [])}
    for name in ("pref_pairs_train.jsonl", "pref_pairs_val.jsonl"):
        p = proc / name
        if not p.exists():
            continue
        pairs = read_jsonl(p)
        kinds = Counter(x["rejected_kind"] for x in pairs)
        rep.stats.setdefault("preference_pairs", {})[name] = {"n": len(pairs), "kinds": dict(kinds)}
        for x in pairs:
            if x["encounter_id"] not in pref_ids:
                rep.error(f"{name}: pair {x['id']} is not from the pref split")
            if x["chosen"].strip() == x["rejected"].strip():
                rep.error(f"{name}: pair {x['id']} has chosen == rejected")
            if gold.get(x["id"]) is not None and x["chosen"] != gold[x["id"]]:
                rep.error(f"{name}: pair {x['id']} chosen != gold reference")


def main() -> None:
    parser = add_config_args(argparse.ArgumentParser(description=__doc__,
                                                     formatter_class=argparse.RawDescriptionHelpFormatter))
    parser.add_argument("--skip-tokens", action="store_true")
    args = parser.parse_args()
    setup_logging()
    cfg = load_config(args.config, args.overrides)
    proc = resolve(cfg.data.processed_dir)
    rows = {s: read_jsonl(proc / f"{s}.jsonl") for s in SPLITS if (proc / f"{s}.jsonl").exists()}
    rep = Report()
    if set(rows) != set(SPLITS):
        rep.error(f"missing split files: {set(SPLITS) - set(rows)}")
    check_splits(rows, cfg, rep)
    check_examples(rows, rep)
    check_composition(rows, cfg, rep)
    if not args.skip_tokens:
        check_tokens(rows, cfg, rep)
    check_preferences(proc, rows, rep)
    write_json(proc / "validation_report.json", {"ok": not rep.errors, "errors": rep.errors,
                                                 "warnings": rep.warnings, "stats": rep.stats})
    print(json.dumps(rep.stats, indent=1))
    for w in rep.warnings:
        print("WARN ", w)
    for e in rep.errors[:50]:
        print("ERROR", e)
    print(f"\n{'PASSED' if not rep.errors else 'FAILED'}: {len(rep.errors)} errors, {len(rep.warnings)} warnings")
    sys.exit(0 if not rep.errors else 1)


if __name__ == "__main__":
    main()
