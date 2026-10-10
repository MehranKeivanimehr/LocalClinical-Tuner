import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "data"))
from import_external_notes import _labels, parse_file  # noqa: E402
from quality_filter import generator_models, model_family  # noqa: E402

ROW = {"encounter_id": "syn-00001", "note": "Pt with cough.\nDenies fever.", "summary": "A patient with cough.",
       "soap": {"subjective": "Cough.", "objective": "Not documented.", "assessment": "URI.", "plan": ["Fluids"]},
       "entities": {"symptoms": [{"text": "cough", "negated": False}], "diagnoses": [], "medications": [],
                    "procedures": []}}


def test_parse_file_tolerates_fences_prose_and_cut_off_lines(tmp_path):
    p = tmp_path / "batch_001.jsonl"
    p.write_text("Here you go:\n```jsonl\n" + json.dumps(ROW) + "\n" + '{"encounter_id": "syn-2", "note": "cut of\n```\n')
    objs, bad = parse_file(p)
    assert len(objs) == 1 and bad == 1 and objs[0]["note"].endswith("Denies fever.")


def test_parse_file_accepts_json_array(tmp_path):
    p = tmp_path / "x.json"
    p.write_text(json.dumps([ROW, ROW]))
    assert len(parse_file(p)[0]) == 2


def test_labels_validation_reports_missing_fields():
    ann, missing = _labels(ROW)
    assert missing == [] and ann["soap"]["plan"] == ["Fluids"]
    ann, missing = _labels({"note": "x", "summary": "s", "soap": {"subjective": "only"}})
    assert missing == ["soap", "entities"] and ann == {"summary": "s"}


def test_critic_independence_is_by_model_family():
    assert model_family("claude-opus-5-5 (Claude app)") == model_family("claude-sonnet-5-5") == "claude"
    assert model_family("Qwen/Qwen3-32B") != model_family("Qwen/Qwen2.5-72B-Instruct")
    prov = {"note": "dataset:aci-bench", "summary": "Qwen/Qwen3-32B", "soap": "reference:aci-bench+restructured:x"}
    assert generator_models(prov) == {"Qwen/Qwen3-32B", "x"}
