#!/usr/bin/env python
"""Validate the validator: measure how often the LLM critic catches known, injected label errors.

Takes N records the critic previously ACCEPTED, creates corrupted copies with clinically meaningful errors
(a hallucinated medication in the summary, a flipped symptom negation, a hallucinated plan item, a dropped
diagnosis), runs the critic on the clean and corrupted versions, and reports
  * detection rate  - share of corrupted records the critic rejects (any critical error / low score);
  * false-reject rate on the clean originals (re-run, to measure critic noise).
Writes data/filtered/critic_canary.json (summarized in the data card).

Usage:
    uv run python scripts/critic_canary.py --n 40 --endpoint teacher
"""

from __future__ import annotations

import argparse
import asyncio
import copy
import random
from collections import Counter
from typing import Any

from clinical_tuner.config import add_config_args, load_config
from clinical_tuner.llm import gather_with_progress, make_client
from clinical_tuner.quality.critic import CriticVerdict, run_critic
from clinical_tuner.text import contains_term, normalize
from clinical_tuner.utils import read_jsonl, resolve, setup_logging, write_json

DISTRACTORS = ["warfarin 5 mg daily", "metoprolol 50 mg twice daily", "prednisone 40 mg daily",
               "lisinopril 20 mg daily", "glipizide 10 mg daily", "amiodarone 200 mg daily"]


def corrupt(rec: dict[str, Any], kind: str, rng: random.Random) -> dict[str, Any] | None:
    r = copy.deepcopy(rec)
    ann = r["annotation"]
    note_norm = normalize(r["note"])
    drug = next((d for d in rng.sample(DISTRACTORS, len(DISTRACTORS))
                 if not contains_term(note_norm, d.split()[0])), None)
    if kind == "hallucinated_med_summary" and drug:
        ann["summary"] = ann["summary"].rstrip() + f" The patient was also started on {drug}."
    elif kind == "hallucinated_med_plan" and drug:
        ann["soap"]["plan"] = ann["soap"]["plan"] + [f"Start {drug}."]
    elif kind == "flipped_negation":
        sx = [s for s in ann["entities"]["symptoms"] if s["negated"]]
        if not sx:
            return None
        rng.choice(sx)["negated"] = False
    elif kind == "dropped_diagnosis":
        if not ann["entities"]["diagnoses"]:
            return None
        ann["entities"]["diagnoses"] = ann["entities"]["diagnoses"][1:]
        ann["soap"]["assessment"] = "Not documented."
    else:
        return None
    r["encounter_id"] = f"{rec['encounter_id']}::{kind}"
    return r


async def main_async(args: argparse.Namespace) -> None:
    cfg = load_config(args.config, args.overrides)
    out_dir = resolve(cfg.data.filtered_dir)
    cache = {row["key"].split(":")[0]: row for row in read_jsonl(out_dir / "critic_cache.jsonl")}  # any version
    raw = resolve(cfg.data.raw_dir)
    synthetic = [r for name in ("synthetic.jsonl", "external.jsonl") if (raw / name).exists()  # API or imported notes
                 for r in read_jsonl(raw / name)]
    pool = [r for r in synthetic if r["encounter_id"] in cache
            and CriticVerdict.model_validate(cache[r["encounter_id"]]["verdict"]).decision(
                cfg.data.quality.critic.min_overall, cfg.data.quality.critic.min_dimension)[0]]
    if not pool:
        raise SystemExit("No critic-accepted synthetic records yet - run data/quality_filter.py first")
    rng = random.Random(args.seed)
    picked = rng.sample(pool, min(args.n, len(pool)))
    kinds = ["hallucinated_med_summary", "hallucinated_med_plan", "flipped_negation", "dropped_diagnosis"]
    jobs = [("clean", r) for r in picked]
    for i, r in enumerate(picked):
        c = corrupt(r, kinds[i % len(kinds)], rng)
        if c is not None:
            jobs.append((kinds[i % len(kinds)], c))
    client = make_client(cfg.endpoint(args.endpoint))
    qc = cfg.data.quality.critic

    async def judge(job):
        kind, rec = job
        v = await run_critic(client, rec, rec["annotation"]["entities"], qc.max_tokens)
        ok, reasons = v.decision(qc.min_overall, qc.min_dimension)
        return kind, ok, reasons, v

    results = [x for x in await gather_with_progress(jobs, judge, desc="critic canary")
               if not isinstance(x, BaseException)]
    await client.close()
    by_kind: dict[str, Counter] = {}
    for kind, ok, _reasons, _ in results:
        by_kind.setdefault(kind, Counter())["rejected" if not ok else "accepted"] += 1
    corrupted = [(k, ok) for k, ok, _, _ in results if k != "clean"]
    clean = [ok for k, ok, _, _ in results if k == "clean"]
    clean_reasons = Counter(r for k, ok, rs, _ in results if k == "clean" and not ok for r in rs)
    examples = [{"reasons": rs, "checks": v.checks.model_dump(), "errors": [e.model_dump() for e in v.errors][:4]}
                for k, ok, rs, v in results if k == "clean" and not ok][:6]
    report = {
        "critic": client.cfg.identity, "n_clean": len(clean), "n_corrupted": len(corrupted),
        "detection_rate": round(sum(not ok for _, ok in corrupted) / max(1, len(corrupted)), 3),
        "clean_false_reject_rate": round(sum(not ok for ok in clean) / max(1, len(clean)), 3),
        "by_kind": {k: dict(v) for k, v in by_kind.items()},
        "clean_reject_reasons": dict(clean_reasons),
        "clean_reject_examples": examples,
    }
    write_json(out_dir / (args.out or "critic_canary.json"), report)
    print({k: v for k, v in report.items() if k != 'clean_reject_examples'})


def main() -> None:
    parser = add_config_args(argparse.ArgumentParser(description=__doc__,
                                                     formatter_class=argparse.RawDescriptionHelpFormatter))
    parser.add_argument("--n", type=int, default=40)
    parser.add_argument("--endpoint", default="teacher")
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--out", default=None, help="report file name in data/filtered (default critic_canary.json)")
    args = parser.parse_args()
    setup_logging()
    asyncio.run(main_async(args))


if __name__ == "__main__":
    main()
