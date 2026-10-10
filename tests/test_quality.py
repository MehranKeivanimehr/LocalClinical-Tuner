from types import SimpleNamespace

from clinical_tuner.quality.drugs import canonical_drug, parse_dose_mg, parse_frequency_per_day
from clinical_tuner.quality.grounding import ground_entities
from clinical_tuner.quality.phi import english_score, find_phi, scrub_phi
from clinical_tuner.quality.rules import RuleResult, check_labs, check_vitals, extract_demographics, run_rules
from clinical_tuner.schemas import Entities

RULES = SimpleNamespace(max_major=1, min_entities=3, max_ungrounded_entity_frac=0.2, summary_words=(25, 160))


def _record(note, entities, summary=None, plan=None, sex="M", age=50):
    summary = summary or ("The patient is a 50-year-old man seen for chest pain. " * 5).strip()
    return {"source": "synthetic", "style": "narrative_progress_note", "note": note, "patient": {"age": age, "sex": sex},
            "annotation": {"summary": summary,
                           "soap": {"subjective": "Chest pain.", "objective": "BP 130/80.", "assessment": "Angina.",
                                    "plan": plan or ["ECG"]},
                           "entities": entities}}


def test_dose_and_frequency_parsing():
    assert parse_dose_mg("1 g") == 1000 and parse_dose_mg("25 mcg") == 0.025 and parse_dose_mg("2 puffs") is None
    assert parse_frequency_per_day("BID") == 2 and parse_frequency_per_day("q6h prn") == 4
    assert abs(parse_frequency_per_day("twice weekly") - 2 / 7) < 1e-9
    assert canonical_drug("Toprol XL") == "metoprolol" and canonical_drug("Tylenol") == "acetaminophen"


def test_lab_and_vital_ranges_without_dose_false_positives():
    r = RuleResult()
    check_labs("Naproxen sodium 220 mg BID. Potassium Chloride 20 mEq daily. Na 138, K 4.1, Cr 1.0", r)
    check_vitals("BP 128/82, HR 76, RR 16, SpO2 98% on RA, Temp 98.4 F", r)
    assert r.findings == []
    r = RuleResult()
    check_labs("Potassium 14.2", r)
    check_vitals("BP 120/130", r)
    assert {f.check for f in r.findings} == {"lab_range", "vital_range"}


def test_grounding_rewrites_abbreviations_and_drops_paraphrases():
    ent = Entities.model_validate({"diagnoses": ["hypertension", "fever of unknown origin"],
                                   "symptoms": [{"text": "shortness of breath", "negated": False}]})
    clean, total, ungrounded = ground_entities(ent, "Pt w/ HTN. c/o SOB x2d. Febrile to 101.")
    assert clean.diagnoses == ["HTN"] and clean.symptoms[0].text == "SOB"
    assert total == 3 and ungrounded == ["fever of unknown origin"]


def test_rules_flag_dose_allergy_and_sex_conflicts():
    note = ("58-year-old man. Allergies: penicillin (hives). Chest pain for 2 days, denies fever. Taking metformin "
            "5000 mg twice daily. Started amoxicillin 500 mg three times daily. Prenatal vitamins discussed. ECG done.")
    ents = {"symptoms": [{"text": "chest pain", "negated": False}, {"text": "fever", "negated": True}],
            "diagnoses": [], "medications": [{"name": "metformin", "dose": "5000 mg", "frequency": "twice daily"},
                                             {"name": "amoxicillin", "dose": "500 mg", "frequency": "three times daily"}],
            "procedures": ["ECG"]}
    res = run_rules(_record(note, ents), RULES)
    checks = {(f.check, f.severity) for f in res.findings}
    assert ("max_dose", "critical") in checks
    assert ("allergy_conflict", "critical") in checks
    assert ("age_sex", "critical") in checks
    assert res.rejected(RULES.max_major)


def test_clean_record_passes():
    note = ("50-year-old man with chest pain on exertion for 2 weeks; denies fever. BP 132/84, HR 78. "
            "Takes metformin 500 mg twice daily. ECG ordered.")
    ents = {"symptoms": [{"text": "chest pain", "negated": False}, {"text": "fever", "negated": True}],
            "diagnoses": [], "medications": [{"name": "metformin", "dose": "500 mg", "frequency": "twice daily"}],
            "procedures": ["ECG"]}
    res = run_rules(_record(note, ents), RULES)
    assert not res.rejected(RULES.max_major), res.findings


def test_unsupported_medication_in_label_is_critical():
    note = "50-year-old man with chest pain; denies fever. ECG ordered. Takes metformin 500 mg twice daily."
    ents = {"symptoms": [{"text": "chest pain", "negated": False}, {"text": "fever", "negated": True}],
            "diagnoses": [], "medications": [{"name": "metformin", "dose": "500 mg", "frequency": "twice daily"}],
            "procedures": ["ECG"]}
    res = run_rules(_record(note, ents, plan=["ECG", "Start lisinopril 10 mg daily"]), RULES)
    assert any(f.check == "unsupported_medication" and f.severity == "critical" for f in res.findings)


def test_phi_scrub_and_detection():
    text, counts = scrub_phi("Call Dr. Patel at 555-123-4567 on 03/14/2024. next patient is sophia jackson, MRN 12345.")
    assert "555" not in text and "03/14/2024" not in text and "Patel" not in text and "sophia" not in text
    assert counts["phone"] == 1 and counts["calendar_date"] == 1
    assert find_phi("Seen 3 days ago, no identifiers.") == {}
    assert english_score("the patient is doing well and has no pain") > 0.3


def test_demographics_from_words():
    assert extract_demographics("I am seventy six years old and identify as a white female.") == (76, "F")
    assert extract_demographics("A 45-year-old male with cough.") == (45, "M")


def test_spoken_dates_and_surnames_are_scrubbed():
    for text in ("started from June thirty two thousand and four?", "birth date october 31st 1949 .",
                 "on the 3rd of March 2019", "born may fifth nineteen eighty-two"):
        assert "[DATE]" in scrub_phi(text)[0], text
    assert scrub_phi("[NAME] taylor , birth")[0] == "[NAME] , birth"
    for text in ("see you, [NAME] today.", "Started 3 days ago in may, about 2 weeks", "BP 120/80, A1c 7.2"):
        assert scrub_phi(text)[0] == text


def test_numbers_inside_words_are_not_vitals():
    r = RuleResult()
    check_vitals("doctor: and your heart rate your spo2 was fine at ninety eight percent", r)
    check_labs("hba1c was 7.2 and the a1c goal is 7", r)
    assert r.findings == []


def test_allergy_statements_vs_allergic_conditions():
    from clinical_tuner.quality.rules import find_allergies
    assert find_allergies("Impression: allergic rhinitis; may use ibuprofen for headache.") == {}
    assert find_allergies("History of allergic conjunctivitis; uses naproxen.") == {}
    assert find_allergies("No known drug allergies. Taking ibuprofen.") == {}
    assert set(find_allergies("Allergies: penicillin (hives).")) == {"penicillin"}
    assert set(find_allergies("Developed hives with amoxicillin as a child.")) == {"penicillin"}
    assert set(find_allergies("ACE inhibitor intolerance due to cough.")) == {"ace_inhibitor"}


def test_pulse_grades_are_not_heart_rates():
    r = RuleResult()
    check_vitals("Extremities: radial pulse 2+ bilaterally, dorsalis pedis pulses 1+. HR 72.", r)
    assert r.findings == []
    r = RuleResult()
    check_vitals("Pulse 300 on arrival.", r)
    assert [f.check for f in r.findings] == ["vital_range"]
