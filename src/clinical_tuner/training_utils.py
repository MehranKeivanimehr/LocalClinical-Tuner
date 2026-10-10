"""Helpers shared by the SFT and preference-optimization stages (import AFTER ``unsloth``)."""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

CHAT_MARKERS = {
    # ChatML (Qwen2.5 / Qwen3)
    "<|im_start|>": {"instruction": "<|im_start|>user\n", "response": "<|im_start|>assistant\n"},
    # Llama 3.x
    "<|start_header_id|>": {"instruction": "<|start_header_id|>user<|end_header_id|>\n\n",
                            "response": "<|start_header_id|>assistant<|end_header_id|>\n\n"},
}


def response_markers(tokenizer, override: str | dict[str, str]) -> dict[str, str]:
    """Turn markers used to mask everything except the assistant answer from the loss."""
    if isinstance(override, dict):
        return override
    template = tokenizer.chat_template or ""
    for key, markers in CHAT_MARKERS.items():
        if key in template:
            return markers
    raise ValueError("Unknown chat template; set model.chat_template_markers: {instruction: ..., response: ...}")


def render_chat(tokenizer, messages: list[dict[str, str]], answer: str | None = None) -> str:
    """Full conversation text (with the answer) for SFT, or the prompt with the generation header for inference."""
    if answer is None:
        return tokenizer.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
    return tokenizer.apply_chat_template(messages + [{"role": "assistant", "content": answer}], tokenize=False)


def n_tokens(tokenizer, text: str) -> int:
    return len(tokenizer(text, add_special_tokens=False)["input_ids"])


def warmup_steps(n_examples: int, batch: int, grad_accum: int, epochs: float, max_steps: int, ratio: float) -> int:
    """transformers 5 deprecates ``warmup_ratio``; compute an explicit step count instead."""
    import math

    steps = max_steps if max_steps and max_steps > 0 else math.ceil(n_examples / (batch * grad_accum)) * epochs
    return max(1, math.ceil(steps * ratio))


def gpu_memory_gb() -> dict[str, float]:
    import torch

    if not torch.cuda.is_available():
        return {}
    return {"peak_reserved_gb": round(torch.cuda.max_memory_reserved() / 1024**3, 2),
            "peak_allocated_gb": round(torch.cuda.max_memory_allocated() / 1024**3, 2),
            "gpu": torch.cuda.get_device_name(0)}


def check_label_mask(trainer, tokenizer, reference: str) -> None:
    """Fail fast if the loss mask is wrong: the only supervised tokens must be the assistant answer."""
    sample = trainer.train_dataset[0]
    labels = sample["labels"]
    supervised = tokenizer.decode([t for t in labels if t != -100]).strip()
    if not supervised.startswith(reference.strip()[:40]):
        raise RuntimeError(f"Loss mask looks wrong. Supervised text starts with: {supervised[:200]!r}")
    n_sup = sum(t != -100 for t in labels)
    log.info("Loss-mask check OK: %d/%d tokens supervised (assistant answer only).", n_sup, len(labels))


def save_metrics(out_dir: Path, trainer, extra: dict[str, Any]) -> dict[str, Any]:
    history = trainer.state.log_history
    train_losses = [h["loss"] for h in history if "loss" in h]
    eval_losses = [h["eval_loss"] for h in history if "eval_loss" in h]
    metrics = {
        "final_train_loss": train_losses[-1] if train_losses else None,
        "best_eval_loss": min(eval_losses) if eval_losses else None,
        "final_eval_loss": eval_losses[-1] if eval_losses else None,
        "global_steps": trainer.state.global_step,
        **gpu_memory_gb(),
        **extra,
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "train_metrics.json").write_text(json.dumps(metrics, indent=2))
    (out_dir / "log_history.json").write_text(json.dumps(history, indent=2))
    return metrics


class Timer:
    def __enter__(self):
        self.t0 = time.time()
        return self

    def __exit__(self, *exc):
        self.seconds = round(time.time() - self.t0, 1)
