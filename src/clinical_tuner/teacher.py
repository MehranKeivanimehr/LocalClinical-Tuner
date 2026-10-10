"""Teacher routing (Claude primary, local fallback) and the shared annotation prompts.

Used by both data sources:
* ``data/generate_synthetic_notes.py`` - writes notes *and* labels them;
* ``data/ingest_hf_datasets.py``      - labels public dialogues where the dataset has no compatible reference.
"""

from __future__ import annotations

import logging
from typing import Any

from pydantic import ValidationError

from .config import Config, SamplingCfg
from .llm import BaseChatClient, ProviderUnavailableError, RefusalError, extract_json, make_client
from .schemas import SOAP, Entities

log = logging.getLogger(__name__)


class Teacher:
    """Routes generation to the primary endpoint (Claude) and transparently falls back to the local
    OpenAI-compatible teacher when the primary is unavailable (no credentials) or a request ultimately fails.
    Every call returns the identity of the model that actually produced the output, for provenance."""

    def __init__(self, cfg: Config):
        g = cfg.data.generation
        self.primary: BaseChatClient | None = None
        self.fallback: BaseChatClient | None = None
        try:
            self.primary = make_client(cfg.endpoint(g.primary))
        except ProviderUnavailableError as e:
            log.error("Primary teacher %r unavailable: %s", g.primary, e)
        if g.fallback:
            self.fallback = make_client(cfg.endpoint(g.fallback))
        if self.primary is None and self.fallback is None:
            raise RuntimeError("No usable teacher endpoint (primary unavailable and no fallback configured)")

    async def preflight(self) -> None:
        """Cheap request to detect missing/invalid credentials before launching thousands of calls."""
        if self.primary is None:
            return
        try:
            await self.primary.chat([{"role": "user", "content": "Reply with the single word OK."}], max_tokens=16)
            log.info("Primary teacher %s is reachable.", self.primary.cfg.identity)
        except ProviderUnavailableError as e:
            log.error("Primary teacher unavailable (%s) -> using fallback %s for all requests.", e,
                      self.fallback.cfg.identity if self.fallback else None)
            self.primary = None
        except Exception as e:  # noqa: BLE001
            log.warning("Primary teacher preflight failed (%r); will still try it per request.", e)

    async def chat(self, messages: list[dict[str, str]], sampling: SamplingCfg, *, seed: int | None = None,
                   json_schema: dict[str, Any] | None = None) -> tuple[str, str]:
        if self.primary is not None:
            try:
                out = await self.primary.chat(messages, temperature=sampling.temperature, top_p=sampling.top_p,
                                              max_tokens=sampling.max_tokens, seed=seed, json_schema=json_schema)
                return out[0], self.primary.cfg.identity
            except ProviderUnavailableError as e:
                log.error("Primary teacher became unavailable (%s); switching to fallback.", e)
                self.primary = None
            except RefusalError as e:
                if self.fallback is None:
                    raise
                log.warning("Primary teacher refused (%s); retrying on fallback.", e)
            except Exception as e:  # noqa: BLE001 - the SDK already retried transient errors
                if self.fallback is None:
                    raise
                log.warning("Primary teacher failed (%r); retrying on fallback.", e)
        if self.fallback is None:
            raise RuntimeError("Primary teacher unavailable and no fallback configured")
        out = await self.fallback.chat(messages, temperature=sampling.temperature, top_p=sampling.top_p,
                                       max_tokens=sampling.max_tokens, seed=seed, json_schema=json_schema)
        return out[0], self.fallback.cfg.identity

    def usage(self) -> dict[str, dict[str, int]]:
        return {c.cfg.identity: dict(c.usage) for c in (self.primary, self.fallback) if c is not None and c.usage}

    async def close(self) -> None:
        for c in (self.primary, self.fallback):
            if c is not None:
                await c.close()


# ----------------------------------------------------------------------------------------------------------------
# Annotation (gold labels). The note is the only clinical input, so labels can only use what the note says.
# ----------------------------------------------------------------------------------------------------------------
ANNOTATION_SYSTEM = """You are a meticulous clinical documentation specialist who creates gold-standard reference annotations for clinical NLP. The user gives you one clinical note (a written note or a doctor-patient dialogue transcript). Produce a JSON object with the requested fields.

Rules for all fields:
- Use ONLY information explicitly stated in the note. Do not infer, add, or embellish anything.
- If the note corrects earlier information (for example, a dose is clarified later), use the final corrected information.
- Preserve negations: denied or absent symptoms must never be described as present.
- Never include patient names or other identifiers; refer to "the patient".

"summary": 3 to 6 sentences of plain prose, at most 120 words, in the third person. Cover the patient's age/sex and reason for the visit, the key history and findings, the assessment, and the plan (only those that the note documents).

"soap": an object with
- "subjective": chief complaint, history of present illness, pertinent positives and negatives, and relevant history, medications, allergies, and social history as reported.
- "objective": vital signs, examination findings, and test results as documented, or "Not documented." if there are none.
- "assessment": the clinician's diagnoses or impressions, or "Not documented." if none are stated.
- "plan": a list of strings, one management step per item (medications, tests, referrals, education, follow-up), without numbering; an empty list if no plan is documented.
Write concise clinical prose without markdown. Abbreviations are fine where the note uses them.

"entities": an object with
- "symptoms": list of {"text", "negated"} for symptoms and clinical signs; negated is true for symptoms the patient denies or that are explicitly absent.
- "diagnoses": list of strings: current diagnoses, chronic conditions, past medical history, and the clinician's working diagnosis. Exclude conditions that were ruled out, only listed as differential diagnoses, or that belong to family members.
- "medications": list of {"name", "dose", "frequency"} for medications taken, started, stopped, changed, or administered during the encounter; dose and frequency copied from the note, or null if not stated.
- "procedures": list of strings: procedures, imaging, and diagnostic tests (including laboratory tests) performed, ordered, or planned.
Copy every entity text verbatim from the note: it must be an exact span of the note (capitalization may differ). Do not paraphrase or normalize (write "fussy" if the note says "fussy", not "fussiness"). List each distinct entity once."""

ANNOTATION_USER = """Produce the JSON fields {fields} for this clinical note.

Clinical note:
\"\"\"
{note}
\"\"\""""

SOAP_FROM_REFERENCE_SYSTEM = """You are a clinical documentation specialist. You are given a doctor-patient dialogue and the clinician-written reference note for that same encounter. Restructure the reference note into a SOAP note.

Rules:
- Reuse the reference note's content; do not add facts that are not in the reference note.
- Drop any statement from the reference note that is not supported by the dialogue.
- "subjective": chief complaint, HPI, pertinent positives/negatives, relevant history, medications, allergies, social history.
- "objective": vital signs, examination findings, and results, or "Not documented." if none.
- "assessment": diagnoses/impressions, or "Not documented." if none.
- "plan": list of strings, one management step per item, without numbering.
- Concise clinical prose without markdown. Never include patient names or identifiers; refer to "the patient"."""

SOAP_FROM_REFERENCE_USER = """Dialogue:
\"\"\"
{note}
\"\"\"

Clinician-written reference note:
\"\"\"
{reference}
\"\"\""""


def annotation_schema(fields: tuple[str, ...] = ("summary", "soap", "entities")) -> dict[str, Any]:
    from .schemas import annotation_json_schema

    full = annotation_json_schema()
    return {"type": "object", "properties": {f: full["properties"][f] for f in fields}, "required": list(fields),
            "additionalProperties": False}


def annotation_messages(note: str, fields: tuple[str, ...] = ("summary", "soap", "entities")) -> list[dict[str, str]]:
    return [{"role": "system", "content": ANNOTATION_SYSTEM},
            {"role": "user", "content": ANNOTATION_USER.format(fields=", ".join(f'"{f}"' for f in fields),
                                                               note=note)}]


class AnnotationError(ValueError):
    pass


def parse_annotation(raw: str, fields: tuple[str, ...]) -> dict[str, Any]:
    """Validate teacher JSON into plain dicts ({summary: str, soap: SOAP dict, entities: Entities dict})."""
    parsed = extract_json(raw)
    if not isinstance(parsed, dict):
        raise AnnotationError("unparseable")
    out: dict[str, Any] = {}
    try:
        if "summary" in fields:
            if not isinstance(parsed.get("summary"), str) or not parsed["summary"].strip():
                raise AnnotationError("summary missing")
            out["summary"] = parsed["summary"].strip()
        if "soap" in fields:
            out["soap"] = SOAP.model_validate(parsed.get("soap")).model_dump()
        if "entities" in fields:
            out["entities"] = Entities.model_validate(parsed.get("entities")).model_dump()
    except ValidationError as e:
        raise AnnotationError(f"schema: {str(e)[:300]}") from e
    return out


async def annotate(teacher: Teacher, note: str, sampling: SamplingCfg, fields: tuple[str, ...] = (
        "summary", "soap", "entities"), seed: int | None = None) -> tuple[dict[str, Any], str]:
    raw, model = await teacher.chat(annotation_messages(note, fields), sampling, seed=seed,
                                    json_schema=annotation_schema(fields))
    return parse_annotation(raw, fields), model


async def soap_from_reference(teacher: Teacher, note: str, reference: str, sampling: SamplingCfg,
                              seed: int | None = None) -> tuple[dict[str, Any], str]:
    msgs = [{"role": "system", "content": SOAP_FROM_REFERENCE_SYSTEM},
            {"role": "user", "content": SOAP_FROM_REFERENCE_USER.format(note=note, reference=reference)}]
    raw, model = await teacher.chat(msgs, sampling, seed=seed, json_schema=annotation_schema(("soap",))[
        "properties"]["soap"])
    parsed = extract_json(raw)
    if isinstance(parsed, dict) and "soap" in parsed and isinstance(parsed["soap"], dict):
        parsed = parsed["soap"]
    try:
        return SOAP.model_validate(parsed).model_dump(), model
    except ValidationError as e:
        raise AnnotationError(f"soap schema: {str(e)[:300]}") from e
