"""Rule-based clinical sanity checks applied to every encounter (synthetic and Hugging Face) after generation.

Each check emits ``Finding``s tagged ``critical`` (always reject), ``major`` (reject when more than
``rules.max_major`` occur) or ``minor`` (logged only). Checks are deliberately high-precision: they look for values
and contradictions that are clearly wrong, and leave subtle judgement calls to the LLM critic.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any

from ..prompts import render_soap
from ..schemas import Entities
from ..text import normalize, sentences, word_count
from .drugs import (
    ALLERGY_CLASSES,
    BRAND_TO_GENERIC,
    MAX_DOSES,
    canonical_drug,
    drug_aliases,
    parse_dose_mg,
    parse_frequency_per_day,
)
from .grounding import ground_entities

SEVERITIES = ("critical", "major", "minor")


@dataclass
class Finding:
    check: str
    severity: str
    field: str
    message: str


@dataclass
class RuleResult:
    findings: list[Finding] = field(default_factory=list)
    entities: Entities | None = None  # grounded/deduplicated entities
    stats: dict[str, Any] = field(default_factory=dict)

    def add(self, check: str, severity: str, fld: str, message: str) -> None:
        self.findings.append(Finding(check, severity, fld, message))

    def counts(self) -> dict[str, int]:
        return {s: sum(f.severity == s for f in self.findings) for s in SEVERITIES}

    def rejected(self, max_major: int) -> bool:
        c = self.counts()
        return c["critical"] > 0 or c["major"] > max_major

    def to_dict(self) -> dict[str, Any]:
        return {"findings": [asdict(f) for f in self.findings], "counts": self.counts(), "stats": self.stats}


# --------------------------------------------------------------------------------------------------------------
# Demographics
# --------------------------------------------------------------------------------------------------------------
_NUM_WORDS = {w: i for i, w in enumerate(
    """
zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen
seventeen eighteen nineteen
""".split())}
_TENS = {"twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80,
         "ninety": 90}
_SEX_WORDS = {"male": "M", "man": "M", "boy": "M", "gentleman": "M", "female": "F", "woman": "F", "girl": "F",
              "lady": "F"}
_AGE_RE = re.compile(
    r"\b(\d{1,3}|(?:twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety)(?:[- ](?:one|two|three|four|five|six|"
    r"seven|eight|nine))?|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|thirteen|fourteen|fifteen|"
    r"sixteen|seventeen|eighteen|nineteen)[- ](?:year|yr)s?[- ]old\b(?:\s+(?:\w+\s+){0,2}?"
    r"(male|female|man|woman|boy|girl|gentleman|lady)\b)?", re.IGNORECASE)
_MONTH_AGE_RE = re.compile(r"\b(\d{1,2})[- ](?:month|mo)s?[- ]old\b", re.IGNORECASE)


def _words_to_int(s: str) -> int | None:
    s = s.lower().replace("-", " ")
    if s.isdigit():
        return int(s)
    parts = s.split()
    total = 0
    for p in parts:
        if p in _TENS:
            total += _TENS[p]
        elif p in _NUM_WORDS:
            total += _NUM_WORDS[p]
        else:
            return None
    return total


def stated_ages(text: str) -> list[int]:
    ages = [a for m in _AGE_RE.finditer(text) if (a := _words_to_int(m.group(1))) is not None]
    if _MONTH_AGE_RE.search(text):
        ages.append(0)
    return ages


def extract_demographics(text: str) -> tuple[int | None, str | None]:
    """Best-effort (age, sex) of the patient from phrases like '58-year-old female' / 'seventy six years old'."""
    for m in _AGE_RE.finditer(text):
        age = _words_to_int(m.group(1))
        sex = _SEX_WORDS.get((m.group(2) or "").lower())
        if age is not None:
            if sex is None:
                window = text[m.end(): m.end() + 60].lower()
                sm = re.search(r"\b(male|female|man|woman|boy|girl|gentleman|lady)\b", window)
                sex = _SEX_WORDS.get(sm.group(1)) if sm else None
            return age, sex
    if _MONTH_AGE_RE.search(text):
        return 0, None
    return None, None


# --------------------------------------------------------------------------------------------------------------
# Vitals and labs
# --------------------------------------------------------------------------------------------------------------
_N = r"(?<![\w.])(\d{1,4}(?:\.\d+)?)"  # a number that does not start inside a word ("spo2")
# A number followed by a dose unit (mg, mcg, g, mEq, units, %, mL) is a drug dose, not a lab/vital value
# ("naproxen sodium 220 mg", "potassium chloride 20 mEq", "Paxil CR 25 mg"); "mg/dL" and "mEq/L" stay allowed.
_NOT_DOSE = r"(?!(?i:\s*(?:mcg|mg|g|grams?|units?|%|meq|ml|tabs?|tablets?)(?![/\w])))"
_NOT_SALT = r"(?!(?i:\s*(?:chloride|citrate|bicarbonate|phosphate|sulfate|supplement|sparing|polystyrene)))"
_VITAL_PATTERNS: dict[str, list[re.Pattern[str]]] = {
    "bp": [re.compile(r"\b(?:bp|blood pressure)\b[^0-9\n]{0,20}(\d{2,3})\s*/\s*(\d{2,3})", re.I),
           re.compile(r"\b(\d{2,3})\s*/\s*(\d{2,3})\s*mm\s*hg", re.I)],
    # Uppercase-only HR/RR: lowercase "hr" is usually hours ("24 hr urine").
    # Heart rates have 2-3 digits; "radial pulse 2+" is a pulse-strength grade, not a rate.
    "hr": [re.compile(r"(?:\bHR\b|(?i:\bheart rate\b|\bpulse rate\b|\bpulse\b(?!\s*ox)))[^0-9\n%]{0,12}"
                      r"(?<![\w.])(\d{2,3})(?!\s*[%+])" + _NOT_DOSE),
           re.compile(_N + r"\s*(?:bpm|beats per min)", re.I)],
    "rr": [re.compile(r"(?:\bRR\b|(?i:\bresp(?:iratory)? rate\b|\brespirations\b))[^0-9\n]{0,12}" + _N + _NOT_DOSE),
           re.compile(_N + r"\s*breaths? (?:per|/)\s*min", re.I)],
    "spo2": [re.compile(r"\b(?:spo2|o2 sat\w*|oxygen sat\w*|sats?|saturation|pulse ox\w*)\b[^0-9\n]{0,15}"
                        r"(\d{2,3})\s*%", re.I),
             re.compile(r"\b(\d{2,3})\s*%\s*(?:on|in)\s*(?:room air|ra\b|\d+\s*l)", re.I)],
    "temp": [re.compile(r"\b(?:temp(?:erature)?|tmax|febrile (?:to|at))\b[^0-9\n]{0,10}(\d{2,3}(?:\.\d+)?)"
                        r"\s*°?\s*(?:degrees?\s*)?([fc](?![a-z]))?", re.I),
             re.compile(r"\b(\d{2,3}(?:\.\d+)?)\s*°\s*([fc])\b", re.I)],
}
_VITAL_BOUNDS = {"hr": (20, 250), "rr": (4, 70), "spo2": (50, 100), "sbp": (50, 280), "dbp": (20, 180),
                 "temp_f": (85, 112), "temp_c": (29.5, 44.5)}


def _lab(label: str, a1c: bool = False) -> re.Pattern[str]:
    unit_guard = "" if a1c else _NOT_DOSE
    return re.compile(r"\b(?:" + label + r")\b" + _NOT_SALT + r"[^0-9\n]{0,15}" + _N + r"\b" + unit_guard, re.I)


# name -> (regex, primary range, alternative-unit range or None)
_LAB_RULES: dict[str, tuple[re.Pattern[str], tuple[float, float], tuple[float, float] | None]] = {
    "sodium": (_lab(r"sodium|na"), (95, 190), None),
    "potassium": (re.compile(r"(?:(?i:\bpotassium\b)" + _NOT_SALT + r"[^0-9\n]{0,15}|(?<!vitamin )\bK\+?\s*(?:of|is|was|:|=)?\s*)"
                             r"(\d{1,2}(?:\.\d+)?)\b" + _NOT_DOSE), (1.0, 10.0), None),
    "glucose": (_lab(r"glucose|blood sugars?|fingerstick|fsbg"), (10, 2000), (0.5, 110)),
    "hba1c": (_lab(r"hba1c|a1c", a1c=True), (3, 20), (10, 200)),
    "creatinine": (_lab(r"creatinine(?!\s*(?:clearance|cl\b))|Cr(?!\s*(?:clearance|cl\b))"), (0.1, 25), (10, 2500)),
    "hemoglobin": (_lab(r"hemoglobin(?!\s*a1c)|hgb|hb(?!\s*a1c)"), (2, 25), (20, 250)),
    "wbc": (_lab(r"wbc|white (?:blood )?(?:cell )?count"), (0.1, 300), (100, 300000)),
    "platelets": (_lab(r"platelets?|plt"), (1, 3000), (1000, 3000000)),
    "inr": (_lab(r"inr"), (0.5, 20), None),
    "tsh": (_lab(r"tsh"), (0, 500), None),
    "ldl": (_lab(r"ldl"), (5, 1000), (0.1, 25)),
    "egfr": (_lab(r"egfr"), (1, 200), None),
}


def _in(v: float, rng: tuple[float, float] | None) -> bool:
    return rng is not None and rng[0] <= v <= rng[1]


def check_vitals(note: str, res: RuleResult) -> None:
    for m in (m for rx in _VITAL_PATTERNS["bp"] for m in rx.finditer(note)):
        sbp, dbp = float(m.group(1)), float(m.group(2))
        if not _in(sbp, _VITAL_BOUNDS["sbp"]) or not _in(dbp, _VITAL_BOUNDS["dbp"]) or sbp <= dbp:
            res.add("vital_range", "critical", "note", f"implausible blood pressure {m.group(0).strip()!r}")
    for key in ("hr", "rr", "spo2"):
        for rx in _VITAL_PATTERNS[key]:
            for m in rx.finditer(note):
                v = float(m.group(1))
                if not _in(v, _VITAL_BOUNDS[key]):
                    res.add("vital_range", "critical", "note", f"implausible {key} {m.group(0).strip()!r}")
    for rx in _VITAL_PATTERNS["temp"]:
        for m in rx.finditer(note):
            v = float(m.group(1))
            unit = (m.group(2) or ("f" if v > 50 else "c")).lower()
            if not _in(v, _VITAL_BOUNDS["temp_f" if unit == "f" else "temp_c"]):
                res.add("vital_range", "critical", "note", f"implausible temperature {m.group(0).strip()!r}")


def check_labs(note: str, res: RuleResult) -> None:
    for name, (rx, rng, alt) in _LAB_RULES.items():
        for m in rx.finditer(note):
            v = float(m.group(1).replace(",", ""))
            if not (_in(v, rng) or _in(v, alt)):
                res.add("lab_range", "critical", "note", f"implausible {name} value {m.group(0).strip()!r}")


# --------------------------------------------------------------------------------------------------------------
# Medications: doses, allergies, unsupported mentions
# --------------------------------------------------------------------------------------------------------------
def check_doses(ent: Entities, res: RuleResult) -> None:
    for med in ent.medications:
        generic = canonical_drug(med.name)
        dose = parse_dose_mg(med.dose)
        freq = parse_frequency_per_day(med.frequency)
        if generic == "methotrexate" and freq is not None and freq >= 1 and (dose or 0) >= 5:
            res.add("max_dose", "critical", "entities",
                    f"methotrexate {med.dose} {med.frequency}: oral low-dose methotrexate is weekly, not daily")
        if generic not in MAX_DOSES or dose is None:
            continue
        max_single, max_daily = MAX_DOSES[generic]
        if dose > max_single * 1.001:
            res.add("max_dose", "critical", "entities",
                    f"{med.name} single dose {med.dose} exceeds max {max_single:g} mg")
        elif freq is not None and dose * freq > max_daily * 1.001:
            res.add("max_dose", "critical", "entities",
                    f"{med.name} {med.dose} {med.frequency} = {dose * freq:g} mg/day exceeds max {max_daily:g} mg/day")


_NKDA_RE = re.compile(r"\b(nkda|no known (?:drug )?allergies|no (?:drug )?allergies|denies (?:any )?allergies)\b",
                      re.I)
_AVOIDANCE_RE = re.compile(r"allerg|avoid|instead|rather than|reaction|rash|hives|anaphyla|intoleran|caused|swelling|"
                           r"angioedema|not tolerate|stopped|discontinu|switch", re.I)


# Spans that actually state a drug allergy/intolerance ("allergic to X", "allergies: X", "X allergy", "hives with X").
# Conditions such as "allergic rhinitis" or "allergic conjunctivitis" are NOT allergy statements.
_ALLERGY_SPAN_RES = [
    re.compile(r"\ballerg(?:y|ies)\s*(?::|-|=|\bto\b|\binclude[sd]?\b|\bincluding\b|\bare\b|\bis\b|\blisted as\b)"
               r"\s*(.{0,100})", re.I),
    re.compile(r"\ballergic\s+(?:reaction\s+)?to\s+(.{0,100})", re.I),
    re.compile(r"\b(?:reaction|hives|rash|anaphylaxis|angioedema|swelling)\s+(?:to|from|with|after)\s+(.{0,60})", re.I),
    re.compile(r"\b(?:intolerant|intolerance)\s+(?:of|to)\s+(.{0,60})", re.I),
    re.compile(r"((?:[a-z][a-z-]+\s+){0,2}[a-z][a-z-]+)\s+(?:allergy|allergies|intolerance)\b", re.I),
]


def find_allergies(note: str) -> dict[str, set[str]]:
    """allergy class -> allergen keywords that appear inside an explicit allergy/intolerance statement."""
    found: dict[str, set[str]] = {}
    for sent in sentences(note):
        if not re.search(r"allerg|intoleran|reaction|hives|anaphyla|angioedema", sent, re.I):
            continue
        spans = [m.group(1) for rx in _ALLERGY_SPAN_RES for m in rx.finditer(sent)]
        for span in spans:
            if _NKDA_RE.search(span) or re.match(r"\s*(?:none|no known|nkda|denied|n/a)\b", span, re.I):
                continue
            norm = normalize(span)
            for cls, (keywords, *_rest) in ALLERGY_CLASSES.items():
                for kw in keywords:
                    if re.search(rf"(?<!\w){re.escape(kw)}(?!\w)", norm):
                        found.setdefault(cls, set()).add(kw)
    return found


def _prescribed_in_note(note: str, aliases: list[str]) -> bool:
    """True if any mention of the drug is outside an avoidance/allergy context (i.e. it is actually given)."""
    for sent in sentences(note):
        norm = normalize(sent)
        if any(re.search(rf"(?<!\w){re.escape(normalize(a))}(?!\w)", norm) for a in aliases) and \
                not _AVOIDANCE_RE.search(sent):
            return True
    return False


def check_allergy_conflicts(note: str, ent: Entities, res: RuleResult) -> None:
    allergies = find_allergies(note)
    if not allergies:
        return
    for med in ent.medications:
        generic = canonical_drug(med.name)
        for cls, keywords in allergies.items():
            _kw, members, same_sev, class_sev = ALLERGY_CLASSES[cls]
            if generic not in members:
                continue
            if not _prescribed_in_note(note, drug_aliases(generic) + [med.name]):
                continue
            same = generic in {canonical_drug(k) for k in keywords}
            res.add("allergy_conflict", same_sev if same else class_sev, "entities",
                    f"{med.name} given despite documented {cls} allergy ({', '.join(sorted(keywords))})")


_CURATED_DRUGS = sorted(set(MAX_DOSES) | set(BRAND_TO_GENERIC), key=len, reverse=True)
_CURATED_RE = re.compile(r"(?<!\w)(" + "|".join(re.escape(d) for d in _CURATED_DRUGS) + r")(?!\w)")


def check_unsupported_medications(note: str, summary: str, soap_text: str, res: RuleResult) -> None:
    """A medication named in a gold label but absent (under any alias) from the note is a hallucination."""
    note_norm = normalize(note)
    for fld, text in (("summary", summary), ("soap", soap_text)):
        for m in set(_CURATED_RE.findall(normalize(text))):
            generic = canonical_drug(m)
            aliases = drug_aliases(generic) if generic in MAX_DOSES else [m]
            if not any(re.search(rf"(?<!\w){re.escape(normalize(a))}(?!\w)", note_norm) for a in aliases):
                res.add("unsupported_medication", "critical", fld, f"{m!r} is not mentioned in the note")


# --------------------------------------------------------------------------------------------------------------
# Age / sex consistency
# --------------------------------------------------------------------------------------------------------------
_FEMALE_TERMS = re.compile(r"\b(pregnan\w*|prenatal|antenatal|postpartum|menstrua\w*|menses|uterus|uterine|"
                           r"ovar(?:y|ies|ian)|cervix|endometri\w*|mammogra\w*|vagina\w*|breastfeeding|lactating|"
                           r"lactation|menopaus\w*|hysterectom\w*|pap smear|gravida|trimester|preeclampsia)\b", re.I)
_MALE_TERMS = re.compile(r"\b(prostat\w*|psa|testic\w*|testes|scrot\w*|erectile|vasectom\w*|penile|penis|bph|"
                         r"orchi\w*)\b", re.I)
_OTHER_PERSON = re.compile(r"\b(mother|father|mom|dad|sister|brother|sibling|wife|husband|partner|spouse|daughter|"
                           r"son|aunt|uncle|grand\w*|family|fh|cousin|niece|nephew|girlfriend|boyfriend|friend)\b",
                           re.I)
_ACTIVE_PREGNANCY = re.compile(r"\b(\d+\s*weeks? pregnant|currently pregnant|is pregnant|prenatal visit|"
                               r"(?:first|second|third) trimester|postpartum)\b", re.I)


def check_age_sex(texts: dict[str, str], age: int | None, sex: str | None, res: RuleResult) -> None:
    for fld, text in texts.items():
        for sent in sentences(text):
            if _OTHER_PERSON.search(sent):
                continue
            if sex == "M" and age is not None and age >= 13 and (m := _FEMALE_TERMS.search(sent)):
                res.add("age_sex", "critical", fld, f"female-specific term {m.group(0)!r} for a male patient")
            if sex == "F" and (m := _MALE_TERMS.search(sent)):
                res.add("age_sex", "critical", fld, f"male-specific term {m.group(0)!r} for a female patient")
            if age is not None and (age < 11 or age > 55) and (m := _ACTIVE_PREGNANCY.search(sent)):
                res.add("age_sex", "critical", fld, f"active pregnancy {m.group(0)!r} at age {age}")
            if age is not None and age >= 19 and re.search(r"\bwell[- ]child\b", sent, re.I):
                res.add("age_sex", "major", fld, f"well-child visit for a {age}-year-old")
            if age is not None and age < 30 and re.search(r"\bmenopaus", sent, re.I) and \
                    not re.search(r"premature|early|primary ovarian", sent, re.I):
                res.add("age_sex", "major", fld, f"menopause at age {age}")


# --------------------------------------------------------------------------------------------------------------
# Structure, grounding, negation
# --------------------------------------------------------------------------------------------------------------
_NOT_DOC = {"not documented", "not documented.", "none", "n/a", ""}
_NEG_CUES = re.compile(r"\b(denies|denied|deny|no|not|without|negative for|absence of|free of|nor)\b", re.I)
_POS_BREAK = re.compile(r"\b(but|however|reports|endorses|complains|admits|positive for|notes|has had)\b", re.I)


def _is_negated_in(sentence: str, term: str) -> bool | None:
    """NegEx-lite: True if the term appears after a negation cue in the same sentence with no positive break."""
    low, t = sentence.lower(), term.lower()
    pos = low.find(t)
    if pos < 0:
        return None
    prefix = low[:pos]
    cue = None
    for m in _NEG_CUES.finditer(prefix):
        cue = m
    if cue is None:
        return False
    return not _POS_BREAK.search(prefix[cue.end():])


def check_negation(note: str, ent: Entities, res: RuleResult) -> None:
    """Only for prose notes (dialogue negation spans turns, e.g. 'Any fever?' / 'No.')."""
    sents = sentences(note)
    for sx in ent.symptoms:
        states = [s for s in (_is_negated_in(sent, sx.text) for sent in sents) if s is not None]
        if not states:
            continue
        if not sx.negated and all(states):
            res.add("negation", "major", "entities", f"symptom {sx.text!r} marked present but only negated in note")
        if sx.negated and not any(states):
            res.add("negation", "major", "entities", f"symptom {sx.text!r} marked negated but never negated in note")


def check_soap(soap: dict[str, Any], full_encounter: bool, res: RuleResult) -> None:
    def empty(v: Any) -> bool:
        if isinstance(v, list):
            return not [x for x in v if normalize(str(x)) not in _NOT_DOC]
        return normalize(str(v or "")) in _NOT_DOC

    if empty(soap.get("subjective")):
        res.add("soap_completeness", "critical", "soap", "SUBJECTIVE is empty")
    if full_encounter:
        for sec in ("assessment", "plan"):
            if empty(soap.get(sec)):
                # Synthetic notes always state an impression and plan (by construction), so an empty section in the
                # label is a definite omission.
                res.add("soap_completeness", "critical", "soap", f"{sec.upper()} not documented for a full encounter")
    for sec in ("subjective", "objective", "assessment"):
        others = "|".join(h for h in ("SUBJECTIVE", "OBJECTIVE", "ASSESSMENT", "PLAN") if h != sec.upper())
        if re.search(rf"^\s*\**({others})\**\s*:", str(soap.get(sec) or ""), re.M | re.I):
            res.add("soap_format", "critical", "soap", f"{sec.upper()} contains another section header")


def check_summary(summary: str, bounds: tuple[int, int], res: RuleResult) -> None:
    n = word_count(summary)
    if not bounds[0] <= n <= bounds[1]:
        res.add("summary_format", "critical", "summary", f"summary has {n} words (allowed {bounds[0]}-{bounds[1]})")
    if re.search(r"^\s*(?:[-*•]|\d+\.)\s", summary, re.M) or re.search(r"^#+\s", summary, re.M):
        res.add("summary_format", "major", "summary", "summary uses bullets or headings")


def run_rules(record: dict[str, Any], rules_cfg) -> RuleResult:
    """Run every check on one unified record (see data/quality_filter.py for the record schema)."""
    res = RuleResult()
    note = record["note"]
    ann = record["annotation"]
    ent_raw = Entities.model_validate(ann["entities"])
    soap_text = render_soap(ann["soap"])
    full_encounter = record["source"] == "synthetic"
    is_dialogue = record.get("style") == "dialogue_transcript" or record["source"] != "synthetic"

    # Grounding first: later checks use only entities that are traceable to the note.
    clean, total, ungrounded = ground_entities(ent_raw, note)
    res.entities = clean
    n_clean = sum(clean.counts().values())
    res.stats.update(entities_total=total, entities_kept=n_clean, ungrounded=ungrounded)
    if total == 0 or len(ungrounded) / total > rules_cfg.max_ungrounded_entity_frac:
        res.add("grounding", "critical", "entities",
                f"{len(ungrounded)}/{total} entities not found in note: {ungrounded[:5]}")
    elif ungrounded:
        res.add("grounding", "minor", "entities", f"dropped {len(ungrounded)} ungrounded entities: {ungrounded[:5]}")
    if n_clean < rules_cfg.min_entities:
        res.add("grounding", "critical", "entities", f"only {n_clean} grounded entities (min {rules_cfg.min_entities})")

    # Demographics: synthetic records carry reliable scenario metadata; for public data, read the note itself.
    note_age, note_sex = extract_demographics(note)
    meta = record.get("patient") or {}
    age = meta.get("age") if meta.get("age") is not None else note_age
    sex = meta.get("sex") or note_sex
    res.stats.update(age=age, sex=sex)
    if record["source"] == "synthetic" and meta.get("age") is not None:
        ages = stated_ages(note)
        if ages and not any(abs(a - meta["age"]) <= 1 for a in ages):
            res.add("age_sex", "major", "note", f"note states age(s) {ages} but scenario age is {meta['age']}")
    if record["source"] == "synthetic" and meta.get("sex") and note_sex and note_sex != meta["sex"]:
        res.add("age_sex", "critical", "note", f"note describes sex {note_sex} but scenario sex is {meta['sex']}")

    check_vitals(note, res)
    check_labs(note, res)
    check_doses(clean, res)
    check_allergy_conflicts(note, clean, res)
    check_unsupported_medications(note, ann["summary"], soap_text, res)
    check_age_sex({"note": note, "summary": ann["summary"], "soap": soap_text}, age, sex, res)
    check_soap(ann["soap"], full_encounter, res)
    check_summary(ann["summary"], tuple(rules_cfg.summary_words), res)
    if not is_dialogue:
        check_negation(note, clean, res)
    return res
