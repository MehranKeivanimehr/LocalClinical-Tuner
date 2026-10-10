"""Entity grounding: every gold entity must be traceable to a span of the source note."""

from __future__ import annotations

import re

from rapidfuzz import fuzz

from ..schemas import Entities
from ..text import STOPWORDS, contains_term, normalize, stem_tokens

# Common clinical abbreviations <-> expansions. When a teacher expands "HTN" to "hypertension" (or vice versa), the
# entity is rewritten to the note's own surface form so gold labels stay verbatim.
_ABBREVIATIONS: dict[str, list[str]] = {
    "hypertension": ["htn"], "diabetes mellitus": ["dm"], "type 2 diabetes mellitus": ["t2dm", "dm2", "type 2 dm"],
    "type 2 diabetes": ["t2dm", "dm2"], "type 1 diabetes": ["t1dm", "dm1"], "hyperlipidemia": ["hld"],
    "coronary artery disease": ["cad"], "congestive heart failure": ["chf"], "heart failure": ["hf", "chf"],
    "atrial fibrillation": ["afib", "a-fib", "af"], "chronic obstructive pulmonary disease": ["copd"],
    "chronic kidney disease": ["ckd"], "acute kidney injury": ["aki"], "myocardial infarction": ["mi"],
    "deep vein thrombosis": ["dvt"], "pulmonary embolism": ["pe"], "gastroesophageal reflux disease": ["gerd"],
    "urinary tract infection": ["uti"], "obstructive sleep apnea": ["osa"], "benign prostatic hyperplasia": ["bph"],
    "shortness of breath": ["sob"], "dyspnea on exertion": ["doe"], "chest pain": ["cp"], "headache": ["ha"],
    "transient ischemic attack": ["tia"], "cerebrovascular accident": ["cva"], "rheumatoid arthritis": ["ra"],
    "osteoarthritis": ["oa"], "electrocardiogram": ["ecg", "ekg"], "complete blood count": ["cbc"],
    "basic metabolic panel": ["bmp"], "comprehensive metabolic panel": ["cmp"], "chest x-ray": ["cxr"],
    "computed tomography": ["ct"], "magnetic resonance imaging": ["mri"], "hemoglobin a1c": ["a1c", "hba1c"],
    "thyroid stimulating hormone": ["tsh"], "urinalysis": ["ua"], "echocardiogram": ["echo", "tte"],
    "major depressive disorder": ["mdd"], "generalized anxiety disorder": ["gad"],
    "attention deficit hyperactivity disorder": ["adhd"], "post-traumatic stress disorder": ["ptsd"],
    "irritable bowel syndrome": ["ibs"], "inflammatory bowel disease": ["ibd"], "diabetic ketoacidosis": ["dka"],
    "upper respiratory infection": ["uri"], "community-acquired pneumonia": ["cap"],
    "peripheral artery disease": ["pad"], "polycystic ovary syndrome": ["pcos"],
    "systemic lupus erythematosus": ["sle"], "acute coronary syndrome": ["acs"], "end-stage renal disease": ["esrd"],
    "nausea and vomiting": ["n/v"], "lumbar puncture": ["lp"],
}
_ALIASES: dict[str, set[str]] = {}
for _full, _abbrs in _ABBREVIATIONS.items():
    for _a in _abbrs:
        _ALIASES.setdefault(_full, set()).add(_a)
        _ALIASES.setdefault(_a, set()).add(_full)


def verbatim_alias(term: str, note: str, note_norm: str) -> str | None:
    """If an abbreviation/expansion of ``term`` occurs in the note, return it exactly as written in the note."""
    for alias in sorted(_ALIASES.get(normalize(term), ()), key=len, reverse=True):
        if contains_term(note_norm, alias):
            m = re.search(rf"(?<![\w]){re.escape(alias)}(?![\w])", note, re.IGNORECASE)
            return m.group(0) if m else alias
    return None


class NoteIndex:
    """Pre-computed views of a note for fast repeated grounding checks."""

    def __init__(self, note: str):
        self.norm = normalize(note)
        self.stems = stem_tokens(note)


def is_grounded(term: str, idx: NoteIndex) -> bool:
    """True if ``term`` appears in the note: (1) verbatim whole-word, (2) near-verbatim (fuzzy >= 90, handles
    hyphenation/typos), or (3) all content-word stems appear within a short window (handles inflection and
    light reordering, e.g. 'fussiness' vs 'fussy')."""
    t = normalize(term)
    if not t:
        return False
    if contains_term(idx.norm, t):
        return True
    if len(t) >= 5 and fuzz.partial_ratio(t, idx.norm) >= 90:
        return True
    content = [s for s in stem_tokens(t) if s not in STOPWORDS]
    if not content:
        return False
    window = len(content) + 3
    stems = idx.stems
    positions = [i for i, s in enumerate(stems) if s == content[0]]
    for p in positions:
        span = set(stems[max(0, p - window) : p + window + 1])
        if all(c in span for c in content):
            return True
    return False


def ground_entities(ent: Entities, note: str) -> tuple[Entities, int, list[str]]:
    """Drop ungrounded and duplicate entities; rewrite abbreviation/expansion mismatches to the note's verbatim
    surface form. Returns (clean_entities, n_unique_total, ungrounded_terms)."""
    idx = NoteIndex(note)
    total = 0
    ungrounded: list[str] = []

    def resolve_text(text: str, seen: set[str]) -> str | None:
        nonlocal total
        key = normalize(text)
        if not key or key in seen:
            return None
        seen.add(key)
        total += 1
        if is_grounded(text, idx):
            return text
        if (alias := verbatim_alias(text, note, idx.norm)) is not None and normalize(alias) not in seen:
            seen.add(normalize(alias))
            return alias
        ungrounded.append(text)
        return None

    seen: dict[str, set[str]] = {k: set() for k in ("sx", "dx", "rx", "px")}
    symptoms, diagnoses, medications, procedures = [], [], [], []
    for s in ent.symptoms:
        if (t := resolve_text(s.text, seen["sx"])) is not None:
            symptoms.append(s.model_copy(update={"text": t}))
    for d in ent.diagnoses:
        if (t := resolve_text(d, seen["dx"])) is not None:
            diagnoses.append(t)
    for m in ent.medications:
        if (t := resolve_text(m.name, seen["rx"])) is not None:
            medications.append(m.model_copy(update={"name": t}))
    for p in ent.procedures:
        if (t := resolve_text(p, seen["px"])) is not None:
            procedures.append(t)
    clean = Entities(symptoms=symptoms, diagnoses=diagnoses, medications=medications, procedures=procedures)
    for m in clean.medications:  # normalise placeholder strings to null
        if m.dose is not None and normalize(m.dose) in {"", "null", "none", "not stated", "n/a", "unknown"}:
            m.dose = None
        if m.frequency is not None and normalize(m.frequency) in {"", "null", "none", "not stated", "n/a",
                                                                  "unknown"}:
            m.frequency = None
    return clean, total, ungrounded
