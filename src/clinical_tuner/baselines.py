"""Training-free baselines for the base model: few-shot prompting and simple retrieval-augmented prompting (RAG).

Both baselines use exactly the same system prompt, task instructions and decoding as the fine-tuned model; the only
difference is that worked examples (note -> reference answer, taken from the TRAINING split) are placed in the chat
history before the test note:

* ``fewshot`` - the same k examples for every test note (fixed, seeded choice of short training notes, one synthetic
  and one public dialogue for k=2);
* ``rag`` - the k training notes most similar to the test note under BM25, i.e. the fine-tuning data made available to
  the base model at inference time instead of in its weights.
"""

from __future__ import annotations

import math
import random
from collections import Counter
from typing import Any

from clinical_tuner.prompts import SYSTEM_PROMPT, build_user_message
from clinical_tuner.text import STOPWORDS, stem_tokens

Example = dict[str, Any]  # a processed row: note, task, reference, encounter_id, source


def _tokens(text: str) -> list[str]:
    return [t for t in stem_tokens(text) if t not in STOPWORDS and not t.isdigit()]


class BM25:
    """Okapi BM25 (k1=1.5, b=0.75) over a small in-memory corpus."""

    def __init__(self, docs: list[str], k1: float = 1.5, b: float = 0.75):
        self.k1, self.b = k1, b
        self.tf = [Counter(_tokens(d)) for d in docs]
        self.len = [sum(tf.values()) for tf in self.tf]
        self.avg = sum(self.len) / max(1, len(self.len))
        df = Counter(t for tf in self.tf for t in tf)
        n = len(docs)
        self.idf = {t: math.log(1 + (n - f + 0.5) / (f + 0.5)) for t, f in df.items()}

    def scores(self, query: str) -> list[float]:
        q = Counter(_tokens(query))
        out = []
        for tf, dl in zip(self.tf, self.len, strict=True):
            s = 0.0
            for t in q:
                f = tf.get(t)
                if f:
                    s += self.idf[t] * f * (self.k1 + 1) / (f + self.k1 * (1 - self.b + self.b * dl / self.avg))
            out.append(s)
        return out

    def top(self, query: str, k: int) -> list[int]:
        s = self.scores(query)
        return sorted(range(len(s)), key=lambda i: (-s[i], i))[:k]


def messages_with_examples(task: str, note: str, examples: list[Example]) -> list[dict[str, str]]:
    msgs = [{"role": "system", "content": SYSTEM_PROMPT}]
    for ex in examples:
        msgs += [{"role": "user", "content": build_user_message(task, ex["note"])},
                 {"role": "assistant", "content": ex["reference"]}]
    msgs.append({"role": "user", "content": build_user_message(task, note)})
    return msgs


def fixed_examples(train: list[Example], task: str, k: int, seed: int) -> list[Example]:
    """k short training examples for ``task`` (below-median note length), alternating synthetic / public."""
    rows = sorted((r for r in train if r["task"] == task), key=lambda r: r["id"])
    median = sorted(len(r["note"]) for r in rows)[len(rows) // 2]
    short = [r for r in rows if len(r["note"]) <= median]
    rng = random.Random(seed)
    pools = [[r for r in short if r["source"] == "synthetic"], [r for r in short if r["source"] != "synthetic"]]
    for p in pools:
        rng.shuffle(p)
    picked = []
    while len(picked) < k and any(pools):
        for p in pools:
            if p and len(picked) < k:
                picked.append(p.pop())
    return picked


class Retriever:
    """BM25 over training notes; returns the k most similar training examples for a task."""

    def __init__(self, train: list[Example]):
        self.by_task: dict[str, tuple[BM25, list[Example]]] = {}
        for task in sorted({r["task"] for r in train}):
            rows = sorted((r for r in train if r["task"] == task), key=lambda r: r["id"])
            self.by_task[task] = (BM25([r["note"] for r in rows]), rows)

    def examples(self, task: str, note: str, k: int) -> list[Example]:
        index, rows = self.by_task[task]
        return [rows[i] for i in index.top(note, k)]
