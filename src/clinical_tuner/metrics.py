"""Evaluation metrics and the reward used to pick on-policy preference negatives.

Reference-based:  ROUGE-L (F), BERTScore (F1, rescaled), entity-level F1 (strict and relaxed matching).
Reference-free :  SOAP structure adherence, JSON validity, medication hallucination (drugs named in the output
                  but absent from the source note), key-entity coverage, summary length compliance.
"""

from __future__ import annotations

import random
import re
from collections.abc import Sequence
from typing import Any

from rapidfuzz import fuzz

from .llm import extract_json
from .prompts import SOAP_SECTIONS
from .quality.drugs import MAX_DOSES, canonical_drug, drug_aliases
from .quality.grounding import _ALIASES
from .quality.rules import _CURATED_RE
from .schemas import Entities, Medication, Symptom
from .text import contains_term, normalize, word_count

ENTITY_CATEGORIES = ("symptoms", "diagnoses", "medications", "procedures")


# --------------------------------------------------------------------------------------------------------------
# Text-overlap metrics
# --------------------------------------------------------------------------------------------------------------
def rouge_l(preds: Sequence[str], refs: Sequence[str]) -> list[float]:
    from rouge_score import rouge_scorer

    scorer = rouge_scorer.RougeScorer(["rougeL"], use_stemmer=True)
    return [scorer.score(r, p)["rougeL"].fmeasure for p, r in zip(preds, refs, strict=True)]


def bertscore(preds: Sequence[str], refs: Sequence[str], model_type: str = "roberta-large", batch_size: int = 32,
              device: str | None = None) -> list[float]:
    from bert_score import score

    _, _, f1 = score(list(preds), list(refs), model_type=model_type, lang="en", rescale_with_baseline=True,
                     batch_size=batch_size, device=device, verbose=False)
    return [float(x) for x in f1]


# --------------------------------------------------------------------------------------------------------------
# Entities
# --------------------------------------------------------------------------------------------------------------
def parse_entities_output(text: str) -> Entities | None:
    """Lenient parser so the base model is not penalized for harmless format variations (case of keys, plain
    strings instead of objects, extra keys, code fences)."""
    obj = extract_json(text)
    if not isinstance(obj, dict):
        return None
    lower = {str(k).lower().strip(): v for k, v in obj.items()}

    def items(key: str) -> list:
        v = lower.get(key) or []
        return v if isinstance(v, list) else [v]

    symptoms = []
    for s in items("symptoms"):
        if isinstance(s, dict) and (s.get("text") or s.get("name")):
            symptoms.append(Symptom(text=str(s.get("text") or s.get("name")), negated=bool(s.get("negated", False))))
        elif isinstance(s, str) and s.strip():
            symptoms.append(Symptom(text=s))
    meds = []
    for m in items("medications"):
        if isinstance(m, dict) and (m.get("name") or m.get("text")):
            meds.append(Medication(name=str(m.get("name") or m.get("text")), dose=_opt(m.get("dose")),
                                   frequency=_opt(m.get("frequency"))))
        elif isinstance(m, str) and m.strip():
            meds.append(Medication(name=m))

    def strs(key: str) -> list[str]:
        out = []
        for x in items(key):
            if isinstance(x, dict):
                x = x.get("text") or x.get("name") or ""
            if str(x).strip():
                out.append(str(x))
        return out

    return Entities(symptoms=symptoms, diagnoses=strs("diagnoses"), medications=meds, procedures=strs("procedures"))


def _opt(v: Any) -> str | None:
    return None if v in (None, "", "null") else str(v)


def _entity_strings(ent: Entities, cat: str) -> list[str]:
    if cat == "symptoms":
        return [s.text for s in ent.symptoms]
    if cat == "medications":
        return [m.name for m in ent.medications]
    return list(getattr(ent, cat))


def _similar(a: str, b: str, relaxed: bool) -> bool:
    na, nb = normalize(a), normalize(b)
    if na == nb:
        return True
    if not relaxed:
        return False
    if canonical_drug(a) == canonical_drug(b) and canonical_drug(a) in MAX_DOSES:
        return True
    if nb in _ALIASES.get(na, ()):  # clinical abbreviations: "HTN" ~ "hypertension"
        return True
    short, long_ = sorted((na, nb), key=len)
    if len(short) >= 3 and contains_term(long_, short) and len(short) / len(long_) >= 0.5:
        return True
    return fuzz.ratio(na, nb) >= 85


def match(pred: list[str], gold: list[str], relaxed: bool) -> list[tuple[int, int]]:
    """Greedy one-to-one matching; returns (pred_idx, gold_idx) pairs."""
    pairs, used = [], set()
    for i, p in enumerate(pred):
        for j, g in enumerate(gold):
            if j not in used and _similar(p, g, relaxed):
                pairs.append((i, j))
                used.add(j)
                break
    return pairs


def _dedup(items: list, key) -> list:
    seen, out = set(), []
    for it in items:
        k = normalize(key(it))
        if k and k not in seen:
            seen.add(k)
            out.append(it)
    return out


def entity_counts(pred: Entities | None, gold: Entities) -> dict[str, Any]:
    """Per-category tp/fp/fn for strict and relaxed matching, plus negation agreement on matched symptoms."""
    out: dict[str, Any] = {"json_valid": pred is not None}
    pred = pred or Entities()
    p_sx, g_sx = _dedup(pred.symptoms, lambda s: s.text), _dedup(gold.symptoms, lambda s: s.text)
    for mode, relaxed in (("strict", False), ("relaxed", True)):
        for cat in ENTITY_CATEGORIES:
            if cat == "symptoms":
                p, g = [s.text for s in p_sx], [s.text for s in g_sx]
            else:
                p = _dedup(_entity_strings(pred, cat), lambda x: x)
                g = _dedup(_entity_strings(gold, cat), lambda x: x)
            m = match(p, g, relaxed)
            out[f"{mode}:{cat}"] = (len(m), len(p) - len(m), len(g) - len(m))
            if cat == "symptoms" and relaxed:
                out["negation"] = (sum(p_sx[i].negated == g_sx[j].negated for i, j in m), len(m))
    return out


def f1(tp: int, fp: int, fn: int) -> float:
    return 2 * tp / (2 * tp + fp + fn) if (tp + fp + fn) else 1.0


def micro_f1(counts: list[dict[str, Any]], mode: str = "relaxed", cats: Sequence[str] = ENTITY_CATEGORIES) -> float:
    tp = fp = fn = 0
    for c in counts:
        for cat in cats:
            a, b, d = c[f"{mode}:{cat}"]
            tp, fp, fn = tp + a, fp + b, fn + d
    return f1(tp, fp, fn)


# --------------------------------------------------------------------------------------------------------------
# Structure, hallucination, coverage
# --------------------------------------------------------------------------------------------------------------
_HEADER_RE = re.compile(r"^\s*\**\s*(SUBJECTIVE|OBJECTIVE|ASSESSMENT|PLAN)\s*\**\s*:", re.MULTILINE)


def soap_structure(text: str) -> float:
    """1.0 iff the four required headers appear exactly once each, in canonical order; otherwise the fraction
    present, scaled by 0.75 if the headers that are present are out of order."""
    found = [m.group(1) for m in _HEADER_RE.finditer(text)]
    present = sum(1 for s in SOAP_SECTIONS if found.count(s) == 1)
    order = [SOAP_SECTIONS.index(s) for s in found]
    in_order = order == sorted(order)
    return present / 4 * (1.0 if in_order else 0.75)


def unsupported_medications(text: str, note: str) -> list[str]:
    """Drug names (curated generic/brand list) mentioned in ``text`` but absent from the note under any alias."""
    note_norm = normalize(note)
    bad = []
    for m in set(_CURATED_RE.findall(normalize(text))):
        generic = canonical_drug(m)
        aliases = drug_aliases(generic) if generic in MAX_DOSES else [m]
        if not any(contains_term(note_norm, a) for a in aliases):
            bad.append(m)
    return sorted(bad)


def key_entity_recall(text: str, gold: Entities) -> float:
    """Fraction of gold diagnoses + medication names that the output mentions (clinical coverage proxy)."""
    keys = list(dict.fromkeys(gold.diagnoses + [m.name for m in gold.medications]))
    if not keys:
        return 1.0
    t = normalize(text)
    hit = 0
    for k in keys:
        nk = normalize(k)
        if contains_term(t, nk) or (len(nk) >= 5 and fuzz.partial_ratio(nk, t) >= 90):
            hit += 1
    return hit / len(keys)


def summary_length_ok(text: str, max_words: int = 120) -> bool:
    return 0 < word_count(text) <= max_words * 1.1


# --------------------------------------------------------------------------------------------------------------
# Reward for preference-pair mining (gold reference scores ~1.0)
# --------------------------------------------------------------------------------------------------------------
def reward(task: str, output: str, ex: dict[str, Any]) -> float:
    gold = Entities.model_validate(ex["gold_entities"])
    if task == "entities":
        pred = parse_entities_output(output)
        if pred is None:
            return 0.0
        c = entity_counts(pred, gold)
        agree, n = c["negation"]
        neg = agree / n if n else 1.0
        return 0.8 * micro_f1([c]) + 0.2 * neg
    hallucinated = 1.0 if unsupported_medications(output, ex["note"]) else 0.0
    rl = rouge_l([output], [ex["reference"]])[0]
    cov = key_entity_recall(output, gold)
    if task == "summary":
        r = 0.4 * rl + 0.4 * cov + 0.2 * float(summary_length_ok(output))
    else:
        r = 0.3 * rl + 0.3 * cov + 0.4 * soap_structure(output)
    return max(0.0, min(1.0, r - 0.5 * hallucinated))


# --------------------------------------------------------------------------------------------------------------
# Uncertainty
# --------------------------------------------------------------------------------------------------------------
def bootstrap_ci(values: Sequence[float], n: int = 1000, seed: int = 0, alpha: float = 0.05) -> tuple[float, float]:
    if not values:
        return (float("nan"), float("nan"))
    rng = random.Random(seed)
    k = len(values)
    means = sorted(sum(values[rng.randrange(k)] for _ in range(k)) / k for _ in range(n))
    return means[int(n * alpha / 2)], means[int(n * (1 - alpha / 2)) - 1]


def bootstrap_micro_f1_ci(counts: list[dict[str, Any]], n: int = 1000, seed: int = 0, mode: str = "relaxed"
                          ) -> tuple[float, float]:
    if not counts:
        return (float("nan"), float("nan"))
    rng = random.Random(seed)
    k = len(counts)
    vals = sorted(micro_f1([counts[rng.randrange(k)] for _ in range(k)], mode) for _ in range(n))
    return vals[int(n * 0.025)], vals[int(n * 0.975) - 1]
