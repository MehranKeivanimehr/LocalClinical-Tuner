import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "training"))
import evaluate  # noqa: E402

from clinical_tuner.config import load_config  # noqa: E402
from clinical_tuner.utils import read_json, write_jsonl  # noqa: E402


def _cfg(tmp_path):
    return load_config(overrides=[f"project.results_dir={tmp_path}"])


def test_split_routes_outputs_away_from_test_results(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path)
    monkeypatch.setattr(evaluate, "SPLIT", "test")
    test_paths = (evaluate.gen_path(cfg, "sft"), evaluate.metrics_path(cfg), evaluate.judge_dir(cfg))
    monkeypatch.setattr(evaluate, "SPLIT", "val")
    val_paths = (evaluate.gen_path(cfg, "sft"), evaluate.metrics_path(cfg), evaluate.judge_dir(cfg))
    assert test_paths == (tmp_path / "generations" / "sft.jsonl", tmp_path / "metrics.json", tmp_path / "judge")
    assert val_paths == (tmp_path / "generations_val" / "sft.jsonl", tmp_path / "metrics_val.json",
                         tmp_path / "judge_val")


def test_judge_pairs_cli_override():
    cfg = load_config()
    assert evaluate.judge_pairs(cfg, argparse.Namespace(pairs=None)) == [tuple(p) for p in cfg.evaluation.judge_pairs]
    assert evaluate.judge_pairs(cfg, argparse.Namespace(pairs=["orpo_l0.1:sft"])) == [("orpo_l0.1", "sft")]


def test_pairwise_summary_is_position_debiased(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path)
    monkeypatch.setattr(evaluate, "SPLIT", "test")
    rows = []
    # item 1: x wins in both orders; item 2: y wins in both; item 3: the judge follows position -> tie
    for i, (first, second) in enumerate([("A", "B"), ("B", "A"), ("A", "A")], 1):
        rows.append({"a": "x", "b": "y", "id": f"e{i}", "winner": first})
        rows.append({"a": "y", "b": "x", "id": f"e{i}", "winner": second})
    write_jsonl(tmp_path / "judge" / "pairwise.jsonl", rows)
    out = evaluate.summarize_judge(cfg, [("x", "y")])["pairwise"]["x_vs_y"]
    assert out["n"] == 3 and out["win"] == round(1 / 3, 4) and out["loss"] == round(1 / 3, 4)
    assert out["tie"] == round(1 / 3, 4) and out["position_consistency"] == round(2 / 3, 4)
    assert read_json(tmp_path / "metrics.json")["judge"]["pairwise"]["x_vs_y"] == out
