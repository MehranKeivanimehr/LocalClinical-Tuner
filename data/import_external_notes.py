#!/usr/bin/env python
"""Bring your own data: import notes (and optional labels) produced outside the pipeline.

Two typical uses:
  * Synthetic notes written in a chat app (e.g. the Claude desktop app) from the batches made by
    ``data/make_external_batches.py`` - no API key needed.
  * Your own note collection (JSONL with at least a "note" field). Pass ``--private`` for anything that might be
    real clinical text: identifiers are scrubbed instead of rejected, missing labels are produced ONLY by the local
    teacher, and the record is flagged so the quality filter never sends it to a remote critic.

Accepted input: *.jsonl / *.json / *.md / *.txt files containing JSON objects one per line (```jsonl fences and
surrounding prose are ignored) or a JSON array. Fields: "note" (required), optional "encounter_id", "summary",
"soap" {subjective, objective, assessment, plan[]}, "entities" {symptoms[{text, negated}], diagnoses[],
medications[{name, dose, frequency}], procedures[]}, and optional "scenario"/"age"/"sex".

Output: data/raw/external.jsonl (+ external_stats.json). The quality filter then treats these records exactly like
API-generated ones.

Usage:
    uv run python data/import_external_notes.py --generator-label "claude-opus-5-5 (Claude app)"
    uv run python data/import_external_notes.py --write-missing-batch          # make-up batch for missing ids
    uv run python data/import_external_notes.py --inputs my_notes.jsonl --private --source-name private
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import re
from collections import Counter
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from clinical_tuner.config import add_config_args, load_config
from clinical_tuner.llm import gather_with_progress
from clinical_tuner.quality.phi import find_phi, scrub_phi
from clinical_tuner.quality.rules import extract_demographics
from clinical_tuner.schemas import SOAP, Entities, Scenario
from clinical_tuner.teacher import AnnotationError, Teacher, annotate
from clinical_tuner.text import word_count
from clinical_tuner.utils import read_jsonl, resolve, setup_logging, stable_hash, write_json, write_jsonl

log = logging.getLogger("import_external")
_SOAP_HEADING_RE = re.compile(r"^\s*(subjective|objective|assessment|plan)\s*[:\-]", re.IGNORECASE | re.MULTILINE)


def parse_file(path: Path) -> tuple[list[dict[str, Any]], int]:
    """Return (objects, n_unparseable_lines). Tolerates code fences, prose, and a cut-off final line."""
    text = path.read_text(encoding="utf-8").strip()
    if text.startswith("["):
        try:
            data = json.loads(text)
            return [d for d in data if isinstance(d, dict)], 0
        except json.JSONDecodeError:
            pass
    objs, bad = [], 0
    for line in text.splitlines():
        line = line.strip().rstrip(",")
        if not line.startswith("{"):
            continue  # fences, prose, blank lines
        try:
            obj = json.loads(line)
            if isinstance(obj, dict):
                objs.append(obj)
        except json.JSONDecodeError:
            bad += 1
    return objs, bad


def _labels(obj: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """Validated subset of provided labels and the list of fields that still need labeling."""
    ann: dict[str, Any] = {}
    if isinstance(obj.get("summary"), str) and obj["summary"].strip():
        ann["summary"] = obj["summary"].strip()
    try:
        if obj.get("soap") is not None:
            ann["soap"] = SOAP.model_validate(obj["soap"]).model_dump()
    except ValidationError:
        pass
    try:
        if obj.get("entities") is not None:
            ann["entities"] = Entities.model_validate(obj["entities"]).model_dump()
    except ValidationError:
        pass
    return ann, [f for f in ("summary", "soap", "entities") if f not in ann]


async def main_async(args: argparse.Namespace) -> None:
    cfg = load_config(args.config, args.overrides)
    if args.private:  # private text must never reach a remote API: label with the local teacher only
        cfg.data.generation.primary = cfg.data.generation.fallback or "teacher"
        cfg.data.generation.fallback = None
    ext_dir = resolve(args.dir)
    files = [Path(p) for p in args.inputs] if args.inputs else sorted(
        p for p in (ext_dir / "responses").glob("*") if p.suffix in {".jsonl", ".json", ".md", ".txt"})
    scen_path = ext_dir / "scenarios.jsonl"
    scen_rows = read_jsonl(scen_path) if scen_path.exists() and not args.inputs else []
    scenarios = {r["encounter_id"]: Scenario.model_validate(r) for r in scen_rows}
    batch_of = {r["encounter_id"]: r.get("batch") for r in scen_rows}
    out_path = resolve(cfg.data.raw_dir) / f"{args.out_name}.jsonl"
    previous = {r["encounter_id"]: r for r in read_jsonl(out_path)} if out_path.exists() else {}
    syn = cfg.data.synthetic

    stats: Counter = Counter()
    rejected: list[dict[str, Any]] = []
    todo: list[dict[str, Any]] = []
    seen: set[str] = set()
    for f in files:
        objs, bad = parse_file(f)
        stats["files"] += 1
        stats["unparseable_lines"] += bad
        for obj in objs:
            stats["objects"] += 1
            note = obj.get("note")
            if not isinstance(note, str) or not note.strip():
                stats["rejected:no_note"] += 1
                continue
            note = note.replace("\\n", "\n").strip() if "\n" not in note and "\\n" in note else note.strip()
            eid = str(obj.get("encounter_id") or f"ext-{stable_hash(note)}")
            if eid in seen:
                stats["duplicates"] += 1
                continue
            seen.add(eid)
            scen = scenarios.get(eid)
            if scenarios and scen is None:
                stats["rejected:unknown_encounter_id"] += 1
                rejected.append({"encounter_id": eid, "reason": "unknown_encounter_id", "file": f.name})
                continue
            reason = None
            n_words = word_count(note)
            if not args.private and not syn.min_note_words <= n_words <= syn.max_note_words:
                reason = "note_length"
            elif not args.private and len({m.group(1).lower() for m in _SOAP_HEADING_RE.finditer(note)}) >= 2:
                reason = "note_already_soap"
            elif args.private:
                note, scrubbed = scrub_phi(note)
                stats.update({f"phi_scrubbed:{k}": v for k, v in scrubbed.items()})
            elif phi := find_phi(note):
                reason = f"identifier_pattern:{','.join(sorted(phi))}"
            if reason:
                stats[f"rejected:{reason}"] += 1
                rejected.append({"encounter_id": eid, "reason": reason, "file": f.name, "note": note})
                continue
            ann, missing = _labels(obj)
            if args.relabel:
                ann, missing = {}, ["summary", "soap", "entities"]
            todo.append({"eid": eid, "note": note, "scenario": scen, "ann": ann, "missing": missing, "obj": obj})

    teacher = None
    if any(t["missing"] for t in todo):
        teacher = Teacher(cfg)
        await teacher.preflight()

    async def finish(t: dict[str, Any]) -> dict[str, Any]:
        prev = previous.get(t["eid"])
        if prev and prev.get("_note_hash") == stable_hash(t["note"]) and not args.relabel:
            return prev
        prov = {"note": args.generator_label}
        prov.update({f: args.generator_label for f in t["ann"]})
        ann = dict(t["ann"])
        if t["missing"]:
            gen, model = await annotate(teacher, t["note"], cfg.data.annotation, fields=tuple(t["missing"]))
            ann.update(gen)
            prov.update({f: model for f in t["missing"]})
        s: Scenario | None = t["scenario"]
        age, sex = (s.age, s.sex) if s else extract_demographics(t["note"])
        obj = t["obj"]
        return {
            "encounter_id": t["eid"],
            "source": args.source_name,
            "source_info": {"dataset": "external", "generator": args.generator_label,
                            "license": "user-provided" if args.private else "synthetic (generated content)"},
            "style": s.style if s else obj.get("style"),
            "scenario": s.model_dump() if s else obj.get("scenario"),
            "patient": {"age": obj.get("age", age), "sex": obj.get("sex", sex)},
            "note": t["note"],
            "annotation": {"summary": ann["summary"], "soap": ann["soap"], "entities": ann["entities"]},
            "provenance": prov,
            "private": bool(args.private),
            "_note_hash": stable_hash(t["note"]),
        }

    results = await gather_with_progress(todo, finish, desc="import/label")
    records = []
    for t, r in zip(todo, results, strict=True):
        if isinstance(r, BaseException):
            reason = f"labeling_failed:{type(r).__name__}" if not isinstance(r, AnnotationError) else "labels_invalid"
            stats[f"rejected:{reason}"] += 1
            rejected.append({"encounter_id": t["eid"], "reason": reason})
        else:
            records.append(r)
    if teacher is not None:
        stats_usage = teacher.usage()
        await teacher.close()
    else:
        stats_usage = {}

    write_jsonl(out_path, records)
    write_jsonl(out_path.with_name(f"{args.out_name}_rejected.jsonl"), rejected)
    submitted = {f.stem for f in files}
    expected = {i for i, b in batch_of.items() if b in submitted}  # only batches you have already answered
    received = {r["encounter_id"] for r in records} | {r["encounter_id"] for r in rejected}
    missing_ids = sorted(expected - received)
    out_stats = {
        "generator_label": args.generator_label, "private": args.private, "imported": len(records),
        "labels_provided": sum(all(p == args.generator_label for p in r["provenance"].values()) for r in records),
        "batches_answered": len(submitted & set(batch_of.values())), "batches_total": len(set(batch_of.values())),
        "expected_from_answered_batches": len(expected), "missing_ids": len(missing_ids),
        "missing_id_list": missing_ids, **dict(stats),
        "usage": stats_usage,
    }
    write_json(out_path.with_name(f"{args.out_name}_stats.json"), out_stats)
    log.info("Imported %d records -> %s | %s", len(records), out_path, {k: v for k, v in out_stats.items()
                                                                      if k != "usage"})
    if missing_ids:
        log.warning("%d scenario ids from answered batches have no usable response (first: %s)", len(missing_ids),
                    missing_ids[:5])
        if args.write_missing_batch:
            import sys

            sys.path.insert(0, str(Path(__file__).resolve().parent))
            from make_external_batches import batch_message

            size = args.batch_size
            chunks = [missing_ids[i : i + size] for i in range(0, len(missing_ids), size)]
            for k, chunk in enumerate(chunks, 1):
                p = ext_dir / "batches" / f"makeup_{k:03d}.md"
                p.write_text(batch_message(f"Make-up batch {k:03d} of {len(chunks):03d}",
                                           [scenarios[i] for i in chunk]), encoding="utf-8")
            log.info("Wrote %d make-up batch(es) to %s (save replies as responses/makeup_NNN.jsonl)", len(chunks),
                     ext_dir / "batches")


def main() -> None:
    parser = add_config_args(argparse.ArgumentParser(description=__doc__,
                                                     formatter_class=argparse.RawDescriptionHelpFormatter))
    parser.add_argument("--dir", default="data/external/claude_app", help="batch directory (responses/ inside)")
    parser.add_argument("--inputs", nargs="*", default=None, help="explicit input files (bring-your-own data)")
    parser.add_argument("--generator-label", default="claude-app",
                        help='who wrote the notes/labels, e.g. "claude-opus-5-5 (Claude app)"; used for provenance '
                             "and to keep the critic a different model")
    parser.add_argument("--source-name", default="synthetic", help='"synthetic" (default) or e.g. "private"')
    parser.add_argument("--out-name", default="external")
    parser.add_argument("--private", action="store_true", help="real/sensitive text: scrub PHI, local-only models")
    parser.add_argument("--relabel", action="store_true", help="ignore provided labels; label with the teacher")
    parser.add_argument("--write-missing-batch", action="store_true")
    parser.add_argument("--batch-size", type=int, default=15)
    args = parser.parse_args()
    setup_logging()
    asyncio.run(main_async(args))


if __name__ == "__main__":
    main()
