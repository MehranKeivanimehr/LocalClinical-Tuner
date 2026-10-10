from clinical_tuner.metrics import (
    entity_counts,
    micro_f1,
    parse_entities_output,
    reward,
    soap_structure,
    unsupported_medications,
)
from clinical_tuner.prompts import render_entities, render_soap
from clinical_tuner.schemas import Entities

GOLD = Entities.model_validate({
    "symptoms": [{"text": "chest pain", "negated": False}, {"text": "fever", "negated": True}],
    "diagnoses": ["HTN"], "medications": [{"name": "metformin", "dose": "500 mg", "frequency": "BID"}],
    "procedures": ["ECG"]})


def test_lenient_entity_parsing():
    e = parse_entities_output('Here:\n```json\n{"Symptoms": ["cough"], "medications": ["Tylenol"]}\n```')
    assert e is not None and e.symptoms[0].text == "cough" and e.medications[0].name == "Tylenol"
    assert parse_entities_output("not json at all") is None


def test_entity_f1_perfect_and_relaxed_matching():
    perfect = entity_counts(GOLD, GOLD)
    assert micro_f1([perfect]) == 1.0 and perfect["negation"] == (2, 2)
    pred = Entities.model_validate({"symptoms": [{"text": "chest pain", "negated": False}],
                                    "diagnoses": ["hypertension"], "medications": [{"name": "Glucophage"}],
                                    "procedures": ["ECG", "CBC"]})
    c = entity_counts(pred, GOLD)
    assert c["relaxed:diagnoses"] == (1, 0, 0)        # HTN ~ hypertension
    assert c["relaxed:medications"] == (1, 0, 0)      # Glucophage ~ metformin
    assert c["strict:medications"] == (0, 1, 1)
    assert c["relaxed:procedures"] == (1, 1, 0)


def test_soap_structure():
    assert soap_structure(render_soap({"subjective": "a", "objective": "b", "assessment": "c", "plan": ["d"]})) == 1.0
    assert soap_structure("SUBJECTIVE:\na\nPLAN:\nb") == 0.5
    assert soap_structure("free text") == 0.0


def test_unsupported_medications_alias_aware():
    note = "Takes ASA 81 mg daily and Lipitor."
    assert unsupported_medications("Continue aspirin and atorvastatin.", note) == []
    assert unsupported_medications("Start lisinopril 10 mg.", note) == ["lisinopril"]


def test_reward_gold_is_high_and_hallucination_penalized():
    ex = {"gold_entities": GOLD.model_dump(), "note": "chest pain, no fever. HTN. metformin 500 mg BID. ECG.",
          "reference": "A patient with HTN on metformin had chest pain without fever; ECG was done."}
    assert reward("entities", render_entities(GOLD.model_dump()), ex) == 1.0
    assert reward("summary", ex["reference"], ex) > 0.9
    assert reward("summary", ex["reference"] + " Started warfarin.", ex) < 0.5


def test_clinical_drug_abbreviations_are_aliases():
    assert unsupported_medications("Continue hydroxychloroquine 200 mg twice daily.", "On HCQ 200 BID for RA.") == []
    assert unsupported_medications("Weekly methotrexate.", "MTX 15 mg weekly") == []
