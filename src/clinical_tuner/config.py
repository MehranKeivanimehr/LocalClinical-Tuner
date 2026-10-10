"""Typed, validated loading of ``training/config.yaml`` with ``--set key.path=value`` overrides.

Unknown keys raise an error (``extra="forbid"``), so a typo in the config fails loudly instead of being ignored.
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field

from .llm import EndpointConfig
from .utils import load_dotenv, resolve

DEFAULT_CONFIG = "training/config.yaml"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ProjectCfg(_Strict):
    seed: int = 3407
    output_dir: str = "outputs"
    results_dir: str = "results"


class EndpointCfg(_Strict):
    model: str
    provider: Literal["openai", "anthropic"] = "openai"
    base_url: str | None = None
    label: str | None = None  # model identity for provenance (e.g. "Qwen/Qwen3-32B" for a vLLM served name)
    api_key_env: str | None = None
    max_concurrency: int = 32
    timeout: float = 600.0
    max_retries: int = 5
    extra_body: dict[str, Any] = Field(default_factory=dict)
    effort: str | None = None
    server_fallback: bool = True
    send_sampling_params: bool = False
    min_max_tokens: int = 16000
    pricing: dict[str, float] | None = None  # USD per 1M tokens, for the data card's cost estimate

    def to_endpoint(self) -> EndpointConfig:
        return EndpointConfig(**self.model_dump(exclude={"pricing"}))


class SamplingCfg(_Strict):
    temperature: float = 0.7
    top_p: float = 0.9
    max_tokens: int = 2048


class GenerationCfg(_Strict):
    primary: str = "claude"  # endpoint name; Claude via the Anthropic API
    fallback: str | None = "teacher"  # local OpenAI-compatible teacher used when the primary is unavailable


class SyntheticCfg(_Strict):
    n_generate: int = 1750  # over-generate: the quality filter rejects some encounters
    note_generation: SamplingCfg = SamplingCfg(temperature=0.9, top_p=0.95, max_tokens=1600)
    min_note_words: int = 100
    max_note_words: int = 1000


class HFSourceCfg(_Strict):
    name: str
    hf_id: str
    configs: list[str | None] = Field(default_factory=lambda: [None])
    splits: list[str] = Field(default_factory=list)
    license: str
    upstream: str
    citation: str = ""
    reuse_reference: Literal["soap_from_note", "summary_from_section", "none"] = "none"
    max_records: int | None = None


class HFExcludedCfg(_Strict):
    name: str
    reason: str


class HFCleaningCfg(_Strict):
    min_words: int = 80
    max_words: int = 1800
    min_english_score: float = 0.15  # fraction of tokens that are common English function words
    near_duplicate_jaccard: float = 0.6


class HFCfg(_Strict):
    sources: list[HFSourceCfg] = Field(default_factory=list)
    excluded: list[HFExcludedCfg] = Field(default_factory=list)
    cleaning: HFCleaningCfg = HFCleaningCfg()


class RulesCfg(_Strict):
    max_major: int = 1  # reject when the number of major rule violations exceeds this
    min_entities: int = 3
    max_ungrounded_entity_frac: float = 0.2
    summary_words: tuple[int, int] = (25, 160)


class CriticCfg(_Strict):
    enabled: bool = True
    # Candidate critic endpoints in priority order; for each record the first reachable one whose model family
    # differs from every model that generated that record is used (private records: local endpoints only).
    endpoints: list[str] = Field(default_factory=lambda: ["teacher", "claude", "judge"])
    min_overall: int = 3
    min_dimension: int = 2  # reject if any rubric dimension is <= this
    max_tokens: int = 3000


class ReviewCfg(_Strict):
    n_accepted: int = 25
    n_rejected: int = 25


class QualityCfg(_Strict):
    rules: RulesCfg = RulesCfg()
    critic: CriticCfg = CriticCfg()
    review: ReviewCfg = ReviewCfg()


class CompositionCfg(_Strict):
    total_encounters: int = 2000
    synthetic_fraction: float = 0.7  # remainder comes from the Hugging Face sources


class SplitCfg(_Strict):
    # Encounters per split; values < 1 are fractions of the final dataset (the remainder is SFT train).
    test: float = 0.075
    val: float = 0.025
    pref: float = 0.2

    def counts(self, n: int) -> dict[str, int]:
        return {k: (round(v * n) if v < 1 else int(v)) for k, v in self.model_dump().items()}


class DataCfg(_Strict):
    raw_dir: str = "data/raw"
    filtered_dir: str = "data/filtered"
    review_dir: str = "data/review"
    processed_dir: str = "data/processed"
    data_card: str = "data/DATA_CARD.md"
    generation: GenerationCfg = GenerationCfg()
    synthetic: SyntheticCfg = SyntheticCfg()
    annotation: SamplingCfg = SamplingCfg(temperature=0.3, top_p=0.9, max_tokens=3000)
    hf: HFCfg = HFCfg()
    quality: QualityCfg = QualityCfg()
    composition: CompositionCfg = CompositionCfg()
    splits: SplitCfg = SplitCfg()
    near_duplicate_jaccard: float = 0.6


class ModelCfg(_Strict):
    base_model: str = "Qwen/Qwen2.5-7B-Instruct"
    max_seq_length: int = 4096
    load_in_4bit: bool = True
    # Turn markers used to mask the prompt so loss is computed on assistant tokens only. "auto" detects
    # ChatML (Qwen) and Llama-3 templates; otherwise give {"instruction": "...", "response": "..."}.
    chat_template_markers: Literal["auto"] | dict[str, str] = "auto"


class LoraCfg(_Strict):
    r: int = 32
    alpha: int = 64
    dropout: float = 0.0
    target_modules: list[str] = Field(
        default_factory=lambda: ["q_proj", "k_proj", "v_proj", "o_proj", "gate_proj", "up_proj", "down_proj"]
    )
    use_dora: bool = False
    use_rslora: bool = False


class TrainCfg(_Strict):
    output_dir: str
    epochs: float = 2.0
    max_steps: int = -1
    learning_rate: float = 2e-4
    per_device_batch_size: int = 4
    gradient_accumulation_steps: int = 4
    warmup_ratio: float = 0.05
    lr_scheduler: str = "cosine"
    weight_decay: float = 0.01
    max_grad_norm: float = 1.0
    optim: str = "adamw_8bit"
    logging_steps: int = 10
    eval_steps: int = 50
    save_merged: bool = True


class PreferenceCfg(_Strict):
    policy_endpoint: str = "sft"
    n_samples: int = 4
    temperature: float = 0.9
    top_p: float = 0.95
    max_tokens: int = 1536
    min_margin: float = 0.15
    hard_negatives: bool = True
    val_fraction: float = 0.05


class PrefTrainCfg(TrainCfg):
    method: Literal["orpo", "dpo"] = "orpo"
    beta: float = 0.1
    nll_on_prompt: bool = False  # ORPO only: TRL's default NLL over prompt + response (True) or response only


class EvalCfg(_Strict):
    models: list[str] = Field(default_factory=lambda: ["base", "sft", "orpo"])
    temperature: float = 0.0
    max_tokens: int = 1536
    extra_body: dict[str, Any] = Field(default_factory=dict)
    bertscore_model: str = "roberta-large"
    bertscore_batch_size: int = 32
    bootstrap_samples: int = 1000
    judge_endpoint: str = "judge"
    judge_per_task: int = 50
    judge_pairs: list[tuple[str, str]] = Field(default_factory=lambda: [("orpo", "base"), ("orpo", "sft"), ("sft", "base")])
    n_sample_outputs: int = 2


class ExportCfg(_Strict):
    merged_model: str = "outputs/orpo/merged"
    output_dir: str = "outputs/gguf"
    model_name: str = "localclinical"
    quantizations: list[str] = Field(default_factory=lambda: ["Q4_K_M", "Q5_K_M"])
    llama_cpp_dir: str = "third_party/llama.cpp"
    llama_cpp_ref: str = "v0.6.0"
    modelfile_quant: str = "Q4_K_M"


class ServingCfg(_Strict):
    endpoint: str = "ollama"
    compare_endpoint: str | None = None
    gradio_host: str = "127.0.0.1"
    gradio_port: int = 7860


class Config(_Strict):
    project: ProjectCfg = ProjectCfg()
    endpoints: dict[str, EndpointCfg] = Field(default_factory=dict)
    data: DataCfg = DataCfg()
    model: ModelCfg = ModelCfg()
    lora: LoraCfg = LoraCfg()
    sft: TrainCfg = TrainCfg(output_dir="outputs/sft")
    preference: PreferenceCfg = PreferenceCfg()
    orpo: PrefTrainCfg = PrefTrainCfg(output_dir="outputs/orpo", learning_rate=3e-5, epochs=2)
    evaluation: EvalCfg = EvalCfg()
    export: ExportCfg = ExportCfg()
    serving: ServingCfg = ServingCfg()

    def endpoint(self, name: str) -> EndpointConfig:
        if name not in self.endpoints:
            raise KeyError(f"Endpoint {name!r} not defined in config.endpoints ({sorted(self.endpoints)})")
        return self.endpoints[name].to_endpoint()


def _set_path(d: dict[str, Any], dotted: str, value: Any) -> None:
    keys = dotted.split(".")
    for k in keys[:-1]:
        d = d.setdefault(k, {})
    d[keys[-1]] = value


def load_config(path: str | Path = DEFAULT_CONFIG, overrides: list[str] | None = None) -> Config:
    load_dotenv()
    with open(resolve(path), encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}
    for ov in overrides or []:
        key, sep, val = ov.partition("=")
        if not sep:
            raise ValueError(f"Override must look like key.path=value, got {ov!r}")
        _set_path(raw, key.strip(), yaml.safe_load(val))
    return Config.model_validate(raw)


def add_config_args(parser: argparse.ArgumentParser) -> argparse.ArgumentParser:
    parser.add_argument("--config", default=DEFAULT_CONFIG, help="Path to config YAML (default: %(default)s)")
    parser.add_argument(
        "--set", dest="overrides", action="append", default=[], metavar="KEY=VALUE",
        help="Override a config value, e.g. --set sft.epochs=1 --set data.n_encounters=50 (repeatable)",
    )
    return parser
