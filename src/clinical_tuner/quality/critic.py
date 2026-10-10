"""LLM critic: an independent model grades every encounter against a JSON rubric with severity-tagged errors.

The critic is always a *different* model from the one(s) that wrote the note and labels (checked per record via
model identities), so a generator's systematic mistakes are not rubber-stamped by itself.
"""

from __future__ import annotations

import logging
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from ..llm import BaseChatClient, extract_json
from ..prompts import render_soap

log = logging.getLogger(__name__)

CRITIC_VERSION = "v5"  # bump when the prompt/schema changes so cached verdicts are not reused
DIMENSIONS = ("clinical_plausibility", "internal_consistency", "summary_faithfulness", "soap_faithfulness",
              "soap_completeness", "entity_accuracy")
SEVERITIES = ("critical", "major", "minor")
FIELDS = ("note", "summary", "soap", "entities")
CATEGORIES = ("hallucination", "omission", "negation_error", "dosing_error", "clinical_implausibility",
              "inconsistency", "wrong_section", "format", "identifier", "other")

CRITIC_SYSTEM = """You are a senior physician and clinical documentation auditor reviewing training data for a clinical NLP model. Each item has a source clinical note (a written note or a doctor-patient dialogue transcript) and three reference labels derived from it: a summary, a SOAP note, and extracted entities (symptoms with negation flags, diagnoses, medications, procedures/tests).

Audit the item and return a JSON object with these fields, in this order:
- "checks": three lists that you fill in by actually verifying the labels against the note (use [] when there is nothing to report):
  - "unsupported_statements": statements in the summary or SOAP note (findings, diagnoses, medications, doses, results, plans) that the note does not support or that contradict it.
  - "negation_flag_errors": the entities list symptoms in two groups, PRESENT and ABSENT. Go through both groups item by item and list only the symptoms that are in the WRONG group: a symptom the note says the patient has, reports, or that is found on exam belongs in PRESENT; a symptom the patient denies or that the note says is absent belongs in ABSENT. A symptom that is correctly in ABSENT is NOT an error.
  - "omitted_key_findings": clinically important information stated in the note that is missing from the labels - in particular a diagnosis/impression missing from the SOAP ASSESSMENT or the entities, or a SOAP section marked "Not documented." although the note documents it.
- "analysis": a brief audit (at most 80 words) of the most important problems.
- "scores": integer 1-5 for each dimension (5 = no problems, 4 = trivial issues, 3 = minor issues, 2 = significant issues, 1 = unusable):
  - clinical_plausibility: the note describes a medically coherent encounter (realistic history, findings, doses, test results and management for this patient).
  - internal_consistency: the note does not contradict itself (age, sex, timeline, medications, results).
  - summary_faithfulness: every statement in the summary is supported by the note, with negations preserved.
  - soap_faithfulness: every statement in the SOAP note is supported by the note and placed in the correct section.
  - soap_completeness: the SOAP note captures the clinically important information that the note documents.
  - entity_accuracy: entities are present in the note, correctly typed, negation flags are correct, and important entities are not missing.
- "errors": a list of concrete problems, each {"severity", "field", "category", "description"}:
  - severity "critical": would mislead clinical care or teach a model a wrong fact - a hallucinated or contradicted diagnosis, medication, dose, or result; a flipped negation; a dangerous or impossible value or dose; a patient identifier; a label describing a different patient.
  - severity "major": a clinically relevant omission, an unsupported detail, information in the wrong SOAP section, or a wrong entity type/negation flag on a non-critical item.
  - severity "minor": wording, style, or formatting issues.
  - field: one of note, summary, soap, entities. category: one of hallucination, omission, negation_error, dosing_error, clinical_implausibility, inconsistency, wrong_section, format, identifier, other.
- "overall": integer 1-5 for whether this item should be used as training data (5 = excellent, 3 = acceptable, 1 = must be discarded).

Every item in unsupported_statements or negation_flag_errors is a critical error and must also appear in "errors" with severity "critical". Each omitted key diagnosis or a section wrongly marked "Not documented." is at least a major error. Be strict about faithfulness and safety, and do not penalize the labels for omitting information that is absent from the note. If the note is a short dialogue excerpt, it is acceptable for SOAP sections to say "Not documented." when the excerpt contains no such information."""

CRITIC_USER = """Source: {source}{context}

Clinical note:
\"\"\"
{note}
\"\"\"

Reference summary:
\"\"\"
{summary}
\"\"\"

Reference SOAP note:
\"\"\"
{soap}
\"\"\"

Reference entities:
{entities}"""


class CriticError(BaseModel):
    model_config = ConfigDict(extra="ignore")
    severity: str
    field: str = "other"
    category: str = "other"
    description: str = ""


class CriticChecks(BaseModel):
    model_config = ConfigDict(extra="ignore")
    unsupported_statements: list[str] = Field(default_factory=list)
    negation_flag_errors: list[str] = Field(default_factory=list)
    omitted_key_findings: list[str] = Field(default_factory=list)


class CriticVerdict(BaseModel):
    model_config = ConfigDict(extra="ignore")
    checks: CriticChecks = Field(default_factory=CriticChecks)
    analysis: str = ""
    scores: dict[str, int]
    errors: list[CriticError] = Field(default_factory=list)
    overall: int

    def decision(self, min_overall: int, min_dimension: int) -> tuple[bool, list[str]]:
        reasons = []
        # The explicit checks are binding: any unsupported statement or negation error rejects the record, even if
        # the model forgot to repeat it as a critical error.
        if self.checks.unsupported_statements:
            reasons.append("critic_check:unsupported_statement")
        if self.checks.negation_flag_errors:
            reasons.append("critic_check:negation_flag_error")
        crit = [e for e in self.errors if e.severity == "critical"]
        if crit:
            reasons.append(f"critic_critical:{crit[0].category}")
        majors = [e for e in self.errors if e.severity == "major"]
        if len(self.checks.omitted_key_findings) + len(majors) >= 3:
            reasons.append("critic_major:omissions")
        if self.overall < min_overall:
            reasons.append(f"critic_overall<{min_overall}")
        low = [d for d in DIMENSIONS if self.scores.get(d, 5) <= min_dimension]
        if low:
            reasons.append(f"critic_low:{low[0]}")
        return not reasons, reasons


def critic_schema() -> dict[str, Any]:
    score = {"type": "integer", "enum": [1, 2, 3, 4, 5]}
    str_list = {"type": "array", "items": {"type": "string"}, "maxItems": 12}
    return {
        "type": "object",
        "properties": {
            "checks": {"type": "object", "properties": {"unsupported_statements": str_list,
                                                         "negation_flag_errors": str_list,
                                                         "omitted_key_findings": str_list},
                       "required": ["unsupported_statements", "negation_flag_errors", "omitted_key_findings"],
                       "additionalProperties": False},
            "analysis": {"type": "string"},
            "scores": {"type": "object", "properties": {d: score for d in DIMENSIONS},
                       "required": list(DIMENSIONS), "additionalProperties": False},
            "errors": {"type": "array", "items": {
                "type": "object",
                "properties": {"severity": {"type": "string", "enum": list(SEVERITIES)},
                               "field": {"type": "string", "enum": list(FIELDS)},
                               "category": {"type": "string", "enum": list(CATEGORIES)},
                               "description": {"type": "string"}},
                "required": ["severity", "field", "category", "description"], "additionalProperties": False},
                "maxItems": 12},
            "overall": score,
        },
        "required": ["checks", "analysis", "scores", "errors", "overall"],
        "additionalProperties": False,
    }


def render_entities_for_review(entities: dict[str, Any]) -> str:
    """Plain-language view of the entity labels; avoids the double negatives of a JSON `negated` flag."""
    sx = entities.get("symptoms", [])
    meds = [m["name"] + (f" ({', '.join(x for x in (m.get('dose'), m.get('frequency')) if x)})"
                         if m.get("dose") or m.get("frequency") else "") for m in entities.get("medications", [])]

    def join(items: list[str]) -> str:
        return "; ".join(items) if items else "(none)"

    return "\n".join([
        f"- Symptoms PRESENT (reported or found): {join([s['text'] for s in sx if not s['negated']])}",
        f"- Symptoms ABSENT (denied or explicitly negative): {join([s['text'] for s in sx if s['negated']])}",
        f"- Diagnoses: {join(entities.get('diagnoses', []))}",
        f"- Medications: {join(meds)}",
        f"- Procedures and tests: {join(entities.get('procedures', []))}",
    ])


def critic_messages(record: dict[str, Any], entities: dict[str, Any]) -> list[dict[str, str]]:
    ann = record["annotation"]
    ctx = ""
    if record.get("scenario"):
        s = record["scenario"]
        ctx = (f"\nIntended scenario (metadata, for context only): {s['specialty']}; {s['age']}-year-old "
               f"{'female' if s['sex'] == 'F' else 'male'}; {s['problem']}; style {s['style']}.")
    user = CRITIC_USER.format(source=record["source"], context=ctx, note=record["note"], summary=ann["summary"],
                              soap=render_soap(ann["soap"]), entities=render_entities_for_review(entities))
    return [{"role": "system", "content": CRITIC_SYSTEM}, {"role": "user", "content": user}]


VERIFY_SCHEMA = {"type": "object", "properties": {"status": {"type": "string",
                                                              "enum": ["PRESENT", "ABSENT", "NOT_MENTIONED"]}},
                 "required": ["status"], "additionalProperties": False}


async def verify_negation_flags(client: BaseChatClient, note: str, entities: dict[str, Any],
                                flagged: list[str]) -> list[str]:
    """Second, focused look at each symptom the critic flagged: ask directly whether the note presents it as present
    or absent, and keep the flag only if that answer contradicts the label (filters critic self-contradictions)."""
    from ..text import normalize

    labels = {normalize(s["text"]): s["negated"] for s in entities.get("symptoms", [])}
    confirmed = []
    for item in flagged:
        key = normalize(item)
        if key not in labels:
            continue
        msgs = [{"role": "system", "content": "You answer questions about a clinical note precisely and literally."},
                {"role": "user", "content": f'Clinical note:\n"""\n{note}\n"""\n\nAccording to this note, is "{item}" '
                 "PRESENT (the patient has or reports it, or it is found on examination) or ABSENT (the patient denies "
                 'it or the note says it is absent)? Reply with JSON {"status": "PRESENT" | "ABSENT" | "NOT_MENTIONED"}.'}]
        raw = (await client.chat(msgs, temperature=0.0, max_tokens=64, json_schema=VERIFY_SCHEMA))[0]
        status = (extract_json(raw) or {}).get("status")
        if (status == "PRESENT" and labels[key]) or (status == "ABSENT" and not labels[key]):
            confirmed.append(item)
    return confirmed


SUPPORT_SCHEMA = {"type": "object", "properties": {"verdict": {"type": "string",
                                                               "enum": ["SUPPORTED", "NOT_SUPPORTED"]}},
                  "required": ["verdict"], "additionalProperties": False}


async def verify_unsupported(client: BaseChatClient, note: str, statements: list[str]) -> list[str]:
    """Focused re-check of each statement the critic called unsupported; keeps only confirmed ones."""
    confirmed = []
    for st in statements:
        msgs = [{"role": "system", "content": "You check claims against a clinical note precisely and literally."},
                {"role": "user", "content": f'Clinical note:\n"""\n{note}\n"""\n\nA reviewer says this statement from a '
                 f'label is not supported by the note:\n"""\n{st}\n"""\n\nIs the label content it refers to supported '
                 "by the note? Answer NOT_SUPPORTED only if a specific detail (a number, drug, dose, date, diagnosis, "
                 "finding, or plan item) is absent from the note or contradicts it. Faithful paraphrases and summaries "
                 'of stated facts are SUPPORTED. Reply with JSON {"verdict": "SUPPORTED" | "NOT_SUPPORTED"}.'}]
        raw = (await client.chat(msgs, temperature=0.0, max_tokens=32, json_schema=SUPPORT_SCHEMA))[0]
        if (extract_json(raw) or {}).get("verdict") == "NOT_SUPPORTED":
            confirmed.append(st)
    return confirmed


async def run_critic(client: BaseChatClient, record: dict[str, Any], entities: dict[str, Any],
                     max_tokens: int) -> CriticVerdict:
    raw = (await client.chat(critic_messages(record, entities), temperature=0.0, top_p=1.0, max_tokens=max_tokens,
                             json_schema=critic_schema()))[0]
    parsed = extract_json(raw)
    if parsed is None:
        raise ValueError(f"critic returned unparseable output: {raw[:300]!r}")
    try:
        verdict = CriticVerdict.model_validate(parsed)
    except ValidationError as e:
        raise ValueError(f"critic output failed validation: {e}") from e
    if verdict.checks.negation_flag_errors:
        confirmed = await verify_negation_flags(client, record["note"], entities, verdict.checks.negation_flag_errors)
        verdict.checks.negation_flag_errors = confirmed
        if not confirmed:  # every negation complaint was refuted by the focused re-check
            verdict.errors = [e for e in verdict.errors if e.category != "negation_error"]
    if verdict.checks.unsupported_statements:
        confirmed = await verify_unsupported(client, record["note"], verdict.checks.unsupported_statements)
        verdict.checks.unsupported_statements = confirmed
        if not confirmed:  # every hallucination complaint was refuted by the focused re-check
            verdict.errors = [e for e in verdict.errors if e.category != "hallucination"]
    return verdict
