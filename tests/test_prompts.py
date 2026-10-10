from pathlib import Path

from clinical_tuner.prompts import SYSTEM_PROMPT, TASKS, build_messages, render_entities, render_soap

ROOT = Path(__file__).resolve().parents[1]


def test_build_messages_structure():
    for task in TASKS:
        msgs = build_messages(task, "  Pt c/o cough.  ")
        assert [m["role"] for m in msgs] == ["system", "user"]
        assert msgs[0]["content"] == SYSTEM_PROMPT
        assert msgs[1]["content"].endswith('Clinical note:\n"""\nPt c/o cough.\n"""')


def test_render_soap_canonical_and_renumbers_plan():
    text = render_soap({"subjective": "Cough x3d.", "objective": "", "assessment": "Viral URI.",
                        "plan": ["1. Fluids", "- Rest", "Return if worse"]})
    assert text.split("\n\n")[0] == "SUBJECTIVE:\nCough x3d."
    assert "OBJECTIVE:\nNot documented." in text
    assert text.endswith("PLAN:\n1. Fluids\n2. Rest\n3. Return if worse")


def test_render_entities_key_order():
    out = render_entities({"procedures": ["ECG"], "symptoms": []})
    assert list(__import__("json").loads(out)) == ["symptoms", "diagnoses", "medications", "procedures"]


def test_modelfile_system_prompt_matches_training():
    mf = ROOT / "serving" / "ollama_modelfile"
    if mf.exists():
        assert f'SYSTEM """{SYSTEM_PROMPT}"""' in mf.read_text()


def test_render_soap_strips_duplicated_own_header():
    text = render_soap({"subjective": "Cough.", "objective": "", "assessment": "ASSESSMENT: Viral URI.", "plan": []})
    assert "ASSESSMENT:\nViral URI." in text and text.count("ASSESSMENT:") == 1
