#!/usr/bin/env python
"""Generate the synthetic part of the dataset (~70% of encounters) with Claude, falling back to a local teacher.

Two-step generation per encounter, so gold labels can only use what is actually written in the note:
  1. Note writing: the teacher writes a fictional note from a sampled scenario (specialty x problem x style x
     difficulty x setting x demographics x complicating factors). See ``clinical_tuner.taxonomy``.
  2. Annotation: in a fresh context that sees *only the note*, the teacher produces a summary, a SOAP note and
     verbatim entity annotations (JSON-schema constrained output).

The teacher is Claude via the Anthropic API by default (``data.generation.primary``); if no credentials are found or
a request ultimately fails, the local OpenAI-compatible teacher (``data.generation.fallback``) is used and the
record's ``provenance`` says so. Only synthetic content is ever sent to the API - never patient data.

This script applies *structural* gates only (note length, no SOAP headings in the source note, no identifier
patterns, schema-valid JSON). Clinical sanity checks, grounding and the LLM critic run afterwards, uniformly over
synthetic and public data, in ``data/quality_filter.py``. Records are appended as they finish, so an interrupted
run resumes where it stopped.

Usage:
    uv run python data/generate_synthetic_notes.py
    uv run python data/generate_synthetic_notes.py --set data.synthetic.n_generate=20 --out data/raw/smoke
    uv run python data/generate_synthetic_notes.py --dry-run
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import random
import re
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any

from clinical_tuner.config import Config, add_config_args, load_config
from clinical_tuner.llm import gather_with_progress
from clinical_tuner.quality.phi import find_phi
from clinical_tuner.schemas import Scenario
from clinical_tuner.taxonomy import DIFFICULTIES, NOTE_STYLES, sample_scenario
from clinical_tuner.teacher import AnnotationError, Teacher, annotate
from clinical_tuner.text import word_count
from clinical_tuner.utils import append_jsonl, read_jsonl, resolve, setup_logging, write_json

log = logging.getLogger("generate")

NOTE_SYSTEM = """You are an experienced physician and medical writer who creates realistic but entirely fictional clinical documentation for training clinical NLP models. Every note describes an invented patient; never reproduce real patient records.

Privacy: do not include names of patients or clinicians, dates of birth, calendar dates, addresses, phone numbers, record numbers, or any other identifiers. Refer to people by role (for example "the patient", "Doctor:", "Patient:") and express time relatively (for example "3 days ago", "hospital day 2").

Requirements for every note:
- Clinically coherent and realistic: plausible history, examination findings, vital signs, test results, and medication names, doses, and frequencies appropriate for the patient's age, sex, weight, and kidney function.
- State the patient's age and sex explicitly, exactly as given.
- Include pertinent negatives (symptoms the patient explicitly denies), phrased naturally for the style.
- If an allergy is documented, never prescribe that drug or a cross-reactive drug.
- The clinician's impression/diagnoses and the plan (medications started, stopped, or changed; tests ordered; referrals; follow-up) must be stated explicitly somewhere in the note.
- Do NOT organize the note under Subjective/Objective/Assessment/Plan (SOAP) headings and do not end with a summary.
- Output only the note text: no title, preamble, markdown, or commentary."""

NOTE_USER = """Write one clinical encounter note with these characteristics.

Specialty: {specialty}
Care setting: {setting}
Patient: {patient}
Main presenting problem: {problem}
Complicating factors: {factors}
Note style: {style_name}. {style_description}
Complexity ({difficulty}): {difficulty_description}
Length: about {lo}-{hi} words."""

_SOAP_HEADING_RE = re.compile(r"^\s*(subjective|objective|assessment|plan)\s*[:\-]", re.IGNORECASE | re.MULTILINE)


def describe_patient(s: Scenario) -> str:
    sex = {"F": "female", "M": "male"}[s.sex]
    if s.age == 0:
        return f"infant {sex} (choose an age between 1 and 11 months)"
    return f"{s.age}-year-old {sex}"


def note_messages(s: Scenario) -> list[dict[str, str]]:
    user = NOTE_USER.format(
        specialty=s.specialty, setting=s.setting, patient=describe_patient(s), problem=s.problem,
        factors="; ".join(s.complicating_factors) or "none", style_name=s.style.replace("_", " "),
        style_description=NOTE_STYLES[s.style]["description"], difficulty=s.difficulty,
        difficulty_description=DIFFICULTIES[s.difficulty]["description"], lo=s.target_words[0],
        hi=s.target_words[1],
    )
    return [{"role": "system", "content": NOTE_SYSTEM}, {"role": "user", "content": user}]


def clean_note(text: str) -> str:
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL)
    text = re.sub(r"^```[a-z]*\n|\n```$", "", text.strip())
    return text.strip()


class SyntheticGenerator:
    def __init__(self, cfg: Config, teacher: Teacher, out_dir: Path):
        self.cfg = cfg
        self.teacher = teacher
        self.accepted_path = out_dir / "synthetic.jsonl"
        self.rejected_path = out_dir / "synthetic_rejected.jsonl"

    def _reject(self, s: Scenario, reason: str, **extra: Any) -> dict[str, Any]:
        rec = {"encounter_id": s.encounter_id, "source": "synthetic", "stage": "generation", "reason": reason,
               "scenario": s.model_dump(), **extra}
        append_jsonl(self.rejected_path, rec)
        return rec

    async def run_one(self, s: Scenario) -> dict[str, Any]:
        syn = self.cfg.data.synthetic
        seed = int(s.encounter_id.split("-")[-1]) + self.cfg.project.seed
        raw_note, note_model = await self.teacher.chat(note_messages(s), syn.note_generation, seed=seed)
        note = clean_note(raw_note)

        n_words = word_count(note)
        if not syn.min_note_words <= n_words <= syn.max_note_words:
            return self._reject(s, "note_length", note=note, note_words=n_words, note_model=note_model)
        if len({m.group(1).lower() for m in _SOAP_HEADING_RE.finditer(note)}) >= 2:
            return self._reject(s, "note_already_soap", note=note, note_model=note_model)
        if phi := find_phi(note):
            return self._reject(s, f"identifier_pattern:{','.join(sorted(phi))}", note=note, note_model=note_model)

        try:
            ann, ann_model = await annotate(self.teacher, note, self.cfg.data.annotation, seed=seed)
        except AnnotationError as e:
            return self._reject(s, f"annotation_invalid:{str(e)[:60]}", note=note, note_model=note_model)

        record = {
            "encounter_id": s.encounter_id,
            "source": "synthetic",
            "source_info": {"dataset": "LocalClinical-Tuner synthetic", "license": "Apache-2.0"},
            "style": s.style,
            "scenario": s.model_dump(),
            "patient": {"age": s.age, "sex": s.sex},
            "note": note,
            "annotation": ann,
            "provenance": {"note": note_model, "summary": ann_model, "soap": ann_model, "entities": ann_model},
        }
        append_jsonl(self.accepted_path, record)
        return record


def _done_ids(*paths: Path) -> set[str]:
    done: set[str] = set()
    for p in paths:
        if p.exists():
            done |= {r["encounter_id"] for r in read_jsonl(p)}
    return done


async def main_async(args: argparse.Namespace) -> None:
    cfg = load_config(args.config, args.overrides)
    out_dir = resolve(args.out or cfg.data.raw_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    rng = random.Random(cfg.project.seed)
    n = cfg.data.synthetic.n_generate
    scenarios = [sample_scenario(rng, f"syn-{i:05d}") for i in range(n)]  # deterministic, resume-safe

    if args.dry_run:
        print("=== NOTE SYSTEM ===\n" + NOTE_SYSTEM + "\n\n=== NOTE USER (scenario 0) ===\n"
              + note_messages(scenarios[0])[1]["content"])
        print("\nSpecialty mix (top 5):", Counter(s.specialty for s in scenarios).most_common(5))
        return

    teacher = Teacher(cfg)
    await teacher.preflight()
    gen = SyntheticGenerator(cfg, teacher, out_dir)
    done = _done_ids(gen.accepted_path, gen.rejected_path)
    pending = [s for s in scenarios if s.encounter_id not in done]
    log.info("%d scenarios, %d already done, %d pending -> %s", n, len(done), len(pending), out_dir)

    t0 = time.time()
    results = await gather_with_progress(pending, gen.run_one, desc="synthetic encounters")
    errors = [r for r in results if isinstance(r, BaseException)]
    for e in errors[:5]:
        log.error("Generation error: %r", e)
    usage = teacher.usage()
    await teacher.close()

    accepted = read_jsonl(gen.accepted_path) if gen.accepted_path.exists() else []
    rejected = read_jsonl(gen.rejected_path) if gen.rejected_path.exists() else []
    stats_path = out_dir / "synthetic_stats.json"
    prev_usage = {}
    if stats_path.exists():
        import json

        prev_usage = json.loads(stats_path.read_text()).get("usage_cumulative", {})
    cumulative = {m: dict(Counter(prev_usage.get(m, {})) + Counter(u)) for m, u in
                  {**{k: {} for k in prev_usage}, **usage}.items()}
    write_json(stats_path, {
        "requested": n,
        "generated": len(accepted),
        "rejected_at_generation": len(rejected),
        "errors_this_run": len(errors),
        "rejection_reasons": dict(Counter(r["reason"] for r in rejected)),
        "note_models": dict(Counter(r["provenance"]["note"] for r in accepted)),
        "annotation_models": dict(Counter(r["provenance"]["summary"] for r in accepted)),
        "usage_this_run": usage,
        "usage_cumulative": cumulative,
        "seconds_this_run": round(time.time() - t0, 1),
    })
    log.info("Done: %d generated, %d rejected, %d errors (%.1f min). Usage: %s", len(accepted), len(rejected),
             len(errors), (time.time() - t0) / 60, usage)
    if pending and len(errors) == len(pending):
        sys.exit("All generation calls failed - check the teacher endpoints / credentials.")


def main() -> None:
    parser = add_config_args(argparse.ArgumentParser(description=__doc__,
                                                     formatter_class=argparse.RawDescriptionHelpFormatter))
    parser.add_argument("--out", default=None, help="Output directory (default: config data.raw_dir)")
    parser.add_argument("--dry-run", action="store_true", help="Print the prompts for one scenario and exit")
    args = parser.parse_args()
    setup_logging()
    asyncio.run(main_async(args))


if __name__ == "__main__":
    main()
