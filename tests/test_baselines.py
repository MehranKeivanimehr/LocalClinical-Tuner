import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "training"))
import evaluate  # noqa: E402

from clinical_tuner.baselines import BM25, Retriever, fixed_examples, messages_with_examples  # noqa: E402
from clinical_tuner.prompts import SYSTEM_PROMPT, build_user_message  # noqa: E402


def _row(i, source, note, task="summary"):
    return {"id": f"{source}-{i}::{task}", "encounter_id": f"{source}-{i}", "task": task, "source": source,
            "note": note, "reference": f"reference {i}"}


TRAIN = [
    _row(0, "synthetic", "Chest pain radiating to the left arm, troponin elevated, started aspirin."),
    _row(1, "synthetic", "Knee pain after a fall, x-ray shows no fracture, ibuprofen as needed."),
    _row(2, "mts-dialog", "Doctor: any cough? Patient: yes, productive cough and fever for three days."),
    _row(3, "mts-dialog", "Doctor: how is the rash? Patient: itchy rash on both arms since Monday, getting better."),
]


def test_bm25_ranks_the_lexically_closest_note_first():
    index = BM25([r["note"] for r in TRAIN])
    assert index.top("patient with chest pain and an elevated troponin", 1) == [0]
    assert index.top("cough with fever", 1) == [2]


def test_retriever_returns_examples_of_the_same_task():
    rows = TRAIN + [_row(9, "synthetic", "Chest pain and troponin rise.", task="soap")]
    ex = Retriever(rows).examples("soap", "chest pain", 2)
    assert [e["task"] for e in ex] == ["soap"]  # only one soap example exists


def test_fixed_examples_mix_sources_and_are_deterministic():
    a = fixed_examples(TRAIN, "summary", 2, seed=3)
    assert {e["source"] for e in a} == {"synthetic", "mts-dialog"}
    assert a == fixed_examples(TRAIN, "summary", 2, seed=3)


def test_messages_put_examples_before_the_test_note():
    msgs = messages_with_examples("summary", "TEST NOTE", TRAIN[:2])
    assert msgs[0] == {"role": "system", "content": SYSTEM_PROMPT}
    assert [m["role"] for m in msgs[1:]] == ["user", "assistant", "user", "assistant", "user"]
    assert msgs[-1]["content"] == build_user_message("summary", "TEST NOTE")
    assert msgs[2]["content"] == "reference 0"


def test_rag_never_uses_the_test_encounter(monkeypatch):
    # a "test" row whose encounter is (wrongly) also in train must trip the leakage assertion
    monkeypatch.setattr(evaluate, "read_jsonl", lambda path: TRAIN)
    cfg = evaluate.load_config()
    leaked = dict(TRAIN[0])
    with pytest.raises(AssertionError, match="test note retrieved"):
        evaluate.with_prompting(cfg, [leaked], "rag")
    clean = _row(7, "synthetic", "Chest pain with troponin elevation.")
    out = evaluate.with_prompting(cfg, [clean], "rag")
    assert out[0]["context_ids"][0] == TRAIN[0]["id"] and len(out[0]["messages"]) == 6
