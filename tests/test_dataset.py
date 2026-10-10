import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from prepare_dataset import compose, largest_remainder, split_by_source  # noqa: E402

from clinical_tuner.taxonomy import sample_scenario  # noqa: E402


def _recs(n, source):
    return [{"encounter_id": f"{source}-{i}", "source": source, "note": f"note {i}"} for i in range(n)]


def test_largest_remainder_sums_exactly():
    alloc = largest_remainder(10, {"a": 7, "b": 2, "c": 1})
    assert sum(alloc.values()) == 10 and alloc["a"] == 7


def test_composition_keeps_ratio_and_scales_down():
    recs = _recs(100, "synthetic") + _recs(20, "aci-bench") + _recs(10, "mts-dialog")
    chosen, note = compose(recs, total=100, syn_frac=0.7, rng=random.Random(0))
    n_syn = sum(r["source"] == "synthetic" for r in chosen)
    assert note is None and len(chosen) == 100 and n_syn == 70   # 30 public available -> exact 70/30
    scarce = _recs(100, "synthetic") + _recs(15, "mts-dialog")   # only 15 public -> scale to 35 + 15
    chosen, note = compose(scarce, total=100, syn_frac=0.7, rng=random.Random(0))
    assert note is not None and len(chosen) == 50
    assert sum(r["source"] == "synthetic" for r in chosen) == 35


def test_splits_are_disjoint_and_stratified():
    recs = _recs(70, "synthetic") + _recs(30, "mts-dialog")
    splits = split_by_source(recs, {"test": 10, "val": 5, "pref": 20}, random.Random(0))
    ids = [r["encounter_id"] for s in splits.values() for r in s]
    assert len(ids) == len(set(ids)) == 100
    assert sum(r["source"] == "mts-dialog" for r in splits["test"]) == 3   # 30% of 10


def test_scenarios_are_deterministic_and_consistent():
    a = [sample_scenario(random.Random(7), f"syn-{i}") for i in range(3)]
    b = [sample_scenario(random.Random(7), f"syn-{i}") for i in range(3)]
    assert [x.model_dump() for x in a] == [x.model_dump() for x in b]
    for s in (sample_scenario(random.Random(i), "x") for i in range(300)):
        if s.specialty == "obstetrics and gynecology":
            assert s.sex == "F"
        if s.specialty == "emergency medicine":
            assert s.setting == "emergency department"
        if s.specialty == "pediatrics":
            assert s.age <= 17
