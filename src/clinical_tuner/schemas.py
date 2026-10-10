"""Typed schemas for the synthetic dataset and the structured (JSON) outputs."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


class Symptom(BaseModel):
    model_config = ConfigDict(extra="ignore")
    text: str
    negated: bool = False


class Medication(BaseModel):
    model_config = ConfigDict(extra="ignore")
    name: str
    dose: str | None = None
    frequency: str | None = None


class Entities(BaseModel):
    model_config = ConfigDict(extra="ignore")
    symptoms: list[Symptom] = Field(default_factory=list)
    diagnoses: list[str] = Field(default_factory=list)
    medications: list[Medication] = Field(default_factory=list)
    procedures: list[str] = Field(default_factory=list)

    @field_validator("diagnoses", "procedures", mode="before")
    @classmethod
    def _coerce_str_list(cls, v: Any) -> list[str]:
        return [str(x).strip() for x in (v or []) if str(x).strip()]

    def counts(self) -> dict[str, int]:
        return {
            "symptoms": len(self.symptoms),
            "diagnoses": len(self.diagnoses),
            "medications": len(self.medications),
            "procedures": len(self.procedures),
        }


class SOAP(BaseModel):
    model_config = ConfigDict(extra="ignore")
    subjective: str
    objective: str
    assessment: str
    plan: list[str]


class GoldAnnotation(BaseModel):
    """What the teacher produces for each synthetic note (rendered later into the three task targets)."""

    model_config = ConfigDict(extra="ignore")
    summary: str
    soap: SOAP
    entities: Entities


class Scenario(BaseModel):
    """Sampled metadata that steers the teacher towards diverse notes."""

    encounter_id: str
    specialty: str
    problem: str
    style: str
    difficulty: str
    setting: str
    age: int
    sex: str
    complicating_factors: list[str] = Field(default_factory=list)
    target_words: tuple[int, int]


def annotation_json_schema() -> dict[str, Any]:
    """JSON schema handed to the teacher server for guided (structured) decoding."""
    str_list = {"type": "array", "items": {"type": "string"}}
    nullable_str = {"anyOf": [{"type": "string"}, {"type": "null"}]}
    return {
        "type": "object",
        "properties": {
            "summary": {"type": "string"},
            "soap": {
                "type": "object",
                "properties": {
                    "subjective": {"type": "string"},
                    "objective": {"type": "string"},
                    "assessment": {"type": "string"},
                    "plan": str_list,
                },
                "required": ["subjective", "objective", "assessment", "plan"],
                "additionalProperties": False,
            },
            "entities": {
                "type": "object",
                "properties": {
                    "symptoms": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {"text": {"type": "string"}, "negated": {"type": "boolean"}},
                            "required": ["text", "negated"],
                            "additionalProperties": False,
                        },
                    },
                    "diagnoses": str_list,
                    "medications": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {"name": {"type": "string"}, "dose": nullable_str, "frequency": nullable_str},
                            "required": ["name", "dose", "frequency"],
                            "additionalProperties": False,
                        },
                    },
                    "procedures": str_list,
                },
                "required": ["symptoms", "diagnoses", "medications", "procedures"],
                "additionalProperties": False,
            },
        },
        "required": ["summary", "soap", "entities"],
        "additionalProperties": False,
    }
