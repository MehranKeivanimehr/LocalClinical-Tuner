#!/usr/bin/env python
"""Ingest, clean and label the public Hugging Face part of the dataset (~30% of encounters).

Sources (see ``data.hf.sources`` in the config; licenses are verified against the upstream repositories and
recorded on every record and in the data card):
  * ACI-Bench  (CC BY 4.0) - role-played doctor-patient dialogues with clinician-written visit notes.
  * MTS-Dialog (CC BY 4.0) - doctor-patient conversations with human-written note-section summaries.
Excluded sources and the reasons are listed in ``data.hf.excluded`` (e.g. omi-health: no open license;
MIMIC/n2c2: credentialed real-patient data).

Cleaning: unicode/whitespace normalization and speaker-label normalization, PHI-pattern scrubbing, an English
filter, a length filter, exact + near-duplicate removal within the public data and against the synthetic set.

Labels: existing references are reused where they fit a task (ACI-Bench note -> SOAP, restructured without adding
content; MTS-Dialog GENHX section -> summary). Missing labels are produced by the same teacher as the synthetic data
(Claude, local fallback). Every label carries its provenance.

Usage:
    uv run python data/ingest_hf_datasets.py
    uv run python data/ingest_hf_datasets.py --clean-only        # stop after cleaning (no teacher calls)
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import re
import time
import unicodedata
from collections import Counter, defaultdict
from typing import Any

from clinical_tuner.config import Config, HFSourceCfg, add_config_args, load_config
from clinical_tuner.llm import gather_with_progress
from clinical_tuner.quality.grounding import ground_entities
from clinical_tuner.quality.phi import english_score, scrub_phi
from clinical_tuner.quality.rules import extract_demographics
from clinical_tuner.schemas import Entities
from clinical_tuner.teacher import (
    ANNOTATION_SYSTEM,
    AnnotationError,
    Teacher,
    annotate,
    annotation_schema,
    parse_annotation,
    soap_from_reference,
)
from clinical_tuner.text import jaccard, normalize, shingles, word_count
from clinical_tuner.utils import (
    append_jsonl,
    read_json,
    read_jsonl,
    resolve,
    setup_logging,
    write_json,
    write_jsonl,
)

log = logging.getLogger("ingest_hf")

SPEAKERS = {"doctor": "Doctor", "patient": "Patient", "guest_family": "Family member",
            "patient_guest": "Family member", "guest_clinician": "Clinician", "doctor_guest": "Clinician",
            "nurse": "Nurse"}


def normalize_dialogue(text: str) -> str:
    """Unicode/whitespace normalization and canonical 'Speaker: utterance' lines."""
    text = unicodedata.normalize("NFKC", text).replace("\r\n", "\n").replace("\r", "\n")
    lines = []
    for line in text.split("\n"):
        line = re.sub(r"[ \t]+", " ", line).strip()
        if not line:
            continue
        m = re.match(r"^\[([a-z_]+)\]\s*(.*)$", line, re.IGNORECASE) or \
            re.match(r"^([A-Za-z_]+)\s*:\s*(.*)$", line)
        if m and m.group(1).lower() in SPEAKERS:
            line = f"{SPEAKERS[m.group(1).lower()]}: {m.group(2).strip()}"
        lines.append(line)
    return "\n".join(lines)


def load_source(src: HFSourceCfg) -> list[dict[str, Any]]:
    from datasets import load_dataset

    rows = []
    for config in src.configs:
        for split in src.splits:
            ds = load_dataset(src.hf_id, name=config, split=split)
            for i, r in enumerate(ds):
                if src.name == "aci-bench":
                    oid = r["encounter_id"]
                    rows.append({"original_id": oid, "config": config, "split": split, "dialogue": r["dialogue"],
                                 "reference_note": r["note"]})
                elif src.name == "mts-dialog":
                    rows.append({"original_id": str(r.get("ID", i)), "config": config, "split": split,
                                 "dialogue": r["dialogue"], "section_header": r["section_header"],
                                 "section_text": r["section_text"]})
                else:
                    raise ValueError(f"No loader for source {src.name!r}")
    return rows


def clean_source(cfg: Config, src: HFSourceCfg, rows: list[dict[str, Any]], stats: Counter,
                 phi_counts: Counter) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    c = cfg.data.hf.cleaning
    kept, rejected = [], []
    for r in rows:
        eid = f"{src.name.split('-')[0]}-{r['config'] + '-' if r['config'] else ''}{r['split']}-{r['original_id']}"
        dialogue, counts = scrub_phi(normalize_dialogue(r["dialogue"]))
        phi_counts.update(counts)
        ref = {}
        if "reference_note" in r:
            ref["note"], counts = scrub_phi(unicodedata.normalize("NFKC", r["reference_note"]).strip())
            phi_counts.update(counts)
        if "section_text" in r:
            ref["section_header"] = r["section_header"]
            ref["section_text"], counts = scrub_phi(unicodedata.normalize("NFKC", r["section_text"]).strip())
            phi_counts.update(counts)
        rec = {"encounter_id": eid, "source": src.name, "original_id": r["original_id"], "config": r["config"],
               "split": r["split"], "note": dialogue, "reference": ref}
        n = word_count(dialogue)
        if n < c.min_words:
            reason = "too_short"
        elif n > c.max_words:
            reason = "too_long"
        elif english_score(dialogue) < c.min_english_score:
            reason = "not_english"
        else:
            reason = None
        stats[f"{src.name}:{reason or 'clean'}"] += 1
        (rejected if reason else kept).append({**rec, "reason": reason} if reason else rec)
    return kept, rejected


def deduplicate(cands: list[dict[str, Any]], synthetic_notes: list[str], threshold: float,
                stats: Counter) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Exact and near-duplicate removal (word-trigram Jaccard) within the public set and against synthetic notes."""
    syn = [shingles(t) for t in synthetic_notes]
    kept, dropped, kept_sh, seen_exact = [], [], [], set()
    for c in cands:
        key = normalize(c["note"])
        sh = shingles(c["note"])
        reason = None
        if key in seen_exact:
            reason = "exact_duplicate"
        else:
            for pool, why in ((kept_sh, "near_duplicate_public"), (syn, "near_duplicate_synthetic")):
                for other in pool:
                    small, big = sorted((len(sh), len(other)))
                    if big and small / big >= threshold and jaccard(sh, other) >= threshold:
                        reason = why
                        break
                if reason:
                    break
        if reason:
            stats[f"{c['source']}:{reason}"] += 1
            dropped.append({**c, "reason": reason})
        else:
            seen_exact.add(key)
            kept_sh.append(sh)
            kept.append(c)
    return kept, dropped


REPAIR_USER = """Your previous entity extraction for this note contained items that are NOT exact spans of the note:
{bad}

Re-extract the "entities" field. Copy every entity text exactly as it appears in the note (an exact span; capitalization
may differ). Do not paraphrase, normalize, merge, or add qualifiers such as "(family history)". Exclude conditions of
family members. Return a JSON object with only the "entities" field.

Clinical note:
\"\"\"
{note}
\"\"\""""


async def repair_grounding(teacher: Teacher, records: list[dict[str, Any]], sampling) -> Counter:
    """One feedback round for records whose entities are not verbatim spans of the note. The new entities replace
    the old ones only if strictly fewer are ungrounded."""
    stats: Counter = Counter()

    async def fix(r: dict[str, Any]) -> None:
        old = Entities.model_validate(r["annotation"]["entities"])
        _, total, bad = ground_entities(old, r["note"])
        if not bad:
            return
        stats["attempted"] += 1
        msgs = [{"role": "system", "content": ANNOTATION_SYSTEM},
                {"role": "user", "content": REPAIR_USER.format(bad="\n".join(f"- {b}" for b in bad), note=r["note"])}]
        try:
            raw, _model = await teacher.chat(msgs, sampling, json_schema=annotation_schema(("entities",)))
            new = parse_annotation(raw, ("entities",))["entities"]
        except Exception:  # noqa: BLE001
            stats["failed"] += 1
            return
        _, new_total, new_bad = ground_entities(Entities.model_validate(new), r["note"])
        if new_total and len(new_bad) / new_total < len(bad) / max(1, total):
            r["annotation"]["entities"] = new
            r.setdefault("repairs", []).append("entities_grounding")
            stats["improved"] += 1
            stats["fully_grounded" if not new_bad else "partially_grounded"] += 1
        else:
            stats["not_improved"] += 1

    await gather_with_progress(records, fix, desc="grounding repair")
    return stats


class Labeler:
    def __init__(self, cfg: Config, teacher: Teacher, sources: dict[str, HFSourceCfg], out_path, rej_path):
        self.cfg, self.teacher, self.sources = cfg, teacher, sources
        self.out_path, self.rej_path = out_path, rej_path

    async def run_one(self, c: dict[str, Any]) -> dict[str, Any]:
        src = self.sources[c["source"]]
        sampling = self.cfg.data.annotation
        ref = c["reference"]
        ann: dict[str, Any] = {}
        prov: dict[str, str] = {"note": f"dataset:{src.name}"}
        try:
            if src.reuse_reference == "soap_from_note" and ref.get("note"):
                ann["soap"], m = await soap_from_reference(self.teacher, c["note"], ref["note"], sampling)
                prov["soap"] = f"reference:{src.name}+restructured:{m}"
            summary_ref = ref.get("section_text", "")
            if src.reuse_reference == "summary_from_section" and ref.get("section_header") == "GENHX" and \
                    self.cfg.data.quality.rules.summary_words[0] <= word_count(summary_ref) <= \
                    self.cfg.data.quality.rules.summary_words[1]:
                ann["summary"] = summary_ref
                prov["summary"] = f"reference:{src.name}"
            missing = tuple(f for f in ("summary", "soap", "entities") if f not in ann)
            if missing:
                gen, m = await annotate(self.teacher, c["note"], sampling, fields=missing)
                ann.update(gen)
                prov.update({f: m for f in missing})
        except AnnotationError as e:
            rec = {**c, "stage": "labeling", "reason": f"annotation_invalid:{str(e)[:60]}"}
            append_jsonl(self.rej_path, rec)
            return rec

        age, sex = extract_demographics(c["note"])
        if age is None and ref.get("note"):
            age, sex = extract_demographics(ref["note"])
        record = {
            "encounter_id": c["encounter_id"],
            "source": src.name,
            "source_info": {"dataset": src.name, "hf_id": src.hf_id, "config": c["config"], "split": c["split"],
                            "original_id": c["original_id"], "license": src.license, "upstream": src.upstream},
            "style": "dialogue_transcript",
            "scenario": None,
            "patient": {"age": age, "sex": sex},
            "note": c["note"],
            "annotation": {"summary": ann["summary"], "soap": ann["soap"], "entities": ann["entities"]},
            "provenance": prov,
            "reference_section": ref.get("section_header"),
        }
        append_jsonl(self.out_path, record)
        return record


async def run_repair(cfg: Config, out_path) -> dict[str, int]:
    records = read_jsonl(out_path)
    teacher = Teacher(cfg)
    await teacher.preflight()
    stats = await repair_grounding(teacher, records, cfg.data.annotation)
    await teacher.close()
    write_jsonl(out_path, records)
    log.info("Grounding repair: %s", dict(stats))
    return dict(stats)


async def main_async(args: argparse.Namespace) -> None:
    cfg = load_config(args.config, args.overrides)
    raw_dir = resolve(cfg.data.raw_dir)
    raw_dir.mkdir(parents=True, exist_ok=True)
    out_path, rej_path = raw_dir / "hf.jsonl", raw_dir / "hf_rejected.jsonl"
    sources = {s.name: s for s in cfg.data.hf.sources}
    if args.repair_only:
        stats = await run_repair(cfg, out_path)
        hf_stats = read_json(raw_dir / "hf_stats.json") if (raw_dir / "hf_stats.json").exists() else {}
        hf_stats["grounding_repair"] = stats
        write_json(raw_dir / "hf_stats.json", hf_stats)
        return

    stats: Counter = Counter()
    phi_counts: Counter = Counter()
    candidates, cleaning_rejects = [], []
    for src in cfg.data.hf.sources:
        rows = load_source(src)
        stats[f"{src.name}:loaded"] = len(rows)
        kept, rej = clean_source(cfg, src, rows, stats, phi_counts)
        if src.max_records:
            kept = kept[: src.max_records]
        candidates += kept
        cleaning_rejects += rej
        log.info("%s: loaded %d, %d pass cleaning", src.name, len(rows), len(kept))

    syn_path = raw_dir / "synthetic.jsonl"
    synthetic_notes = [r["note"] for r in read_jsonl(syn_path)] if syn_path.exists() else []
    if not synthetic_notes:
        log.warning("No synthetic notes found yet; cross-source dedup will be repeated in quality_filter.py")
    candidates, dup_rejects = deduplicate(candidates, synthetic_notes, cfg.data.hf.cleaning.near_duplicate_jaccard,
                                          stats)
    write_jsonl(raw_dir / "hf_cleaning_rejected.jsonl",
                [{**r, "stage": "cleaning"} for r in cleaning_rejects + dup_rejects])
    write_jsonl(raw_dir / "hf_candidates.jsonl", candidates)
    log.info("After cleaning + dedup: %d candidates (%s)", len(candidates),
             dict(Counter(c["source"] for c in candidates)))

    usage: dict[str, Any] = {}
    repair_stats: dict[str, int] = {}
    if not args.clean_only:
        done = set()
        for p in (out_path, rej_path):
            if p.exists():
                done |= {r["encounter_id"] for r in read_jsonl(p)}
        pending = [c for c in candidates if c["encounter_id"] not in done]
        teacher = Teacher(cfg)
        await teacher.preflight()
        labeler = Labeler(cfg, teacher, sources, out_path, rej_path)
        t0 = time.time()
        results = await gather_with_progress(pending, labeler.run_one, desc="labeling public dialogues")
        errors = [r for r in results if isinstance(r, BaseException)]
        for e in errors[:5]:
            log.error("Labeling error: %r", e)
        usage = teacher.usage()
        await teacher.close()
        log.info("Labeled %d/%d in %.1f min (%d errors). Usage: %s", len(pending) - len(errors), len(pending),
                 (time.time() - t0) / 60, len(errors), usage)
        repair_stats = await run_repair(cfg, out_path)

    labeled = read_jsonl(out_path) if out_path.exists() else []
    by_source: dict[str, Counter] = defaultdict(Counter)
    for k, v in stats.items():
        s, _, what = k.partition(":")
        by_source[s][what] += v
    for r in labeled:
        by_source[r["source"]]["labeled"] += 1
        for f, p in r["provenance"].items():
            by_source[r["source"]][f"{f}<-{p.split(':')[0]}"] += 1
    write_json(raw_dir / "hf_stats.json", {
        "by_source": {k: dict(v) for k, v in by_source.items()},
        "phi_scrubbed": dict(phi_counts),
        "sources": [s.model_dump() for s in cfg.data.hf.sources],
        "excluded_sources": [e.model_dump() for e in cfg.data.hf.excluded],
        "usage_this_run": usage,
        "grounding_repair": repair_stats,
    })


def main() -> None:
    parser = add_config_args(argparse.ArgumentParser(description=__doc__,
                                                     formatter_class=argparse.RawDescriptionHelpFormatter))
    parser.add_argument("--clean-only", action="store_true", help="Only download/clean/dedup; no labeling calls")
    parser.add_argument("--repair-only", action="store_true",
                        help="Only re-run the entity grounding repair on the existing data/raw/hf.jsonl")
    args = parser.parse_args()
    setup_logging()
    asyncio.run(main_async(args))


if __name__ == "__main__":
    main()
