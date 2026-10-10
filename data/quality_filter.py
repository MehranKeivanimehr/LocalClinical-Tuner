#!/usr/bin/env python
"""Quality filter applied uniformly to BOTH data sources (synthetic + Hugging Face) after generation/labeling.

Stages (each rejection records its stage and reasons):
  1. Global near-duplicate removal across all records (word-trigram Jaccard).
  2. Rule-based clinical sanity checks (``clinical_tuner.quality.rules``): vital/lab plausibility, drug max-dose
     table, age/sex consistency, allergy-vs-prescription conflicts, SOAP completeness, entity grounding,
     medications in labels that are absent from the note, negation consistency. Any critical finding, or more than
     ``rules.max_major`` major findings, rejects the encounter.
  3. LLM critic (``clinical_tuner.quality.critic``): a model *different from every model that generated the record*
     scores a JSON rubric with severity-tagged errors. Any critical error, a low overall score, or a very low
     dimension score rejects the encounter.
  4. Exports a random sample of accepted and rejected encounters for human review and writes the data card.

Outputs: data/filtered/{accepted,rejected}.jsonl, data/filtered/quality_stats.json, data/review/review_sample.{md,jsonl},
data/DATA_CARD.md.

Usage:
    uv run python data/quality_filter.py
    uv run python data/quality_filter.py --no-critic          # rules only (e.g. without a critic server)
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import random
from collections import Counter, defaultdict
from typing import Any

from clinical_tuner.config import Config, add_config_args, load_config
from clinical_tuner.datacard import write_data_card
from clinical_tuner.llm import BaseChatClient, ProviderUnavailableError, gather_with_progress, make_client
from clinical_tuner.prompts import render_entities, render_soap
from clinical_tuner.quality.critic import CRITIC_VERSION, DIMENSIONS, CriticVerdict, run_critic
from clinical_tuner.quality.phi import scrub_phi
from clinical_tuner.quality.rules import run_rules
from clinical_tuner.text import jaccard, normalize, shingles
from clinical_tuner.utils import (
    append_jsonl,
    read_jsonl,
    resolve,
    setup_logging,
    stable_hash,
    write_json,
    write_jsonl,
)

log = logging.getLogger("quality")


def model_family(identity: str) -> str:
    """Coarse model family used for the critic-independence check: every Claude variant (API or app) counts as one
    family; other models are compared by name without the organisation prefix."""
    name = identity.lower().split("/")[-1]
    return "claude" if "claude" in name else name


def generator_models(provenance: dict[str, str]) -> set[str]:
    """Model identities that produced any part of a record ('dataset:'/'reference:' parts are human-written)."""
    models = set()
    for p in provenance.values():
        if "restructured:" in p:
            models.add(p.split("restructured:", 1)[1])
        elif not (p.startswith("dataset:") or p.startswith("reference:")):
            models.add(p)
    return models


def phi_sweep(record: dict[str, Any], counts: Counter) -> None:
    """Final, idempotent PHI-pattern sweep over a public record's note AND every label (teachers can normalize a
    spoken date in the note, e.g. 'June thirty two thousand and four', into a written one in a label)."""
    def scrub(text: str) -> str:
        out, c = scrub_phi(text)
        counts.update(c)
        return out

    record["note"] = scrub(record["note"])
    ann = record["annotation"]
    ann["summary"] = scrub(ann["summary"])
    for k in ("subjective", "objective", "assessment"):
        ann["soap"][k] = scrub(ann["soap"][k])
    ann["soap"]["plan"] = [scrub(p) for p in ann["soap"]["plan"]]
    ent = ann["entities"]
    for sx in ent["symptoms"]:
        sx["text"] = scrub(sx["text"])
    ent["diagnoses"] = [scrub(d) for d in ent["diagnoses"]]
    ent["procedures"] = [scrub(p) for p in ent["procedures"]]
    for m in ent["medications"]:
        m["name"] = scrub(m["name"])


def global_dedup(records: list[dict[str, Any]], threshold: float) -> tuple[list[dict], list[dict]]:
    kept, dropped, kept_sh, seen = [], [], [], set()
    for r in records:
        key = normalize(r["note"])
        sh = shingles(r["note"])
        dup = key in seen
        if not dup:
            for other in kept_sh:
                small, big = sorted((len(sh), len(other)))
                if big and small / big >= threshold and jaccard(sh, other) >= threshold:
                    dup = True
                    break
        if dup:
            dropped.append({**r, "stage": "dedup", "reasons": ["near_duplicate"]})
        else:
            seen.add(key)
            kept_sh.append(sh)
            kept.append(r)
    return kept, dropped


class CriticPool:
    """Lazily-created critic clients; picks the first one whose identity differs from the record's generators."""

    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.clients: dict[str, BaseChatClient | None] = {}

    def _client(self, name: str) -> BaseChatClient | None:
        if name not in self.clients:
            try:
                self.clients[name] = make_client(self.cfg.endpoint(name))
            except ProviderUnavailableError as e:
                log.warning("Critic endpoint %r unavailable: %s", name, e)
                self.clients[name] = None
        return self.clients[name]

    async def preflight(self) -> None:
        """Drop critic endpoints that are unreachable or lack credentials before thousands of calls start."""
        for name in self.cfg.data.quality.critic.endpoints:
            client = self._client(name)
            if client is None:
                continue
            try:
                await client.chat([{"role": "user", "content": "Reply with OK."}], max_tokens=8)
                log.info("Critic endpoint %r (%s) is available", name, client.cfg.identity)
            except Exception as e:  # noqa: BLE001
                log.warning("Critic endpoint %r unusable (%s) - skipping it", name, type(e).__name__)
                self.clients[name] = None

    def pick(self, record: dict[str, Any]) -> BaseChatClient | None:
        families = {model_family(g) for g in generator_models(record["provenance"])}
        for name in self.cfg.data.quality.critic.endpoints:
            ep = self.cfg.endpoint(name)
            if model_family(ep.identity) in families:
                continue  # the critic must be a different model (family) from every generator of this record
            if record.get("private") and ep.provider != "openai":
                continue  # private text is never sent to a remote API
            if record.get("private") and not str(ep.base_url or "").startswith(("http://127.0.0.1", "http://localhost")):
                continue
            client = self._client(name)
            if client is not None:
                return client
        return None

    def disable(self, client: BaseChatClient) -> None:
        for k, v in self.clients.items():
            if v is client:
                self.clients[k] = None

    async def close(self) -> None:
        for c in self.clients.values():
            if c is not None:
                await c.close()


def _label_hash(record: dict[str, Any], entities: dict[str, Any]) -> str:
    return stable_hash(record["note"] + json.dumps(record["annotation"]["summary"]) +
                       json.dumps(record["annotation"]["soap"], sort_keys=True) + json.dumps(entities, sort_keys=True))


async def critic_stage(cfg: Config, records: list[dict[str, Any]], cache_path) -> dict[str, dict[str, Any]]:
    """Returns encounter_id -> {"critic": model identity, "verdict": dict} or {"error": str}."""
    cache: dict[str, dict[str, Any]] = {}
    if cache_path.exists():
        for row in read_jsonl(cache_path):
            cache[row["key"]] = row
    pool = CriticPool(cfg)
    await pool.preflight()
    ccfg = cfg.data.quality.critic
    out: dict[str, dict[str, Any]] = {}

    async def one(r: dict[str, Any]) -> None:
        key = f"{r['encounter_id']}:{CRITIC_VERSION}:{_label_hash(r, r['_grounded'])}"
        if key in cache and "verdict" in cache[key]:
            out[r["encounter_id"]] = cache[key]
            return
        client = pool.pick(r)
        if client is None:
            out[r["encounter_id"]] = {"error": "no_independent_critic"}
            return
        try:
            verdict = await run_critic(client, r, r["_grounded"], ccfg.max_tokens)
        except ProviderUnavailableError:
            pool.disable(client)
            out[r["encounter_id"]] = {"error": "no_independent_critic"}
            return
        except Exception as e:  # noqa: BLE001
            out[r["encounter_id"]] = {"error": f"critic_failed:{type(e).__name__}"}
            return
        row = {"key": key, "critic": client.cfg.identity, "verdict": verdict.model_dump()}
        append_jsonl(cache_path, row)
        out[r["encounter_id"]] = row

    await gather_with_progress(records, one, desc="LLM critic")
    usage = {c.cfg.identity: dict(c.usage) for c in pool.clients.values() if c is not None and c.usage}
    await pool.close()
    out["__usage__"] = usage  # type: ignore[assignment]
    return out


def review_markdown(rows: list[dict[str, Any]], title: str) -> str:
    parts = [f"## {title} ({len(rows)})\n"]
    for r in rows:
        ann = r["annotation"]
        q = r.get("quality") or {}
        parts.append(f"### {r['encounter_id']}  -  source: `{r['source']}`  -  "
                     f"{'REJECTED at ' + r.get('stage', '?') + ': ' + ', '.join(r.get('reasons', [])) if r.get('stage') else 'ACCEPTED'}\n")
        crit = (q.get("critic") or {}).get("verdict")
        if crit:
            parts.append(f"Critic ({q['critic'].get('critic')}): overall {crit['overall']}/5; scores "
                         + ", ".join(f"{k}={v}" for k, v in crit["scores"].items()) + "\n")
            for e in crit["errors"]:
                parts.append(f"- critic **{e['severity']}** [{e['field']}/{e['category']}] {e['description']}")
        for f in (q.get("rules") or {}).get("findings", []):
            parts.append(f"- rule **{f['severity']}** [{f['check']}] {f['message']}")
        parts.append(f"\n<details><summary>Note ({len(r['note'].split())} words)</summary>\n\n```text\n{r['note']}\n```\n"
                     f"</details>\n\n**Summary**: {ann['summary']}\n\n<details><summary>SOAP</summary>\n\n```text\n"
                     f"{render_soap(ann['soap'])}\n```\n</details>\n\n<details><summary>Entities</summary>\n\n```json\n"
                     f"{render_entities(ann['entities'])}\n```\n</details>\n\n**Human verdict**: [ ] accept  [ ] reject"
                     f"  -  notes:\n\n---\n")
    return "\n".join(parts)


def stratified_sample(rows: list[dict[str, Any]], n: int, rng: random.Random) -> list[dict[str, Any]]:
    by_src: dict[str, list] = defaultdict(list)
    for r in rows:
        by_src[r["source"]].append(r)
    picked: list[dict[str, Any]] = []
    for _src, items in sorted(by_src.items()):
        k = max(1, round(n * len(items) / max(1, len(rows))))
        picked += rng.sample(items, min(k, len(items)))
    rng.shuffle(picked)
    return picked[:n] if len(picked) > n else picked


async def main_async(args: argparse.Namespace) -> None:
    cfg = load_config(args.config, args.overrides)
    raw_dir, out_dir, review_dir = resolve(cfg.data.raw_dir), resolve(cfg.data.filtered_dir), resolve(cfg.data.review_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    review_dir.mkdir(parents=True, exist_ok=True)
    q = cfg.data.quality

    records: list[dict[str, Any]] = []
    for name in ("synthetic.jsonl", "external.jsonl", "hf.jsonl"):
        p = raw_dir / name
        if p.exists():
            records += read_jsonl(p)
        else:
            log.warning("Missing %s - continuing without it", p)
    log.info("Loaded %d records: %s", len(records), dict(Counter(r["source"] for r in records)))
    public = {s.name for s in cfg.data.hf.sources}
    phi_counts: Counter = Counter()
    for r in records:
        if r["source"] in public or r.get("private"):
            phi_sweep(r, phi_counts)
    if phi_counts:
        log.info("Final PHI sweep on public/private records replaced: %s", dict(phi_counts))

    rejected: list[dict[str, Any]] = []
    records, dups = global_dedup(records, cfg.data.near_duplicate_jaccard)
    rejected += dups
    log.info("Dedup removed %d", len(dups))

    # Stage 2: rules
    passed_rules: list[dict[str, Any]] = []
    rule_counts: Counter = Counter()
    for r in records:
        res = run_rules(r, q.rules)
        r["_rules"] = res.to_dict()
        r["_grounded"] = res.entities.model_dump()
        for f in res.findings:
            rule_counts[f"{f.check}:{f.severity}"] += 1
        if res.rejected(q.rules.max_major):
            reasons = sorted({f"rule:{f.check}" for f in res.findings if f.severity == "critical"} or
                             {f"rule:{f.check}:major" for f in res.findings if f.severity == "major"})
            rejected.append({**r, "stage": "rules", "reasons": reasons})
        else:
            passed_rules.append(r)
    log.info("Rules: %d passed, %d rejected", len(passed_rules), len(records) - len(passed_rules))

    # Stage 3: LLM critic
    critic_usage: dict[str, Any] = {}
    accepted: list[dict[str, Any]] = []
    if q.critic.enabled and not args.no_critic:
        results = await critic_stage(cfg, passed_rules, out_dir / "critic_cache.jsonl")
        run_usage = results.pop("__usage__", {})  # type: ignore[arg-type]
        if run_usage:
            append_jsonl(out_dir / "critic_usage.jsonl", run_usage)
        for row in read_jsonl(out_dir / "critic_usage.jsonl") if (out_dir / "critic_usage.jsonl").exists() else []:
            for ident, u in row.items():
                critic_usage.setdefault(ident, {})
                for k, v in u.items():
                    critic_usage[ident][k] = critic_usage[ident].get(k, 0) + v
        if args.cache_only:
            pending = Counter(v.get("error") for v in results.values() if isinstance(v, dict) and "verdict" not in v)
            log.info("Cache-only pass: %d verdicts available, pending: %s. Re-run with the other critic server up.",
                     sum("verdict" in v for v in results.values() if isinstance(v, dict)), dict(pending))
            return
        for r in passed_rules:
            res = results.get(r["encounter_id"], {"error": "missing"})
            r["_critic"] = res
            if "verdict" not in res:
                rejected.append({**r, "stage": "critic", "reasons": [res.get("error", "critic_error")]})
                continue
            ok, reasons = CriticVerdict.model_validate(res["verdict"]).decision(q.critic.min_overall,
                                                                               q.critic.min_dimension)
            if ok:
                accepted.append(r)
            else:
                rejected.append({**r, "stage": "critic", "reasons": reasons})
    else:
        log.warning("LLM critic disabled - accepting all rule-passing records")
        accepted = passed_rules

    def finalize(r: dict[str, Any]) -> dict[str, Any]:
        out = {k: v for k, v in r.items() if not k.startswith("_")}
        if "_grounded" in r and "stage" not in r:
            out["annotation"] = {**r["annotation"], "entities": r["_grounded"]}
        out["quality"] = {"rules": r.get("_rules"), "critic": r.get("_critic")}
        return out

    accepted = [finalize(r) for r in accepted]
    rejected = [finalize(r) for r in rejected]
    write_jsonl(out_dir / "accepted.jsonl", accepted)
    write_jsonl(out_dir / "rejected.jsonl", rejected)

    # Stats for the data card
    funnel: dict[str, Counter] = defaultdict(Counter)
    for r in accepted:
        funnel[r["source"]]["accepted"] += 1
    reasons_by: dict[str, Counter] = defaultdict(Counter)
    for r in rejected:
        funnel[r["source"]][f"rejected_{r['stage']}"] += 1
        for reason in r["reasons"]:
            reasons_by[f"{r['stage']}"][reason] += 1
            reasons_by[f"{r['stage']}|{r['source']}"][reason] += 1
    scores: dict[str, list[int]] = defaultdict(list)
    err_counts: Counter = Counter()
    critic_models: Counter = Counter()
    for r in accepted + [x for x in rejected if x["stage"] == "critic"]:
        c = (r.get("quality") or {}).get("critic") or {}
        if "verdict" in c:
            critic_models[f"{r['source']}<-{c['critic']}"] += 1
            for d in DIMENSIONS:
                scores[d].append(c["verdict"]["scores"].get(d, 0))
            scores["overall"].append(c["verdict"]["overall"])
            for e in c["verdict"]["errors"]:
                err_counts[f"{e['severity']}:{e['category']}"] += 1
    stats = {
        "input_records": len(records) + len(dups),
        "accepted": len(accepted),
        "rejected": len(rejected),
        "funnel_by_source": {k: dict(v) for k, v in funnel.items()},
        "rejection_reasons": {k: dict(v.most_common()) for k, v in reasons_by.items()},
        "rule_findings": dict(sorted(rule_counts.items())),
        "critic_models": dict(critic_models),
        "critic_score_means": {k: round(sum(v) / len(v), 3) for k, v in scores.items() if v},
        "critic_error_counts": dict(err_counts.most_common()),
        "critic_usage": critic_usage,
        "accepted_by_source": dict(Counter(r["source"] for r in accepted)),
        "phi_final_sweep": dict(phi_counts),
    }
    write_json(out_dir / "quality_stats.json", stats)

    rng = random.Random(cfg.project.seed)
    sample_acc = stratified_sample(accepted, q.review.n_accepted, rng)
    sample_rej = stratified_sample(rejected, q.review.n_rejected, rng)
    write_jsonl(review_dir / "review_sample.jsonl",
                [{**r, "review": {"status": "accepted" if r in sample_acc else "rejected",
                                  "human_verdict": None, "human_notes": ""}} for r in sample_acc + sample_rej])
    (review_dir / "review_sample.md").write_text(
        "# Human review sample\n\nRandom (seeded, source-stratified) sample of accepted and rejected encounters. "
        "Tick a verdict for each item; disagreements with the automatic filter are the most useful signal.\n\n"
        + review_markdown(sample_acc, "Accepted") + "\n" + review_markdown(sample_rej, "Rejected"), encoding="utf-8")

    write_data_card(cfg)
    log.info("Accepted %d / %d (%s). Stats -> %s", len(accepted), stats["input_records"],
             stats["accepted_by_source"], out_dir / "quality_stats.json")


def main() -> None:
    parser = add_config_args(argparse.ArgumentParser(description=__doc__,
                                                     formatter_class=argparse.RawDescriptionHelpFormatter))
    parser.add_argument("--no-critic", action="store_true", help="Skip the LLM critic stage")
    parser.add_argument("--cache-only", action="store_true",
                        help="Only fill the critic cache with the critic servers that are up now (no outputs). Use when "
                             "two critic models cannot be served at the same time.")
    args = parser.parse_args()
    setup_logging()
    asyncio.run(main_async(args))


if __name__ == "__main__":
    main()
