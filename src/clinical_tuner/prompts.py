"""Single source of truth for every prompt the *student* model sees.

Training, evaluation, preference-data construction and serving (Gradio + Ollama Modelfile) all import from here,
so the prompt format at inference time is byte-identical to the one used in training.
"""

from __future__ import annotations

import json
import re
from typing import Any

TASKS: tuple[str, ...] = ("summary", "soap", "entities")

SYSTEM_PROMPT = (
    "You are a clinical documentation assistant that runs entirely on local hardware. "
    "You help clinicians summarize encounter notes, write structured SOAP notes, and extract medical entities. "
    "Use only information stated in the provided note. Never invent findings, diagnoses, medications, doses, "
    "or plans, and preserve negations exactly (for example, a symptom the patient denies must not be reported "
    "as present)."
)

TASK_INSTRUCTIONS: dict[str, str] = {
    "summary": (
        "Summarize the following clinical encounter note for a busy clinician.\n"
        "Requirements:\n"
        "- Write 3 to 6 sentences of plain prose (maximum 120 words), in the third person.\n"
        "- Cover the patient's age/sex and reason for the visit, the key history and findings, "
        "the assessment, and the plan.\n"
        "- Use only information stated in the note. Do not use headings or bullet points."
    ),
    "soap": (
        "Convert the following clinical encounter note into a structured SOAP note.\n"
        "Requirements:\n"
        "- Use exactly these four section headers, each on its own line and in this order: "
        "SUBJECTIVE:, OBJECTIVE:, ASSESSMENT:, PLAN:\n"
        "- SUBJECTIVE: chief complaint, history of present illness, pertinent positives and negatives, "
        "and relevant history, medications, allergies, and social history as reported.\n"
        "- OBJECTIVE: vital signs, examination findings, and test results that are documented.\n"
        "- ASSESSMENT: the clinician's diagnoses or impressions.\n"
        "- PLAN: a numbered list of management steps (medications, tests, referrals, education, follow-up).\n"
        "- Include only information stated in the note. If a section has no documented information, "
        "write \"Not documented.\""
    ),
    "entities": (
        "Extract the key medical entities from the following clinical encounter note.\n"
        "Return ONLY a JSON object with exactly these keys:\n"
        '- "symptoms": list of objects {"text": string, "negated": boolean}. Include symptoms and clinical '
        "signs; set negated to true for symptoms the patient denies or that are explicitly absent.\n"
        '- "diagnoses": list of strings: current diagnoses, chronic conditions, past medical history, and the '
        "clinician's working diagnosis (exclude conditions that were ruled out or only listed as differentials).\n"
        '- "medications": list of objects {"name": string, "dose": string or null, "frequency": string or '
        "null} for medications taken, started, stopped, changed, or given during the encounter.\n"
        '- "procedures": list of strings: procedures, imaging, and diagnostic tests performed, ordered, or '
        "planned.\n"
        "Copy each entity verbatim from the note, do not include duplicates, and do not add any text outside "
        "the JSON object."
    ),
}

SOAP_SECTIONS: tuple[str, ...] = ("SUBJECTIVE", "OBJECTIVE", "ASSESSMENT", "PLAN")
ENTITY_KEYS: tuple[str, ...] = ("symptoms", "diagnoses", "medications", "procedures")


def build_user_message(task: str, note: str) -> str:
    if task not in TASK_INSTRUCTIONS:
        raise ValueError(f"Unknown task {task!r}; expected one of {TASKS}")
    return f"{TASK_INSTRUCTIONS[task]}\n\nClinical note:\n\"\"\"\n{note.strip()}\n\"\"\""


def build_messages(task: str, note: str) -> list[dict[str, str]]:
    """Chat messages (system + user) for one student task. The assistant turn is the model's answer."""
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": build_user_message(task, note)},
    ]


def render_soap(soap: dict[str, Any]) -> str:
    """Render a SOAP dict (subjective/objective/assessment: str, plan: list[str] | str) to the canonical text."""

    def _section(value: Any, name: str = "") -> str:
        text = (value or "").strip() if isinstance(value, str) else ""
        if name:  # some teachers repeat the section header inside the section ("ASSESSMENT: ASSESSMENT: ...")
            text = re.sub(rf"^\s*\**{name}\**\s*:\s*", "", text, flags=re.IGNORECASE).strip()
        return text or "Not documented."

    plan = soap.get("plan")
    if isinstance(plan, list):
        items = [str(p).strip().lstrip("-*• ").strip() for p in plan if str(p).strip()]
        # Strip any numbering the generator may have added, then re-number canonically.
        items = [_strip_leading_number(i) for i in items]
        plan_text = "\n".join(f"{i}. {item}" for i, item in enumerate(items, 1)) or "Not documented."
    else:
        plan_text = _section(plan, "plan")
    return (
        f"SUBJECTIVE:\n{_section(soap.get('subjective'), 'subjective')}\n\n"
        f"OBJECTIVE:\n{_section(soap.get('objective'), 'objective')}\n\n"
        f"ASSESSMENT:\n{_section(soap.get('assessment'), 'assessment')}\n\n"
        f"PLAN:\n{plan_text}"
    )


def _strip_leading_number(text: str) -> str:
    i = 0
    while i < len(text) and text[i].isdigit():
        i += 1
    if 0 < i < len(text) and text[i] in ".)":
        return text[i + 1 :].strip()
    return text


def render_entities(entities: dict[str, Any]) -> str:
    """Canonical JSON rendering of the entity annotation (stable key order, 2-space indent)."""
    ordered = {k: entities.get(k, []) for k in ENTITY_KEYS}
    return json.dumps(ordered, indent=2, ensure_ascii=False)
