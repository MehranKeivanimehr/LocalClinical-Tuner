#!/usr/bin/env python
"""Evaluate base vs SFT vs SFT+ORPO (and the quantized GGUF) on the held-out test split.

Sub-commands (all config-driven, all resumable):
  generate  Run a model over the test prompts with greedy decoding and identical settings for every model.
            --model <endpoint name> uses any OpenAI-compatible server (vLLM / Ollama / llama.cpp server);
            --backend hf --model-path <dir> runs transformers locally without a server.
  score     Quantitative metrics: ROUGE-L, BERTScore, entity-level F1 (strict + relaxed), negation accuracy,
            SOAP structure, JSON validity, medication-hallucination rate, key-entity coverage - per task and per
            data source, with bootstrap 95% CIs.  -> results/metrics.json
  judge     LLM-as-a-judge with a model that did not generate any training data (Qwen2.5-72B by default):
            absolute 1-5 rubric for every model + pairwise comparisons run in both orders (position-debiased).
  report    Writes results/comparison_table.md and results/sample_outputs.md from the above.

Usage:
    uv run python training/evaluate.py generate --model base
    uv run python training/evaluate.py score
    uv run python training/evaluate.py judge
    uv run python training/evaluate.py report

Model selection (e.g. choosing ORPO's lambda) uses the validation split, never the test split:
    uv run python training/evaluate.py generate --model orpo --tag orpo_l0.1 --split val
    uv run python training/evaluate.py score --split val --models sft orpo_l0.1
    uv run python training/evaluate.py judge --split val --models sft orpo_l0.1 --pairs orpo_l0.1:sft
(outputs go to results/generations_val/, results/metrics_val.json and results/judge_val/).
"""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import logging
import random
import statistics
from collections import defaultdict
from pathlib import Path
from typing import Any

from clinical_tuner.config import Config, add_config_args, load_config
from clinical_tuner.llm import extract_json, gather_with_progress, make_client
from clinical_tuner.metrics import (
    ENTITY_CATEGORIES,
    bertscore,
    bootstrap_ci,
    bootstrap_micro_f1_ci,
    entity_counts,
    key_entity_recall,
    micro_f1,
    parse_entities_output,
    rouge_l,
    soap_structure,
    summary_length_ok,
    unsupported_medications,
)
from clinical_tuner.prompts import TASK_INSTRUCTIONS
from clinical_tuner.schemas import Entities
from clinical_tuner.utils import append_jsonl, read_json, read_jsonl, resolve, setup_logging, write_json

log = logging.getLogger("evaluate")

DISPLAY = {"base": "Base (Qwen2.5-7B-Instruct)", "base_fewshot": "Base + 2-shot prompting",
           "base_rag": "Base + RAG (2 retrieved examples)", "sft": "SFT", "orpo": "SFT + ORPO",
           "gguf_q4": "SFT + ORPO (Q4_K_M GGUF)"}


def model_order(cfg: Config) -> list[str]:
    """Report column order: base model, training-free baselines, fine-tuned models, quantized export."""
    first, rest = cfg.evaluation.models[:1], cfg.evaluation.models[1:]
    return [*first, *cfg.evaluation.baselines, *rest, "gguf_q4"]


def results_dir(cfg: Config) -> Path:
    return resolve(cfg.project.results_dir)


SPLIT = "test"  # set from --split; model selection must use "val", never "test"


def gen_path(cfg: Config, tag: str) -> Path:
    sub = "generations" if SPLIT == "test" else f"generations_{SPLIT}"
    return results_dir(cfg) / sub / f"{tag}.jsonl"


def metrics_path(cfg: Config) -> Path:
    return results_dir(cfg) / ("metrics.json" if SPLIT == "test" else f"metrics_{SPLIT}.json")


def judge_dir(cfg: Config) -> Path:
    return results_dir(cfg) / ("judge" if SPLIT == "test" else f"judge_{SPLIT}")


def load_test(cfg: Config, limit: int | None = None) -> list[dict[str, Any]]:
    rows = read_jsonl(resolve(cfg.data.processed_dir) / f"{SPLIT}.jsonl")
    return rows[:limit] if limit else rows


# ----------------------------------------------------------------------------------------------------------------
# generate
# ----------------------------------------------------------------------------------------------------------------
async def generate_openai(cfg: Config, tag: str, endpoint: str, rows: list[dict[str, Any]]) -> None:
    out = gen_path(cfg, tag)
    done = {r["id"] for r in read_jsonl(out)} if out.exists() else set()
    pending = [r for r in rows if r["id"] not in done]
    client = make_client(cfg.endpoint(endpoint))
    ec = cfg.evaluation

    async def one(r: dict[str, Any]) -> None:
        text = (await client.chat(r["messages"], temperature=ec.temperature, top_p=1.0, max_tokens=ec.max_tokens,
                                  seed=cfg.project.seed, extra_body=ec.extra_body))[0]
        append_jsonl(out, {"id": r["id"], "task": r["task"], "source": r["source"], "output": text,
                           **({"context_ids": r["context_ids"]} if "context_ids" in r else {})})

    log.info("Generating %d/%d outputs for %s via endpoint %r", len(pending), len(rows), tag, endpoint)
    res = await gather_with_progress(pending, one, desc=f"generate:{tag}")
    errs = [e for e in res if isinstance(e, BaseException)]
    await client.close()
    if errs:
        raise SystemExit(f"{len(errs)} generation errors, e.g. {errs[0]!r} (re-run to resume)")


def with_prompting(cfg: Config, rows: list[dict[str, Any]], prompting: str) -> list[dict[str, Any]]:
    """Rewrite the prompts of ``rows`` for a training-free baseline (worked examples from the TRAIN split)."""
    if prompting == "zero":
        return rows
    from clinical_tuner.baselines import Retriever, fixed_examples, messages_with_examples

    train = read_jsonl(resolve(cfg.data.processed_dir) / "train.jsonl")
    k = cfg.evaluation.baseline_examples
    retriever = Retriever(train) if prompting == "rag" else None
    fixed = {t: fixed_examples(train, t, k, cfg.project.seed) for t in TASK_INSTRUCTIONS} if not retriever else {}
    out = []
    for r in rows:
        exs = retriever.examples(r["task"], r["note"], k) if retriever else fixed[r["task"]]
        assert not {e["encounter_id"] for e in exs} & {r["encounter_id"]}, "test note retrieved from train"
        out.append({**r, "messages": messages_with_examples(r["task"], r["note"], exs),
                    "context_ids": [e["id"] for e in exs]})
    return out


def generate_hf(cfg: Config, tag: str, model_path: str, rows: list[dict[str, Any]], batch_size: int) -> None:
    """Server-free fallback: greedy batched generation with transformers (bf16)."""
    import torch
    from transformers import AutoModelForCausalLM, AutoTokenizer

    out = gen_path(cfg, tag)
    done = {r["id"] for r in read_jsonl(out)} if out.exists() else set()
    pending = [r for r in rows if r["id"] not in done]
    tok = AutoTokenizer.from_pretrained(model_path, padding_side="left")
    tok.pad_token = tok.pad_token or tok.eos_token
    model = AutoModelForCausalLM.from_pretrained(model_path, dtype=torch.bfloat16, device_map="auto").eval()
    rep = float(cfg.evaluation.extra_body.get("repetition_penalty", 1.0))
    for i in range(0, len(pending), batch_size):
        batch = pending[i : i + batch_size]
        prompts = [tok.apply_chat_template(r["messages"], tokenize=False, add_generation_prompt=True) for r in batch]
        enc = tok(prompts, return_tensors="pt", padding=True, add_special_tokens=False).to(model.device)
        with torch.no_grad():
            gen = model.generate(**enc, max_new_tokens=cfg.evaluation.max_tokens, do_sample=False,
                                 repetition_penalty=rep, pad_token_id=tok.pad_token_id)
        for r, g in zip(batch, gen, strict=True):
            text = tok.decode(g[enc["input_ids"].shape[1]:], skip_special_tokens=True)
            append_jsonl(out, {"id": r["id"], "task": r["task"], "source": r["source"], "output": text})
        log.info("hf generate %s: %d/%d", tag, min(i + batch_size, len(pending)), len(pending))


# ----------------------------------------------------------------------------------------------------------------
# score
# ----------------------------------------------------------------------------------------------------------------
def _mean_ci(vals: list[float], cfg: Config) -> dict[str, float]:
    lo, hi = bootstrap_ci(vals, cfg.evaluation.bootstrap_samples, cfg.project.seed)
    return {"mean": round(statistics.fmean(vals), 4) if vals else float("nan"), "ci95": [round(lo, 4), round(hi, 4)],
            "n": len(vals)}


def score_rows(cfg: Config, pairs: list[tuple[dict[str, Any], str]], bert: dict[str, float]) -> dict[str, Any]:
    """Aggregate metrics for a list of (test example, model output)."""
    by_task: dict[str, list] = defaultdict(list)
    for ex, out in pairs:
        by_task[ex["task"]].append((ex, out))
    res: dict[str, Any] = {}
    for task, items in by_task.items():
        m: dict[str, Any] = {}
        if task in ("summary", "soap"):
            preds, refs = [o for _, o in items], [e["reference"] for e, _ in items]
            m["rougeL"] = _mean_ci(rouge_l(preds, refs), cfg)
            m["bertscore_f1"] = _mean_ci([bert[e["id"]] for e, _ in items if e["id"] in bert], cfg)
            m["key_entity_recall"] = _mean_ci(
                [key_entity_recall(o, Entities.model_validate(e["gold_entities"])) for e, o in items], cfg)
            m["med_hallucination_rate"] = _mean_ci(
                [float(bool(unsupported_medications(o, e["note"]))) for e, o in items], cfg)
            if task == "soap":
                m["soap_structure"] = _mean_ci([soap_structure(o) for _, o in items], cfg)
            else:
                m["length_compliance"] = _mean_ci([float(summary_length_ok(o)) for _, o in items], cfg)
        else:
            counts = [entity_counts(parse_entities_output(o), Entities.model_validate(e["gold_entities"]))
                      for e, o in items]
            for mode in ("strict", "relaxed"):
                lo, hi = bootstrap_micro_f1_ci(counts, cfg.evaluation.bootstrap_samples, cfg.project.seed, mode)
                m[f"entity_f1_{mode}"] = {"mean": round(micro_f1(counts, mode), 4), "ci95": [round(lo, 4), round(hi, 4)],
                                          "n": len(counts)}
            for cat in ENTITY_CATEGORIES:
                m[f"f1_{cat}"] = round(micro_f1(counts, "relaxed", (cat,)), 4)
            agree = sum(c["negation"][0] for c in counts)
            total = sum(c["negation"][1] for c in counts)
            m["negation_accuracy"] = round(agree / total, 4) if total else None
            m["json_valid_rate"] = _mean_ci([float(c["json_valid"]) for c in counts], cfg)
            m["med_hallucination_rate"] = _mean_ci(
                [float(bool(unsupported_medications(o, e["note"]))) for e, o in items], cfg)
        res[task] = m
    return res


def cmd_score(cfg: Config, args: argparse.Namespace) -> None:
    import torch

    test = {r["id"]: r for r in load_test(cfg)}
    models = args.models or [t for t in [*cfg.evaluation.models, *cfg.evaluation.baselines, "gguf_q4"]
                             if gen_path(cfg, t).exists()]
    out: dict[str, Any] = {"meta": {
        "date": dt.date.today().isoformat(), "split": SPLIT, "test_examples": len(test),
        "test_encounters": len({r["encounter_id"] for r in test.values()}),
        "sources": sorted({r["source"] for r in test.values()}),
        "decoding": {"temperature": cfg.evaluation.temperature, "max_tokens": cfg.evaluation.max_tokens,
                     **cfg.evaluation.extra_body},
        "bertscore_model": cfg.evaluation.bertscore_model, "base_model": cfg.model.base_model,
        "ci": f"bootstrap 95% CI over test examples ({cfg.evaluation.bootstrap_samples} resamples)",
    }, "models": {}}
    device = "cuda" if torch.cuda.is_available() else "cpu"
    for tag in models:
        gens = {g["id"]: g["output"] for g in read_jsonl(gen_path(cfg, tag))}
        missing = [i for i in test if i not in gens]
        if missing:
            log.warning("%s: %d test outputs missing - scoring the %d available", tag, len(missing), len(gens))
        pairs = [(test[i], gens[i]) for i in test if i in gens]
        prose = [(e, o) for e, o in pairs if e["task"] in ("summary", "soap")]
        bs = bertscore([o for _, o in prose], [e["reference"] for e, _ in prose], cfg.evaluation.bertscore_model,
                       cfg.evaluation.bertscore_batch_size, device) if prose else []
        bert = {e["id"]: s for (e, _), s in zip(prose, bs, strict=True)}
        entry = {"n": len(pairs), "by_task": score_rows(cfg, pairs, bert), "by_source": {}}
        for src in out["meta"]["sources"]:
            sub = [(e, o) for e, o in pairs if e["source"] == src]
            if sub:
                entry["by_source"][src] = score_rows(cfg, sub, bert)
        out["models"][tag] = entry
        log.info("Scored %s (%d outputs)", tag, len(pairs))
    old = read_json(metrics_path(cfg)) if metrics_path(cfg).exists() else {}
    if "judge" in old:
        out["judge"] = old["judge"]
    write_json(metrics_path(cfg), out)


# ----------------------------------------------------------------------------------------------------------------
# judge
# ----------------------------------------------------------------------------------------------------------------
JUDGE_ABS_SYSTEM = """You are an expert physician grading AI-generated clinical documentation. You see a source clinical note, the task instructions given to the AI, and the AI's response. Grade ONLY against the source note (there is no reference answer).

Return JSON: {"rationale": "<at most 80 words>", "faithfulness": 1-5, "completeness": 1-5, "format": 1-5, "clinical_usefulness": 1-5, "overall": 1-5}
- faithfulness: every statement is supported by the note; no hallucinated or contradicted findings, diagnoses, medications or doses; negations preserved. Any hallucinated medication or flipped negation caps this at 2.
- completeness: captures the clinically important information the task asks for.
- format: follows the required output format exactly (sentence/word limits, the four SOAP headers, valid JSON with the required keys).
- clinical_usefulness: accurate, concise and directly useful to a clinician.
- overall: holistic quality, weighting faithfulness most."""

JUDGE_PAIR_SYSTEM = """You are an expert physician comparing two AI-generated responses to the same clinical documentation task. Judge ONLY against the source note (there is no reference answer). Priorities, in order: (1) faithfulness - no hallucinated or contradicted facts, medications, doses, or flipped negations; (2) completeness for the task; (3) adherence to the required format; (4) concision and clinical usefulness. Ignore response length except where it violates the task format. Do not let the order of presentation influence you.

Return JSON: {"rationale": "<at most 80 words>", "winner": "A" | "B" | "tie"}"""

JUDGE_USER = """Task instructions given to the AI:
\"\"\"
{instructions}
\"\"\"

Source clinical note:
\"\"\"
{note}
\"\"\"

{responses}"""

ABS_SCHEMA = {"type": "object", "properties": {"rationale": {"type": "string"}, **{
    k: {"type": "integer", "enum": [1, 2, 3, 4, 5]} for k in
    ("faithfulness", "completeness", "format", "clinical_usefulness", "overall")}},
    "required": ["rationale", "faithfulness", "completeness", "format", "clinical_usefulness", "overall"],
    "additionalProperties": False}
PAIR_SCHEMA = {"type": "object", "properties": {"rationale": {"type": "string"},
                                                "winner": {"type": "string", "enum": ["A", "B", "tie"]}},
               "required": ["rationale", "winner"], "additionalProperties": False}


def judge_subset(cfg: Config, test: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rng = random.Random(cfg.project.seed)
    subset = []
    for task in TASK_INSTRUCTIONS:
        items = sorted([r for r in test if r["task"] == task], key=lambda r: r["id"])
        rng.shuffle(items)
        subset += items[: cfg.evaluation.judge_per_task]
    return subset


async def cmd_judge(cfg: Config, args: argparse.Namespace) -> None:
    test = load_test(cfg)
    subset = judge_subset(cfg, test)
    jdir = judge_dir(cfg)
    jdir.mkdir(parents=True, exist_ok=True)
    pairs = judge_pairs(cfg, args)
    models = [m for m in (args.models or [*cfg.evaluation.models, *cfg.evaluation.baselines])
              if gen_path(cfg, m).exists()]
    gens = {m: {g["id"]: g["output"] for g in read_jsonl(gen_path(cfg, m))} for m in models}
    client = make_client(cfg.endpoint(cfg.evaluation.judge_endpoint))
    judge_id = client.cfg.identity

    def user(ex: dict[str, Any], responses: str) -> str:
        return JUDGE_USER.format(instructions=TASK_INSTRUCTIONS[ex["task"]], note=ex["note"], responses=responses)

    abs_path, pair_path = jdir / "absolute.jsonl", jdir / "pairwise.jsonl"
    done_abs = {(r["model"], r["id"]) for r in read_jsonl(abs_path)} if abs_path.exists() else set()
    done_pair = {(r["a"], r["b"], r["id"]) for r in read_jsonl(pair_path)} if pair_path.exists() else set()

    jobs: list[tuple] = []
    for ex in subset:
        for m in models:
            if ex["id"] in gens[m] and (m, ex["id"]) not in done_abs:
                jobs.append(("abs", m, None, ex))
        for a, b in pairs:
            if a in gens and b in gens and ex["id"] in gens[a] and ex["id"] in gens[b]:
                for x, y in ((a, b), (b, a)):  # both presentation orders
                    if (x, y, ex["id"]) not in done_pair:
                        jobs.append(("pair", x, y, ex))

    async def run(job: tuple) -> None:
        kind, x, y, ex = job
        if kind == "abs":
            msgs = [{"role": "system", "content": JUDGE_ABS_SYSTEM},
                    {"role": "user", "content": user(ex, f"AI response:\n\"\"\"\n{gens[x][ex['id']]}\n\"\"\"")}]
            raw = (await client.chat(msgs, temperature=0.0, max_tokens=600, json_schema=ABS_SCHEMA))[0]
            v = extract_json(raw) or {}
            append_jsonl(abs_path, {"model": x, "id": ex["id"], "task": ex["task"], "source": ex["source"],
                                    "judge": judge_id, **{k: v.get(k) for k in ABS_SCHEMA["properties"]}})
        else:
            resp = (f"Response A:\n\"\"\"\n{gens[x][ex['id']]}\n\"\"\"\n\nResponse B:\n\"\"\"\n{gens[y][ex['id']]}\n\"\"\"")
            msgs = [{"role": "system", "content": JUDGE_PAIR_SYSTEM}, {"role": "user", "content": user(ex, resp)}]
            raw = (await client.chat(msgs, temperature=0.0, max_tokens=400, json_schema=PAIR_SCHEMA))[0]
            v = extract_json(raw) or {}
            append_jsonl(pair_path, {"a": x, "b": y, "id": ex["id"], "task": ex["task"], "source": ex["source"],
                                     "judge": judge_id, "winner": v.get("winner"), "rationale": v.get("rationale")})

    log.info("Judge %s: %d jobs on %d items", judge_id, len(jobs), len(subset))
    res = await gather_with_progress(jobs, run, desc="judge")
    await client.close()
    errs = [e for e in res if isinstance(e, BaseException)]
    if errs:
        log.error("%d judge errors, e.g. %r (re-run to resume)", len(errs), errs[0])
    summarize_judge(cfg, pairs)


def judge_pairs(cfg: Config, args: argparse.Namespace) -> list[tuple[str, str]]:
    if getattr(args, "pairs", None):
        return [tuple(p.split(":", 1)) for p in args.pairs]  # type: ignore[misc]
    return [tuple(p) for p in cfg.evaluation.judge_pairs]  # type: ignore[misc]


def summarize_judge(cfg: Config, pairs: list[tuple[str, str]]) -> dict[str, Any]:
    jdir = judge_dir(cfg)
    summary: dict[str, Any] = {"absolute": {}, "pairwise": {}}
    if (jdir / "absolute.jsonl").exists():
        rows = [r for r in read_jsonl(jdir / "absolute.jsonl") if isinstance(r.get("overall"), int)]
        summary["judge_model"] = rows[0]["judge"] if rows else None
        order = {m: k for k, m in enumerate(model_order(cfg))}
        for m in sorted({r["model"] for r in rows}, key=lambda x: (order.get(x, len(order)), x)):
            mr = [r for r in rows if r["model"] == m]
            summary["absolute"][m] = {k: _mean_ci([float(r[k]) for r in mr], cfg) for k in
                                      ("faithfulness", "completeness", "format", "clinical_usefulness", "overall")}
            summary["absolute"][m]["by_task"] = {t: round(statistics.fmean(float(r["overall"]) for r in mr
                                                                           if r["task"] == t), 3)
                                                 for t in sorted({r["task"] for r in mr})}
    if (jdir / "pairwise.jsonl").exists():
        rows = read_jsonl(jdir / "pairwise.jsonl")
        verdicts: dict[tuple[str, str, str], dict[str, str | None]] = defaultdict(dict)
        for r in rows:
            winner = {"A": r["a"], "B": r["b"]}.get(r["winner"], "tie")
            verdicts[(min(r["a"], r["b"]), max(r["a"], r["b"]), r["id"])][f"{r['a']}>{r['b']}"] = winner

        def wtl(final: list[str], a: str, b: str) -> dict[str, Any]:
            n = len(final)
            return {"n": n, "win": round(sum(f == a for f in final) / n, 4),
                    "tie": round(sum(f == "tie" for f in final) / n, 4),
                    "loss": round(sum(f == b for f in final) / n, 4)}

        for a, b in pairs:
            key_pair = (min(a, b), max(a, b))
            final: list[tuple[str, str]] = []  # (example id, outcome)
            for (x, y, _id), v in verdicts.items():
                if (x, y) != key_pair or len(v) < 2:
                    continue
                w = set(v.values())
                final.append((_id, w.pop() if len(w) == 1 else "tie"))  # disagreement across orders -> tie
            if final:
                outcomes = [f for _, f in final]
                summary["pairwise"][f"{a}_vs_{b}"] = {
                    **wtl(outcomes, a, b),
                    "position_consistency": round(sum(len(set(v.values())) == 1 for (x, y, _), v in verdicts.items()
                                                      if (x, y) == key_pair and len(v) == 2) / len(final), 4),
                    "by_task": {t: wtl([f for i, f in final if i.endswith(f"::{t}")], a, b)
                                for t in TASK_INSTRUCTIONS if any(i.endswith(f"::{t}") for i, _ in final)},
                }
    metrics = read_json(metrics_path(cfg)) if metrics_path(cfg).exists() else {"models": {}}
    metrics["judge"] = summary
    write_json(metrics_path(cfg), metrics)
    return summary


# ----------------------------------------------------------------------------------------------------------------
# report
# ----------------------------------------------------------------------------------------------------------------
def _fmt(m: Any, pct: bool = False, ci: bool = True) -> str:
    if m is None:
        return "-"
    if isinstance(m, dict):
        v, (lo, hi) = m["mean"], m.get("ci95", [None, None])
        if pct:
            return f"{100 * v:.1f}%" + (f" ({100 * lo:.1f}-{100 * hi:.1f})" if ci and lo is not None else "")
        return f"{v:.3f}" + (f" ({lo:.3f}-{hi:.3f})" if ci and lo is not None else "")
    return f"{100 * m:.1f}%" if pct else f"{m:.3f}"


ROWS = [  # (task, metric, label, is_percent, higher_is_better)
    ("summary", "rougeL", "Summary ROUGE-L", False, True),
    ("summary", "bertscore_f1", "Summary BERTScore F1", False, True),
    ("summary", "key_entity_recall", "Summary key-entity coverage", True, True),
    ("summary", "length_compliance", "Summary length ≤ 132 words (120 + 10%)", True, True),
    ("summary", "med_hallucination_rate", "Summary medication hallucination", True, False),
    ("soap", "rougeL", "SOAP ROUGE-L", False, True),
    ("soap", "bertscore_f1", "SOAP BERTScore F1", False, True),
    ("soap", "soap_structure", "SOAP structure adherence", True, True),
    ("soap", "key_entity_recall", "SOAP key-entity coverage", True, True),
    ("soap", "med_hallucination_rate", "SOAP medication hallucination", True, False),
    ("entities", "entity_f1_relaxed", "Entity F1 (relaxed)", False, True),
    ("entities", "entity_f1_strict", "Entity F1 (strict)", False, True),
    ("entities", "negation_accuracy", "Symptom negation accuracy", True, True),
    ("entities", "json_valid_rate", "Valid JSON", True, True),
]


def cmd_report(cfg: Config, args: argparse.Namespace) -> None:
    rdir = results_dir(cfg)
    if judge_dir(cfg).exists():  # refresh the judge summary from the raw verdicts (and the configured pairs)
        summarize_judge(cfg, judge_pairs(cfg, args))
    metrics = read_json(rdir / "metrics.json")
    models = [m for m in model_order(cfg) if m in metrics["models"]]
    meta = metrics["meta"]
    L = ["# Results: base vs fine-tuned\n",
         f"Test set: **{meta['test_examples']} examples** from {meta['test_encounters']} held-out encounters "
         f"(sources: {', '.join(meta['sources'])}). Greedy decoding with identical settings for every model "
         f"({meta['decoding']}). Values are means with {meta['ci']} in parentheses.\n",
         "## Main comparison\n",
         "| Metric | " + " | ".join(DISPLAY.get(m, m) for m in models) + " |",
         "|---|" + "---|" * len(models)]
    for task, key, label, pct, hib in ROWS:
        vals = [metrics["models"][m]["by_task"].get(task, {}).get(key) for m in models]
        nums = [v["mean"] if isinstance(v, dict) else v for v in vals]
        best = None
        valid = [n for n in nums if isinstance(n, (int, float))]
        if valid:
            best = max(valid) if hib else min(valid)
        cells = [(f"**{_fmt(v, pct)}**" if n == best and len(valid) > 1 else _fmt(v, pct)) for v, n in zip(vals, nums,
                                                                                                         strict=True)]
        arrow = "↑" if hib else "↓"
        L.append(f"| {label} {arrow} | " + " | ".join(cells) + " |")
    j = metrics.get("judge") or {}
    if j.get("absolute"):
        L += ["", f"## LLM-as-a-judge (judge: `{j.get('judge_model')}`, reference-free, 1-5)\n",
              "| Dimension | " + " | ".join(DISPLAY.get(m, m) for m in j["absolute"]) + " |",
              "|---|" + "---|" * len(j["absolute"])]
        for dim in ("faithfulness", "completeness", "format", "clinical_usefulness", "overall"):
            L.append(f"| {dim} | " + " | ".join(_fmt(j["absolute"][m][dim]) for m in j["absolute"]) + " |")
    if j.get("pairwise"):
        L += ["", "**Pairwise (each comparison judged in both presentation orders; disagreement counts as a tie)**\n",
              "| Comparison | n | win | tie | loss | position consistency |", "|---|---|---|---|---|---|"]
        for k, v in j["pairwise"].items():
            a, b = k.split("_vs_")
            L.append(f"| {DISPLAY.get(a, a)} vs {DISPLAY.get(b, b)} | {v['n']} | {100 * v['win']:.1f}% | "
                     f"{100 * v['tie']:.1f}% | {100 * v['loss']:.1f}% | {100 * v['position_consistency']:.1f}% |")
        tasks = [t for t in TASK_INSTRUCTIONS if any(t in v.get("by_task", {}) for v in j["pairwise"].values())]
        if tasks:
            L += ["", "Pairwise by task (win / tie / loss, %):\n",
                  "| Comparison | " + " | ".join(tasks) + " |", "|---|" + "---|" * len(tasks)]
            for k, v in j["pairwise"].items():
                a, b = k.split("_vs_")
                cells = []
                for t in tasks:
                    bt = v.get("by_task", {}).get(t)
                    cells.append(f"{100 * bt['win']:.0f} / {100 * bt['tie']:.0f} / {100 * bt['loss']:.0f}" if bt else "-")
                L.append(f"| {DISPLAY.get(a, a)} vs {DISPLAY.get(b, b)} | " + " | ".join(cells) + " |")
    L += ["", "## By data source\n"]
    for src in meta["sources"]:
        L += [f"**{src}**\n", "| Metric | " + " | ".join(DISPLAY.get(m, m) for m in models) + " |",
              "|---|" + "---|" * len(models)]
        for task, key, label, pct, _ in ROWS:
            if key in ("bertscore_f1",):
                continue
            vals = [metrics["models"][m]["by_source"].get(src, {}).get(task, {}).get(key) for m in models]
            if all(v is None for v in vals):
                continue
            L.append(f"| {label} | " + " | ".join(_fmt(v, pct, ci=False) for v in vals) + " |")
        L.append("")
    (rdir / "comparison_table.md").write_text("\n".join(L) + "\n", encoding="utf-8")

    # sample outputs
    test = {r["id"]: r for r in load_test(cfg)}
    gens = {m: {g["id"]: g["output"] for g in read_jsonl(gen_path(cfg, m))} for m in model_order(cfg)
            if gen_path(cfg, m).exists()}
    rng = random.Random(cfg.project.seed)
    S = ["# Sample outputs (held-out test set)\n",
         "Randomly selected (seeded) test encounters. Notes are synthetic or public role-played dialogues.\n"]
    for task in TASK_INSTRUCTIONS:
        ids = sorted(i for i in test if test[i]["task"] == task and all(i in g for g in gens.values()))
        for i in rng.sample(ids, min(cfg.evaluation.n_sample_outputs, len(ids))):
            ex = test[i]
            S.append(f"## {task.upper()} - `{ex['encounter_id']}` ({ex['source']})\n")
            note = ex["note"] if len(ex["note"]) < 2500 else ex["note"][:2500] + " [...]"
            S.append(f"<details><summary>Source note</summary>\n\n```text\n{note}\n```\n</details>\n")
            S.append(f"**Reference**\n\n```text\n{ex['reference']}\n```\n")
            for m, g in gens.items():
                S.append(f"**{DISPLAY.get(m, m)}**\n\n```text\n{g[i].strip()}\n```\n")
    (rdir / "sample_outputs.md").write_text("\n".join(S), encoding="utf-8")
    log.info("Wrote %s and %s", rdir / "comparison_table.md", rdir / "sample_outputs.md")


def main() -> None:
    parser = add_config_args(argparse.ArgumentParser(description=__doc__,
                                                     formatter_class=argparse.RawDescriptionHelpFormatter))
    sub = parser.add_subparsers(dest="cmd", required=True)
    g = sub.add_parser("generate")
    g.add_argument("--model", required=True, help="endpoint name (openai backend) or output tag (hf backend)")
    g.add_argument("--tag", default=None, help="name of the generations file (default: --model)")
    g.add_argument("--backend", choices=["openai", "hf"], default="openai")
    g.add_argument("--model-path", default=None, help="hf backend: model directory or hub id")
    g.add_argument("--batch-size", type=int, default=16)
    g.add_argument("--limit", type=int, default=None)
    g.add_argument("--split", default="test", choices=["test", "val"], help="val is for model selection")
    g.add_argument("--prompting", default="zero", choices=["zero", "fewshot", "rag"],
                   help="training-free baselines: fixed worked examples (fewshot) or BM25-retrieved ones (rag)")
    s = sub.add_parser("score")
    s.add_argument("--models", nargs="*", default=None)
    s.add_argument("--split", default="test", choices=["test", "val"])
    j = sub.add_parser("judge")
    j.add_argument("--models", nargs="*", default=None, help="models to grade (default: evaluation.models)")
    j.add_argument("--pairs", nargs="*", default=None, help="pairwise comparisons as a:b (default: judge_pairs)")
    j.add_argument("--split", default="test", choices=["test", "val"])
    sub.add_parser("report")
    args = parser.parse_args()
    setup_logging()
    cfg = load_config(args.config, args.overrides)
    global SPLIT
    SPLIT = getattr(args, "split", "test")
    if args.cmd == "generate":
        rows = with_prompting(cfg, load_test(cfg, args.limit), args.prompting)
        tag = args.tag or (args.model if args.prompting == "zero" else f"{args.model}_{args.prompting}")
        if args.backend == "hf":
            generate_hf(cfg, tag, args.model_path or args.model, rows, args.batch_size)
        else:
            asyncio.run(generate_openai(cfg, tag, args.model, rows))
    elif args.cmd == "score":
        cmd_score(cfg, args)
    elif args.cmd == "judge":
        asyncio.run(cmd_judge(cfg, args))
    else:
        cmd_report(cfg, args)


if __name__ == "__main__":
    main()
