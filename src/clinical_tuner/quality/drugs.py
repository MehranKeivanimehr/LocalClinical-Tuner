"""Small curated drug knowledge base for rule-based sanity checks.

* ``MAX_DOSES``: conservative adult *upper bounds* (mg) for a single dose and a 24-hour total, taken from usual
  labelled maxima and rounded up generously. They exist to catch impossible/implausible values (e.g. "metformin
  5000 mg BID", "levothyroxine 50 mg"), not to give dosing advice. Drugs dosed in units, mEq, puffs or by weight
  (insulin, heparin, opioids in tolerant patients, IV vancomycin, ...) are deliberately absent.
* ``ALLERGY_CLASSES``: allergy keywords -> drugs that must not be prescribed. ``same_drug_severity`` applies when the
  prescribed drug *is* the allergen; ``class_severity`` when it is another member of the class.

NOT FOR CLINICAL USE - this table is only a data-quality heuristic for synthetic training data.
"""

from __future__ import annotations

import re

from ..text import normalize

# generic name -> (max single dose mg, max daily dose mg)
MAX_DOSES: dict[str, tuple[float, float]] = {
    # analgesics / anti-inflammatories
    "acetaminophen": (1000, 4000), "ibuprofen": (800, 3200), "naproxen": (1000, 1650), "aspirin": (1000, 4000),
    "ketorolac": (60, 120), "meloxicam": (15, 15), "celecoxib": (400, 800), "diclofenac": (100, 200),
    "indomethacin": (100, 200), "tramadol": (100, 400), "colchicine": (1.2, 1.8),
    # diabetes
    "metformin": (2000, 2550), "glipizide": (20, 40), "glimepiride": (8, 8), "glyburide": (20, 20),
    "sitagliptin": (100, 100), "linagliptin": (5, 5), "empagliflozin": (25, 25), "dapagliflozin": (10, 10),
    "canagliflozin": (300, 300), "pioglitazone": (45, 45),
    # cardiovascular
    "lisinopril": (80, 80), "enalapril": (40, 40), "ramipril": (20, 20), "benazepril": (80, 80),
    "losartan": (150, 150), "valsartan": (320, 320), "irbesartan": (300, 300), "olmesartan": (40, 40),
    "amlodipine": (10, 10), "nifedipine": (120, 180), "diltiazem": (540, 540), "verapamil": (480, 480),
    "metoprolol": (400, 450), "carvedilol": (50, 100), "atenolol": (200, 200), "propranolol": (240, 640),
    "bisoprolol": (20, 20), "hydrochlorothiazide": (100, 100), "chlorthalidone": (100, 100),
    "furosemide": (600, 600), "bumetanide": (10, 10), "torsemide": (200, 200), "spironolactone": (400, 400),
    "hydralazine": (100, 300), "clonidine": (1.2, 2.4), "isosorbide mononitrate": (240, 240),
    "atorvastatin": (80, 80), "rosuvastatin": (40, 40), "simvastatin": (80, 80), "pravastatin": (80, 80),
    "ezetimibe": (10, 10), "warfarin": (20, 20), "apixaban": (10, 20), "rivaroxaban": (20, 30),
    "dabigatran": (150, 300), "clopidogrel": (600, 600), "ticagrelor": (180, 180), "digoxin": (0.5, 0.5),
    "amiodarone": (800, 1600),
    # endocrine
    "levothyroxine": (0.5, 0.5), "methimazole": (40, 120), "propylthiouracil": (300, 1200),
    "prednisone": (100, 100), "methylprednisolone": (1000, 1000), "dexamethasone": (40, 40),
    "hydrocortisone": (300, 400), "alendronate": (70, 70),
    # psychiatry / neurology
    "sertraline": (200, 200), "fluoxetine": (80, 80), "escitalopram": (30, 30), "citalopram": (40, 40),
    "paroxetine": (75, 75), "bupropion": (450, 450), "venlafaxine": (375, 375), "duloxetine": (120, 120),
    "mirtazapine": (45, 45), "trazodone": (600, 600), "amitriptyline": (300, 300), "nortriptyline": (150, 150),
    "lithium": (1800, 2400), "quetiapine": (800, 800), "olanzapine": (30, 30), "risperidone": (16, 16),
    "aripiprazole": (30, 30), "haloperidol": (20, 100), "lorazepam": (10, 10), "alprazolam": (10, 10),
    "clonazepam": (20, 20), "diazepam": (40, 40), "zolpidem": (12.5, 12.5), "buspirone": (60, 60),
    "methylphenidate": (72, 72), "amphetamine": (60, 60), "atomoxetine": (100, 100),
    "gabapentin": (1200, 3600), "pregabalin": (300, 600), "topiramate": (200, 400), "sumatriptan": (100, 200),
    "levetiracetam": (4500, 4500), "lamotrigine": (250, 500), "carbamazepine": (800, 1600),
    "donepezil": (23, 23), "memantine": (28, 28), "carbidopa-levodopa": (2000, 2000),
    # GI
    "omeprazole": (120, 360), "pantoprazole": (80, 240), "esomeprazole": (40, 240), "famotidine": (40, 160),
    "ondansetron": (24, 32), "metoclopramide": (20, 60), "mesalamine": (4800, 4800),
    # antimicrobials
    "amoxicillin": (3000, 6000), "amoxicillin-clavulanate": (2000, 4000), "penicillin": (2000, 4000),
    "azithromycin": (2000, 2000), "clarithromycin": (1000, 1000), "doxycycline": (200, 400),
    "minocycline": (200, 400), "ciprofloxacin": (750, 1500), "levofloxacin": (750, 750),
    "moxifloxacin": (400, 400), "cephalexin": (1000, 4000), "cefdinir": (600, 600), "ceftriaxone": (2000, 4000),
    "cefuroxime": (500, 1000), "nitrofurantoin": (100, 400), "metronidazole": (2000, 4000),
    "fluconazole": (800, 800), "valacyclovir": (2000, 4000), "acyclovir": (800, 4000), "oseltamivir": (75, 150),
    "isoniazid": (900, 900), "rifampin": (600, 600),
    # rheumatology / urology / respiratory / misc
    "allopurinol": (800, 800), "febuxostat": (120, 120), "methotrexate": (25, 25),
    "hydroxychloroquine": (400, 600), "tamsulosin": (0.8, 0.8), "finasteride": (5, 5), "oxybutynin": (30, 30),
    "sildenafil": (100, 100), "tadalafil": (40, 40), "montelukast": (10, 10), "cetirizine": (20, 20),
    "loratadine": (10, 20), "fexofenadine": (180, 180), "diphenhydramine": (100, 400),
    "ferrous sulfate": (650, 1300),
}

BRAND_TO_GENERIC: dict[str, str] = {
    "tylenol": "acetaminophen", "paracetamol": "acetaminophen", "apap": "acetaminophen",
    "advil": "ibuprofen", "motrin": "ibuprofen", "aleve": "naproxen", "naprosyn": "naproxen", "asa": "aspirin",
    "toradol": "ketorolac", "mobic": "meloxicam", "celebrex": "celecoxib", "voltaren": "diclofenac",
    "ultram": "tramadol", "colcrys": "colchicine", "glucophage": "metformin", "januvia": "sitagliptin",
    "jardiance": "empagliflozin", "farxiga": "dapagliflozin", "invokana": "canagliflozin",
    "zestril": "lisinopril", "prinivil": "lisinopril", "vasotec": "enalapril", "cozaar": "losartan",
    "diovan": "valsartan", "norvasc": "amlodipine", "lopressor": "metoprolol", "toprol": "metoprolol",
    "toprol xl": "metoprolol", "metoprolol succinate": "metoprolol", "metoprolol tartrate": "metoprolol",
    "coreg": "carvedilol", "tenormin": "atenolol", "hctz": "hydrochlorothiazide", "lasix": "furosemide",
    "bumex": "bumetanide", "aldactone": "spironolactone", "lipitor": "atorvastatin", "crestor": "rosuvastatin",
    "zocor": "simvastatin", "zetia": "ezetimibe", "coumadin": "warfarin", "jantoven": "warfarin",
    "eliquis": "apixaban", "xarelto": "rivaroxaban", "pradaxa": "dabigatran", "plavix": "clopidogrel",
    "brilinta": "ticagrelor", "lanoxin": "digoxin", "synthroid": "levothyroxine", "levoxyl": "levothyroxine",
    "tapazole": "methimazole", "medrol": "methylprednisolone", "decadron": "dexamethasone",
    "fosamax": "alendronate", "zoloft": "sertraline", "prozac": "fluoxetine", "lexapro": "escitalopram",
    "celexa": "citalopram", "paxil": "paroxetine", "wellbutrin": "bupropion", "effexor": "venlafaxine",
    "cymbalta": "duloxetine", "remeron": "mirtazapine", "desyrel": "trazodone", "elavil": "amitriptyline",
    "seroquel": "quetiapine", "zyprexa": "olanzapine", "risperdal": "risperidone", "abilify": "aripiprazole",
    "haldol": "haloperidol", "ativan": "lorazepam", "xanax": "alprazolam", "klonopin": "clonazepam",
    "valium": "diazepam", "ambien": "zolpidem", "buspar": "buspirone", "ritalin": "methylphenidate",
    "concerta": "methylphenidate", "adderall": "amphetamine", "strattera": "atomoxetine",
    "neurontin": "gabapentin", "lyrica": "pregabalin", "topamax": "topiramate", "imitrex": "sumatriptan",
    "keppra": "levetiracetam", "lamictal": "lamotrigine", "tegretol": "carbamazepine", "aricept": "donepezil",
    "namenda": "memantine", "sinemet": "carbidopa-levodopa", "prilosec": "omeprazole",
    "protonix": "pantoprazole", "nexium": "esomeprazole", "pepcid": "famotidine", "zofran": "ondansetron",
    "reglan": "metoclopramide", "augmentin": "amoxicillin-clavulanate", "zithromax": "azithromycin",
    "z-pak": "azithromycin", "biaxin": "clarithromycin", "cipro": "ciprofloxacin", "levaquin": "levofloxacin",
    "keflex": "cephalexin", "omnicef": "cefdinir", "rocephin": "ceftriaxone", "macrobid": "nitrofurantoin",
    "flagyl": "metronidazole", "diflucan": "fluconazole", "valtrex": "valacyclovir", "tamiflu": "oseltamivir",
    "zyloprim": "allopurinol", "uloric": "febuxostat", "plaquenil": "hydroxychloroquine",
    "flomax": "tamsulosin", "proscar": "finasteride", "ditropan": "oxybutynin", "viagra": "sildenafil",
    "cialis": "tadalafil", "singulair": "montelukast", "zyrtec": "cetirizine", "claritin": "loratadine",
    "allegra": "fexofenadine", "benadryl": "diphenhydramine", "bactrim": "trimethoprim-sulfamethoxazole",
    "septra": "trimethoprim-sulfamethoxazole", "tmp-smx": "trimethoprim-sulfamethoxazole",
    "zosyn": "piperacillin-tazobactam", "ancef": "cefazolin", "vibramycin": "doxycycline",
    # common clinical abbreviations
    "hcq": "hydroxychloroquine", "mtx": "methotrexate", "pcn": "penicillin", "amox": "amoxicillin",
    "amox-clav": "amoxicillin-clavulanate", "doxy": "doxycycline", "pred": "prednisone",
    "solu-medrol": "methylprednisolone", "solumedrol": "methylprednisolone", "z-pack": "azithromycin",
    "zpak": "azithromycin", "zpack": "azithromycin", "lopressor xl": "metoprolol", "lisinopril-hctz": "lisinopril",
    "acetaminophen-codeine": "acetaminophen",
}

PENICILLINS = ["penicillin", "amoxicillin", "ampicillin", "amoxicillin-clavulanate", "piperacillin-tazobactam",
               "nafcillin", "oxacillin", "dicloxacillin"]
CEPHALOSPORINS = ["cephalexin", "cefazolin", "ceftriaxone", "cefdinir", "cefuroxime", "cefepime", "cefpodoxime",
                  "ceftazidime"]
NSAIDS = ["ibuprofen", "naproxen", "aspirin", "ketorolac", "meloxicam", "diclofenac", "celecoxib", "indomethacin"]
MACROLIDES = ["azithromycin", "clarithromycin", "erythromycin"]
FLUOROQUINOLONES = ["ciprofloxacin", "levofloxacin", "moxifloxacin"]
TETRACYCLINES = ["doxycycline", "minocycline", "tetracycline"]
ACE_INHIBITORS = ["lisinopril", "enalapril", "ramipril", "benazepril", "captopril", "quinapril"]
STATINS = ["atorvastatin", "rosuvastatin", "simvastatin", "pravastatin", "lovastatin"]
SULFONAMIDES = ["trimethoprim-sulfamethoxazole", "sulfamethoxazole", "sulfasalazine"]
OPIOIDS = ["codeine", "morphine", "hydrocodone", "oxycodone", "hydromorphone", "tramadol"]

# allergy class -> (allergen keywords, drugs in class, severity if same drug, severity if other class member)
ALLERGY_CLASSES: dict[str, tuple[list[str], list[str], str, str]] = {
    "penicillin": (["penicillin", "pcn", *PENICILLINS], PENICILLINS, "critical", "critical"),
    "cephalosporin": (["cephalosporin", *CEPHALOSPORINS], CEPHALOSPORINS, "critical", "major"),
    "sulfa": (["sulfa", "sulfonamide", *SULFONAMIDES], SULFONAMIDES, "critical", "critical"),
    "nsaid": (["nsaid", "nsaids", *NSAIDS], NSAIDS, "critical", "major"),
    "macrolide": (["macrolide", *MACROLIDES], MACROLIDES, "critical", "major"),
    "fluoroquinolone": (["fluoroquinolone", "quinolone", *FLUOROQUINOLONES], FLUOROQUINOLONES, "critical", "major"),
    "tetracycline": (["tetracycline", *TETRACYCLINES], TETRACYCLINES, "critical", "major"),
    "ace_inhibitor": (["ace inhibitor", "ace inhibitors", *ACE_INHIBITORS], ACE_INHIBITORS, "critical", "critical"),
    "statin": (["statin", "statins", *STATINS], STATINS, "critical", "major"),
    "opioid": (["opioid", "opiate", *OPIOIDS], OPIOIDS, "critical", "major"),
}


def canonical_drug(name: str) -> str:
    """Map a medication string to a canonical generic name when known (else its normalized form)."""
    n = normalize(name)
    n = re.sub(r"\b(er|xr|xl|sr|cr|dr|ir|od|hcl|sodium|potassium|tablets?|capsules?|oral|po|iv)\b", " ", n)
    n = re.sub(r"\s+", " ", n).strip()
    if n in BRAND_TO_GENERIC:
        return BRAND_TO_GENERIC[n]
    if n in MAX_DOSES:
        return n
    for token in n.split():  # e.g. "metoprolol succinate 50" -> metoprolol
        if token in MAX_DOSES:
            return token
        if token in BRAND_TO_GENERIC:
            return BRAND_TO_GENERIC[token]
    return n


def drug_aliases(generic: str) -> list[str]:
    """All surface forms (generic + brands) for a canonical generic name."""
    return [generic] + [b for b, g in BRAND_TO_GENERIC.items() if g == generic]


_DOSE_RE = re.compile(r"(\d+(?:,\d{3})*(?:\.\d+)?)\s*(?:-|to)?\s*(\d+(?:\.\d+)?)?\s*(mg|g|gm|grams?|mcg|µg|ug|microgram)",
                      re.IGNORECASE)


def parse_dose_mg(dose: str | None) -> float | None:
    """'500 mg' -> 500; '1 g' -> 1000; '25 mcg' -> 0.025; ranges take the upper bound; units/puffs -> None."""
    if not dose:
        return None
    m = _DOSE_RE.search(dose)
    if not m:
        return None
    value = float((m.group(2) or m.group(1)).replace(",", ""))
    unit = m.group(3).lower()
    if unit in {"g", "gm", "gram", "grams"}:
        return value * 1000
    if unit in {"mcg", "µg", "ug", "microgram"}:
        return value / 1000
    return value


_FREQ_PATTERNS: list[tuple[re.Pattern[str], float]] = [
    (re.compile(r"\bq\s*2\s*h|every 2 hours"), 12),
    (re.compile(r"\bq\s*3\s*h|every 3 hours"), 8),
    (re.compile(r"\bq\s*4\s*h|every 4 hours|six times"), 6),
    (re.compile(r"\bq\s*6\s*h|every 6 hours|\bqid\b|four times|4 times"), 4),
    (re.compile(r"\bq\s*8\s*h|every 8 hours|\btid\b|three times|3 times"), 3),
    (re.compile(r"\bq\s*12\s*h|every 12 hours|\bbid\b|twice|two times|2 times"), 2),
    (re.compile(r"\bweekly\b|once a week|every week|per week|\bq\s*week|\bqwk\b"), 1 / 7),
    (re.compile(r"every other day|\bqod\b"), 0.5),
    (re.compile(r"\bmonthly\b|every month|once a month"), 1 / 30),
    (re.compile(r"\bq\s*24\s*h|every 24 hours|\bdaily\b|\bqd\b|once a day|once daily|every day|every morning|"
                r"\bqam\b|\bqhs\b|\bhs\b|at bedtime|nightly|every night|each day|per day|a day"), 1),
]


_PER_WEEK_RE = re.compile(r"\b(once|twice|two|three|four|five|six|\d+)\s*(?:times?)?\s*(?:a|per|each|every|/)\s*week|"
                          r"\b(once|twice|two|three|four|five|six|\d+)\s*(?:times?)?\s*weekly")
_COUNT = {"once": 1, "twice": 2, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6}


def parse_frequency_per_day(freq: str | None) -> float | None:
    if not freq:
        return None
    f = freq.lower()
    if m := _PER_WEEK_RE.search(f):  # "twice weekly", "3 times per week" -> n/7 (must precede the BID/TID rules)
        word = m.group(1) or m.group(2)
        return (int(word) if word.isdigit() else _COUNT[word]) / 7
    for rx, per_day in _FREQ_PATTERNS:
        if rx.search(f):
            return per_day
    return None
