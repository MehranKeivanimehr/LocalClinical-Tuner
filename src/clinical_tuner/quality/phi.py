"""Best-effort PHI-pattern detection and scrubbing.

The public sources used here are already de-identified / role-played (ACI-Bench, MTS-Dialog) and synthetic notes
are generated without identifiers, so this is defence in depth, not a de-identification system: it removes
identifier *patterns* (phones, e-mails, SSNs, MRNs, calendar dates, URLs, street addresses, titled names and common
first names) and reports what it changed so the data card can account for it.
"""

from __future__ import annotations

import re
from collections import Counter

_MONTH = (r"(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|sep(?:t(?:ember)?)?|"
          r"oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)")
_DAY_WORD = (r"(?:(?:twenty|thirty)[- ]?)?(?:first|second|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth|eleventh|"
             r"twelfth|thirteenth|fourteenth|fifteenth|sixteenth|seventeenth|eighteenth|nineteenth|twentieth|thirtieth|"
             r"one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|thirteen|fourteen|fifteen|sixteen|"
             r"seventeen|eighteen|nineteen|twenty|thirty)")
_YEAR_WORD = (r"(?:(?:nineteen|twenty)(?:[- ](?:hundred|oh|o))?[- ](?:[a-z]+)(?:[- ](?:one|two|three|four|five|six|"
              r"seven|eight|nine))?|two thousand(?: and)?(?: (?:one|two|three|four|five|six|seven|eight|nine|ten|"
              r"eleven|twelve|thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen|twenty[- ]?[a-z]*))?)")
_DAY = rf"(?:\d{{1,2}}(?:st|nd|rd|th)?|{_DAY_WORD})"
_YEAR = rf"(?:(?:19|20)\d{{2}}|{_YEAR_WORD})"

PHI_PATTERNS: dict[str, tuple[re.Pattern[str], str]] = {
    "email": (re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.]+\b"), "[EMAIL]"),
    "url": (re.compile(r"\bhttps?://\S+|\bwww\.\S+", re.IGNORECASE), "[URL]"),
    "ssn": (re.compile(r"\b\d{3}-\d{2}-\d{4}\b"), "[ID]"),
    "phone": (re.compile(r"(?<!\d)(?:\+?1[-. ]?)?\(?\d{3}\)?[-. ]\d{3}[-. ]\d{4}(?!\d)"), "[PHONE]"),
    "mrn": (re.compile(r"\b(?:MRN|medical record (?:number|no\.?)|patient id)\s*[:#]?\s*\w*\d\w*", re.IGNORECASE),
            "[ID]"),
    "calendar_date": (re.compile(
        rf"\b(?:\d{{1,2}}[/-]\d{{1,2}}[/-](?:19|20)\d{{2}}|(?:19|20)\d{{2}}-\d{{2}}-\d{{2}}|"
        rf"{_MONTH}\.? (?:the )?{_DAY},? (?:of )?{_YEAR}|"            # June 30, 2004 / june thirty two thousand and four
        rf"(?:the )?{_DAY} (?:of )?{_MONTH},? {_YEAR})\b", re.IGNORECASE), "[DATE]"),  # 30th of June 2004
    "street_address": (re.compile(
        r"\b\d{1,5} [A-Z][a-z]+(?: [A-Z][a-z]+)* (?:Street|St|Avenue|Ave|Road|Rd|Boulevard|Blvd|Lane|Ln|Drive|Dr|"
        r"Court|Ct)\b\.?"), "[ADDRESS]"),
    "titled_name": (re.compile(r"\b(Mr|Mrs|Ms|Miss|Dr|Doctor)(\.?) (?!Not\b)[A-Z][a-z]{2,}(?: [A-Z][a-z]{2,})?"),
                    r"\1\2 [NAME]"),
}

# Common US first names that are not also everyday English/clinical words (e.g. no "mark", "grace", "frank").
COMMON_FIRST_NAMES = frozenset("""
james john robert michael william david richard joseph thomas charles christopher daniel matthew anthony
donald steven paul andrew joshua kenneth kevin brian george timothy ronald edward jason jeffrey ryan jacob
gary nicholas eric jonathan stephen larry justin scott brandon benjamin samuel gregory alexander patrick
raymond dennis jerry tyler aaron jose adam nathan henry douglas zachary peter kyle ethan walter noah jeremy
keith roger terry gerald harold sean carl arthur lawrence dylan jesse bryan billy joe bruce gabriel albert
willie alan juan wayne elijah randy roy vincent ralph eugene russell bobby philip louis mary patricia
jennifer linda elizabeth barbara susan jessica sarah karen lisa nancy betty margaret sandra ashley kimberly
emily donna michelle carol amanda dorothy melissa deborah stephanie rebecca sharon laura cynthia kathleen
amy angela shirley anna brenda pamela emma nicole helen samantha katherine christine debra rachel carolyn
janet catherine maria diane ruth julie olivia joyce victoria kelly lauren christina joan evelyn judith megan
andrea cheryl hannah jacqueline martha gloria teresa sara frances kathryn janice abigail alice judy sophia
denise doris marilyn danielle beverly isabella theresa diana natalie brittany marie kayla alexis lori
""".split())

# A surname right after an already-scrubbed first name ("[NAME] taylor ,"), unless it is an ordinary word.
_NOT_SURNAME = {"today", "okay", "ok", "please", "thanks", "thank", "yes", "no", "sir", "here", "there", "again",
                "now", "too", "so", "then", "right", "good", "well", "hi", "hello"}
_SURNAME_AFTER_NAME_RE = re.compile(r"\[NAME\] ([A-Za-z][a-z]+)(?=\s*[,.;:!?])")

# Names introduced explicitly ("next patient is [NAME] jackson, ..."): scrub up to two tokens after the cue when the
# second token is directly followed by punctuation (so "my name is John and ..." keeps "and").
_INTRO_NAME_RE = re.compile(
    r"\b(next patient is|patient's name is|patient name is|my name is|her name is|his name is)\s+"
    r"(?!(?:a|an|the|not)\b)(\[NAME\]|[A-Za-z][a-z]+)(?:\s+([A-Za-z][a-z]+)(?=\s*[,.]))?", re.IGNORECASE)

_FIRST_NAME_RE = re.compile(r"\b(" + "|".join(sorted(COMMON_FIRST_NAMES, key=len, reverse=True)) + r")\b",
                            re.IGNORECASE)


def find_phi(text: str) -> Counter[str]:
    """Count identifier-like patterns (used to *reject* synthetic notes, which must contain none)."""
    found: Counter[str] = Counter()
    for name, (rx, _) in PHI_PATTERNS.items():
        if name == "titled_name":
            continue  # titled clinicians ("Dr. Patel") are tolerated in synthetic notes only via scrubbing
        n = len(rx.findall(text))
        if n:
            found[name] += n
    return found


def scrub_phi(text: str, scrub_first_names: bool = True) -> tuple[str, Counter[str]]:
    """Replace identifier patterns with typed placeholders. Returns (scrubbed_text, counts_by_type)."""
    counts: Counter[str] = Counter()
    for name, (rx, repl) in PHI_PATTERNS.items():
        text, n = rx.subn(repl, text)
        if n:
            counts[name] += n
    if scrub_first_names:
        text, n = _FIRST_NAME_RE.subn("[NAME]", text)
        if n:
            counts["first_name"] += n
    text, n = _INTRO_NAME_RE.subn(lambda m: f"{m.group(1)} [NAME]", text)
    if n:
        counts["introduced_name"] += n

    def _surname(m: re.Match[str]) -> str:
        if m.group(1).lower() in _NOT_SURNAME:
            return m.group(0)
        counts["surname"] += 1
        return "[NAME]"

    text = _SURNAME_AFTER_NAME_RE.sub(_surname, text)
    return text, counts


_EN_FUNCTION_WORDS = frozenset(
    """
the and of to a in is that it you for on with was as have be are this at not but or i my he she they we your
do did any what how about so
""".split()
)


def english_score(text: str) -> float:
    """Fraction of tokens that are common English function words (~0.3-0.5 for English prose/dialogue)."""
    toks = re.findall(r"[a-zA-Z']+", text.lower())
    if not toks:
        return 0.0
    return sum(t in _EN_FUNCTION_WORDS for t in toks) / len(toks)
