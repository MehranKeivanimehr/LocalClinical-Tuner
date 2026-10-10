#!/usr/bin/env python
"""Critic-guided label repair for public (Hugging Face) encounters.

The note of a public record is real source data and cannot be "fixed" - but its labels can. For every public record
the critic rejected, this script hands the teacher the dialogue plus the critic's concrete findings (unsupported
statements, negation-flag errors, omissions, critical/major errors) and asks for corrected labels written from the
dialogue alone. Reused references are replaced in the process when they were the problem (e.g. MTS-Dialog section
summaries that contain facts never said in the dialogue). Repaired records keep their provenance trail and go
through the full critic again in the next quality_filter run; a record that still fails stays rejected.

Usage (teacher server running):
    uv run python data/repair_labels.py
"""

from __future__ import annotations

import argparse
import asyncio
import logging
from collections import Counter
from typing import Any

from clinical_tuner.config import add_config_args, load_config
from clinical_tuner.llm import gather_with_progress
from clinical_tuner.teacher import (
    ANNOTATION_SYSTEM,
    AnnotationError,
    Teacher,
    annotation_schema,
    parse_annotation,
)
from clinical_tuner.utils import read_jsonl, resolve, setup_logging, write_json, write_jsonl

log = logging.getLogger("repair")

REPAIR_USER = """An auditor reviewed the reference labels for the clinical note below and found these problems:
{problems}

Write NEW labels ("summary", "soap", "entities") for this note from scratch, using ONLY what the note itself says.
Fix every problem listed above, do not add facts that the note does not state, and copy entity texts verbatim from
the note. If the note is a short excerpt without an assessment or plan, write "Not documented." for those parts.

Clinical note:
\"\"\"
{note}
\"\"\""""


def problems_from_verdict(v: dict[str, Any]) -> list[str]:
    checks = v.get("checks", {})
    out = [f"- Unsupported: {s}" for s in checks.get("unsupported_statements", [])]
    out += [f"- Wrong negation flag: {s}" for s in checks.get("negation_flag_errors", [])]
    out += [f"- Omitted: {s}" for s in checks.get("omitted_key_findings", [])]
    out += [f"- {e['severity']} ({e['field']}/{e['category']}): {e['description']}" for e in v.get("errors", [])
            if e.get("severity") in ("critical", "major")]
    return out[:20]


async def main_async(args: argparse.Namespace) -> None:
    cfg = load_config(args.config, args.overrides)
    raw_path = resolve(cfg.data.raw_dir) / "hf.jsonl"
    records = read_jsonl(raw_path)
    rejected = {r["encounter_id"]: r for r in read_jsonl(resolve(cfg.data.filtered_dir) / "rejected.jsonl")
                if r.get("stage") == "critic" and r["source"] != "synthetic"
                and ((r.get("quality") or {}).get("critic") or {}).get("verdict")}
    todo = [r for r in records if r["encounter_id"] in rejected and "label_repair" not in r.get("repairs", [])]
    log.info("Repairing labels of %d critic-rejected public records", len(todo))
    teacher = Teacher(cfg)
    await teacher.preflight()
    stats: Counter = Counter()

    async def fix(r: dict[str, Any]) -> None:
        v = rejected[r["encounter_id"]]["quality"]["critic"]["verdict"]
        probs = problems_from_verdict(v)
        if not probs:
            stats["no_actionable_feedback"] += 1
            return
        msgs = [{"role": "system", "content": ANNOTATION_SYSTEM},
                {"role": "user", "content": REPAIR_USER.format(problems="\n".join(probs), note=r["note"])}]
        try:
            raw, model = await teacher.chat(msgs, cfg.data.annotation, json_schema=annotation_schema())
            ann = parse_annotation(raw, ("summary", "soap", "entities"))
        except (AnnotationError, Exception) as e:  # noqa: BLE001
            stats[f"failed:{type(e).__name__}"] += 1
            return
        r["previous_annotation"] = r["annotation"]
        r["annotation"] = ann
        r["provenance"] = {**r["provenance"], "summary": model, "soap": model,
                           "entities": model}
        r.setdefault("repairs", []).append("label_repair")
        stats["repaired"] += 1

    await gather_with_progress(todo, fix, desc="label repair")
    usage = teacher.usage()
    await teacher.close()
    write_jsonl(raw_path, records)
    out = {"attempted": len(todo), **dict(stats), "usage": usage}
    write_json(resolve(cfg.data.raw_dir) / "label_repair_stats.json", out)
    log.info("Label repair: %s", out)


def main() -> None:
    parser = add_config_args(argparse.ArgumentParser(description=__doc__,
                                                     formatter_class=argparse.RawDescriptionHelpFormatter))
    args = parser.parse_args()
    setup_logging()
    asyncio.run(main_async(args))


if __name__ == "__main__":
    main()
