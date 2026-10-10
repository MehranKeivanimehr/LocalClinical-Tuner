"""Scenario taxonomy used to steer synthetic note generation towards realistic diversity.

Every encounter is generated from a sampled (specialty, presenting problem, note style, difficulty, setting,
demographics, complicating factors) tuple. Problems may carry constraints using a tiny suffix syntax:
``"benign prostatic hyperplasia with lower urinary tract symptoms|sex=M|age=50-85"``.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field

from .schemas import Scenario


@dataclass(frozen=True)
class Specialty:
    name: str
    age_range: tuple[int, int]
    problems: tuple[str, ...]
    styles: tuple[str, ...] | None = None  # None = any outpatient style
    sex: str | None = None  # restrict sex for every problem in this specialty
    settings: tuple[str, ...] | None = None  # restrict care settings (intersected with the style's settings)


# Note styles: description is shown to the teacher; settings are compatible care settings.
NOTE_STYLES: dict[str, dict] = {
    "dialogue_transcript": {
        "weight": 0.24,
        "settings": ["outpatient clinic", "urgent care", "specialty clinic"],
        "description": (
            "A verbatim doctor-patient conversation transcript as captured by an ambient scribe. Use speaker "
            "labels 'Doctor:' and 'Patient:' (optionally 'Family member:' or 'Nurse:'). Natural spoken language "
            "with lay terms, hesitations, and some back-and-forth. The clinician states exam findings, the "
            "impression, and the plan out loud near the end."
        ),
    },
    "dictation": {
        "weight": 0.10,
        "settings": ["outpatient clinic", "hospital ward", "specialty clinic"],
        "description": (
            "A raw physician voice-dictation transcript: run-on spoken sentences, occasional self-corrections "
            "(e.g., 'correction, that should be...'), and minimal formatting. Headings may be spoken inline."
        ),
    },
    "narrative_progress_note": {
        "weight": 0.16,
        "settings": ["outpatient clinic", "specialty clinic", "hospital ward"],
        "description": (
            "A free-text narrative progress note written in full sentences and paragraphs, as a clinician "
            "would type it into the EHR, without SOAP headings."
        ),
    },
    "terse_shorthand": {
        "weight": 0.14,
        "settings": ["outpatient clinic", "urgent care", "hospital ward", "emergency department"],
        "description": (
            "A terse clinician note in heavy shorthand: abbreviations (pt, c/o, SOB, DOE, HTN, T2DM, f/u, PRN, "
            "BID, w/, s/p, r/o), sentence fragments, and line-based fragments. No SOAP headings."
        ),
    },
    "ed_provider_note": {
        "weight": 0.12,
        "settings": ["emergency department"],
        "description": (
            "An emergency department provider note with sections such as Chief Complaint, HPI, ROS, Physical "
            "Exam, ED Course / Results, Medical Decision Making, and Disposition."
        ),
    },
    "telehealth_note": {
        "weight": 0.08,
        "settings": ["telehealth video visit", "telephone visit"],
        "description": (
            "A telehealth visit note: patient-reported history, home-measured vitals if any, a limited "
            "visual exam via video (or none for phone), and the plan. Mention the visit modality."
        ),
    },
    "inpatient_progress_note": {
        "weight": 0.08,
        "settings": ["hospital ward", "intensive care unit"],
        "description": (
            "A daily inpatient progress note: hospital day, overnight events, current medications/infusions, "
            "vitals and lab trends, exam, and the plan by problem. Use free-text problem labels, not SOAP headings."
        ),
    },
    "consult_note": {
        "weight": 0.08,
        "settings": ["hospital ward", "specialty clinic", "emergency department"],
        "description": (
            "A specialist consultation note: reason for consult and requesting service, HPI, pertinent history, "
            "exam, data review, impression, and recommendations to the primary team."
        ),
    },
}

OUTPATIENT_STYLES = (
    "dialogue_transcript",
    "dictation",
    "narrative_progress_note",
    "terse_shorthand",
    "telehealth_note",
)

DIFFICULTIES: dict[str, dict] = {
    "easy": {
        "weight": 0.30,
        "target_words": (150, 300),
        "description": (
            "One main problem, at most one chronic comorbidity, at most two medications, clear and explicit "
            "language, and at least two pertinent negatives (symptoms the patient explicitly denies)."
        ),
    },
    "medium": {
        "weight": 0.45,
        "target_words": (280, 500),
        "description": (
            "Two or three active problems, two to five medications with doses, some abbreviations, at least three "
            "pertinent negatives, at least one vital sign set and one test result, and at least one medication "
            "change (start, stop, or dose adjustment)."
        ),
    },
    "hard": {
        "weight": 0.25,
        "target_words": (450, 800),
        "description": (
            "Multiple chronic comorbidities, five or more medications including at least one discontinued and one "
            "dose change, abbreviations, several pertinent negatives, lab values or imaging results, and at least "
            "one piece of information that is corrected or clarified later in the note (e.g., the patient first "
            "gives a wrong dose and later corrects it). The correct final information must be unambiguous."
        ),
    },
}

COMPLICATING_FACTORS: tuple[str, ...] = (
    "a professional interpreter is used for the visit",
    "medication non-adherence because of cost",
    "the patient lives alone and has limited social support",
    "a family caregiver attends and provides part of the history",
    "recent international travel",
    "current tobacco use",
    "heavy alcohol use",
    "limited health literacy",
    "a documented drug allergy relevant to prescribing",
    "the patient is a shift worker with an irregular schedule",
    "recent hospitalization within the past month",
    "the patient uses over-the-counter supplements",
    "pregnancy status is relevant to prescribing",
    "the patient is anxious and asks many questions",
    "transportation barriers make follow-up difficult",
    "the patient takes an anticoagulant",
)

SPECIALTIES: tuple[Specialty, ...] = (
    Specialty("family medicine", (18, 85), (
        "type 2 diabetes mellitus follow-up with rising HbA1c",
        "uncontrolled essential hypertension",
        "acute bacterial sinusitis",
        "mechanical low back pain",
        "hyperlipidemia management and statin discussion",
        "positive depression screening",
        "uncomplicated urinary tract infection|sex=F",
        "seasonal allergic rhinitis",
        "obesity and weight-management counseling",
        "annual preventive visit with vaccine updates",
        "chronic insomnia",
        "new-onset fatigue with suspected anemia",
    )),
    Specialty("internal medicine (hospitalist)", (35, 92), (
        "community-acquired pneumonia",
        "acute decompensated heart failure",
        "lower-extremity cellulitis",
        "acute kidney injury from dehydration",
        "COPD exacerbation",
        "upper gastrointestinal bleed",
        "sepsis from a urinary source",
        "symptomatic hyponatremia",
        "syncope evaluation",
        "diabetic ketoacidosis recovery",
    ), styles=("inpatient_progress_note", "narrative_progress_note", "terse_shorthand", "dictation"),
       settings=("hospital ward", "intensive care unit")),
    Specialty("cardiology", (40, 90), (
        "stable exertional angina",
        "newly diagnosed atrial fibrillation",
        "heart failure with reduced ejection fraction follow-up",
        "follow-up after recent myocardial infarction with stent",
        "palpitations evaluation",
        "resistant hypertension",
        "aortic stenosis murmur surveillance",
        "peripheral artery disease with claudication",
        "pre-operative cardiac risk assessment",
        "statin intolerance with high LDL",
    )),
    Specialty("endocrinology", (18, 80), (
        "type 1 diabetes with frequent hypoglycemia on insulin pump",
        "hypothyroidism needing levothyroxine adjustment",
        "Graves disease hyperthyroidism",
        "thyroid nodule evaluation",
        "postmenopausal osteoporosis|sex=F|age=50-85",
        "polycystic ovary syndrome|sex=F|age=18-40",
        "primary hyperparathyroidism with hypercalcemia",
        "prediabetes and metabolic syndrome",
        "adrenal incidentaloma work-up",
    )),
    Specialty("pulmonology", (25, 85), (
        "poorly controlled persistent asthma",
        "COPD maintenance follow-up",
        "obstructive sleep apnea and CPAP adherence",
        "incidental pulmonary nodule follow-up",
        "interstitial lung disease",
        "chronic cough",
        "pulmonary embolism follow-up on anticoagulation",
        "pulmonary sarcoidosis",
    )),
    Specialty("gastroenterology", (20, 85), (
        "refractory gastroesophageal reflux disease",
        "irritable bowel syndrome with diarrhea",
        "Crohn's disease flare",
        "ulcerative colitis maintenance",
        "elevated liver enzymes with fatty liver disease",
        "iron-deficiency anemia work-up",
        "celiac disease follow-up",
        "chronic pancreatitis with abdominal pain",
        "progressive dysphagia",
        "colon cancer screening discussion",
    )),
    Specialty("neurology", (18, 88), (
        "migraine with aura",
        "first unprovoked seizure",
        "Parkinson's disease follow-up",
        "multiple sclerosis relapse",
        "diabetic peripheral neuropathy",
        "transient ischemic attack follow-up",
        "essential tremor",
        "post-concussion syndrome",
        "memory complaints and cognitive evaluation|age=60-90",
    )),
    Specialty("psychiatry", (18, 75), (
        "major depressive disorder medication management",
        "generalized anxiety disorder",
        "bipolar I disorder on lithium",
        "adult ADHD evaluation",
        "post-traumatic stress disorder",
        "alcohol use disorder",
        "panic disorder",
        "schizophrenia stable on long-acting injectable",
    ), styles=("dialogue_transcript", "narrative_progress_note", "dictation", "telehealth_note", "terse_shorthand")),
    Specialty("pediatrics", (0, 17), (
        "well-child visit at 12 months|age=1-1",
        "acute otitis media|age=1-6",
        "pediatric asthma exacerbation|age=4-16",
        "ADHD medication follow-up|age=6-17",
        "fever in a young infant|age=0-0",
        "croup|age=1-5",
        "atopic dermatitis|age=0-12",
        "functional constipation|age=2-12",
        "bronchiolitis|age=0-1",
        "streptococcal pharyngitis|age=4-15",
    )),
    Specialty("obstetrics and gynecology", (16, 60), (
        "routine prenatal visit at 28 weeks|age=18-42",
        "postpartum follow-up|age=18-42",
        "abnormal uterine bleeding",
        "contraception counseling|age=16-45",
        "menopausal vasomotor symptoms|age=45-60",
        "evaluation for preeclampsia|age=18-42",
        "recurrent vaginitis",
        "chronic pelvic pain with suspected endometriosis|age=18-45",
    ), sex="F"),
    Specialty("orthopedics", (16, 88), (
        "knee osteoarthritis",
        "rotator cuff tear",
        "lateral ankle sprain",
        "post-operative follow-up after total hip arthroplasty|age=55-88",
        "carpal tunnel syndrome",
        "lumbar radiculopathy",
        "distal radius fracture follow-up",
        "anterior cruciate ligament tear|age=16-40",
    )),
    Specialty("dermatology", (12, 85), (
        "moderate plaque psoriasis on a biologic",
        "acne vulgaris|age=12-30",
        "changing pigmented lesion",
        "adult atopic dermatitis",
        "rosacea",
        "hidradenitis suppurativa",
        "actinic keratoses|age=50-85",
    )),
    Specialty("emergency medicine", (18, 90), (
        "chest pain to rule out acute coronary syndrome",
        "right lower quadrant abdominal pain",
        "sudden severe headache",
        "acute asthma attack",
        "forearm laceration requiring repair",
        "renal colic from a kidney stone",
        "fall in an elderly patient on anticoagulation|age=70-95",
        "allergic reaction with angioedema",
        "hyperglycemia with suspected DKA",
    ), styles=("ed_provider_note", "dialogue_transcript", "terse_shorthand", "dictation"),
       settings=("emergency department",)),
    Specialty("nephrology", (30, 88), (
        "chronic kidney disease stage 3b",
        "hyperkalemia on an ACE inhibitor",
        "nephrotic syndrome",
        "hemodialysis patient with fluid overload",
        "kidney transplant follow-up",
        "recurrent calcium oxalate kidney stones",
    ), styles=OUTPATIENT_STYLES + ("consult_note",)),
    Specialty("oncology and hematology", (25, 85), (
        "breast cancer on adjuvant endocrine therapy|sex=F",
        "chemotherapy-induced neutropenia",
        "newly diagnosed diffuse large B-cell lymphoma",
        "prostate cancer on active surveillance|sex=M|age=50-85",
        "deep vein thrombosis on anticoagulation",
        "sickle cell disease pain crisis|age=18-50",
        "lung cancer on immunotherapy",
    ), styles=OUTPATIENT_STYLES + ("consult_note", "inpatient_progress_note")),
    Specialty("infectious disease", (18, 85), (
        "HIV routine follow-up on antiretroviral therapy",
        "osteomyelitis on IV antibiotics",
        "recurrent Clostridioides difficile infection",
        "latent tuberculosis infection",
        "fever after international travel",
        "early Lyme disease",
        "herpes zoster",
    ), styles=OUTPATIENT_STYLES + ("consult_note",)),
    Specialty("rheumatology", (20, 85), (
        "rheumatoid arthritis on methotrexate",
        "acute gout flare",
        "systemic lupus erythematosus follow-up",
        "polymyalgia rheumatica|age=55-85",
        "psoriatic arthritis",
        "fibromyalgia",
        "ankylosing spondylitis|age=18-50",
    )),
    Specialty("urology", (20, 88), (
        "benign prostatic hyperplasia with lower urinary tract symptoms|sex=M|age=50-88",
        "kidney stone follow-up",
        "erectile dysfunction|sex=M|age=35-80",
        "recurrent urinary tract infections|sex=F",
        "elevated PSA|sex=M|age=50-80",
        "overactive bladder",
    )),
    Specialty("geriatrics", (65, 97), (
        "recurrent falls and polypharmacy review",
        "mild cognitive impairment",
        "urinary incontinence",
        "frailty and unintentional weight loss",
        "advance care planning with multiple chronic conditions",
        "osteoporosis after a fragility fracture",
    )),
    Specialty("otolaryngology and ophthalmology", (5, 88), (
        "chronic rhinosinusitis",
        "gradual hearing loss|age=40-88",
        "benign paroxysmal positional vertigo|age=30-88",
        "recurrent tonsillitis|age=5-30",
        "primary open-angle glaucoma follow-up|age=45-88",
        "acute conjunctivitis",
        "diabetic retinopathy screening|age=30-80",
    )),
)


@dataclass
class _Problem:
    text: str
    sex: str | None = None
    age_range: tuple[int, int] | None = None
    extra: dict = field(default_factory=dict)


def parse_problem(spec: str) -> _Problem:
    parts = spec.split("|")
    problem = _Problem(text=parts[0].strip())
    for part in parts[1:]:
        key, _, value = part.partition("=")
        if key == "sex":
            problem.sex = value
        elif key == "age":
            lo, _, hi = value.partition("-")
            problem.age_range = (int(lo), int(hi))
        else:
            problem.extra[key] = value
    return problem


def _weighted_choice(rng: random.Random, options: dict[str, dict], allowed: tuple[str, ...] | None = None) -> str:
    keys = [k for k in options if allowed is None or k in allowed]
    return rng.choices(keys, weights=[options[k]["weight"] for k in keys], k=1)[0]


def sample_scenario(rng: random.Random, encounter_id: str) -> Scenario:
    """Sample one diverse, internally consistent scenario."""
    spec = rng.choice(SPECIALTIES)
    problem = parse_problem(rng.choice(spec.problems))
    style = _weighted_choice(rng, NOTE_STYLES, spec.styles or OUTPATIENT_STYLES)
    difficulty = _weighted_choice(rng, DIFFICULTIES)
    style_settings = NOTE_STYLES[style]["settings"]
    if spec.settings:
        style_settings = [s for s in style_settings if s in spec.settings] or list(spec.settings)
    setting = rng.choice(style_settings)

    lo, hi = problem.age_range or spec.age_range
    age = rng.randint(lo, hi)
    sex = problem.sex or spec.sex or rng.choice(["F", "M"])

    n_factors = {"easy": rng.choice([0, 0, 1]), "medium": rng.choice([0, 1, 1]), "hard": rng.choice([1, 2])}[difficulty]
    factors = rng.sample(COMPLICATING_FACTORS, k=n_factors)
    if age < 12:  # keep factors plausible for young children
        factors = [f for f in factors if f in {
            "a professional interpreter is used for the visit",
            "a family caregiver attends and provides part of the history",
            "recent international travel",
            "transportation barriers make follow-up difficult",
            "a documented drug allergy relevant to prescribing",
        }]
    if sex == "M" or age < 13 or age > 55:
        factors = [f for f in factors if f != "pregnancy status is relevant to prescribing"]

    return Scenario(
        encounter_id=encounter_id,
        specialty=spec.name,
        problem=problem.text,
        style=style,
        difficulty=difficulty,
        setting=setting,
        age=age,
        sex=sex,
        complicating_factors=factors,
        target_words=DIFFICULTIES[difficulty]["target_words"],
    )


def all_problems() -> list[tuple[str, str]]:
    return [(s.name, parse_problem(p).text) for s in SPECIALTIES for p in s.problems]
