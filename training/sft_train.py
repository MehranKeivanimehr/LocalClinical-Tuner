#!/usr/bin/env python
"""Stage 1 - Supervised fine-tuning with Unsloth + QLoRA (optionally DoRA).

* Base model loaded in 4-bit (bitsandbytes NF4) through Unsloth's patched kernels.
* LoRA adapters on all attention + MLP projections.
* Loss on assistant tokens only (prompt masked via ``train_on_responses_only``); a sanity check aborts the run if
  the mask is wrong.
* Saves the LoRA adapter and (optionally) a merged 16-bit model that vLLM / llama.cpp can load directly.

Usage:
    uv run python training/sft_train.py                                  # full run from training/config.yaml
    uv run python training/sft_train.py --set sft.max_steps=20           # quick smoke test
"""

from __future__ import annotations

import argparse
import logging

import unsloth  # noqa: F401  # must be imported before transformers/trl so its patches apply
from datasets import Dataset
from trl import SFTConfig, SFTTrainer
from unsloth import FastLanguageModel, is_bfloat16_supported
from unsloth.chat_templates import train_on_responses_only

from clinical_tuner.config import add_config_args, load_config
from clinical_tuner.training_utils import (
    Timer,
    check_label_mask,
    n_tokens,
    render_chat,
    response_markers,
    save_metrics,
    warmup_steps,
)
from clinical_tuner.utils import read_jsonl, resolve, seed_everything, setup_logging

log = logging.getLogger("sft")


def build_dataset(rows, tokenizer, max_len: int) -> tuple[Dataset, int]:
    texts, dropped = [], 0
    for r in rows:
        text = render_chat(tokenizer, r["messages"], r["reference"])
        if n_tokens(tokenizer, text) > max_len:
            dropped += 1
            continue
        texts.append({"text": text, "reference": r["reference"]})
    return Dataset.from_list(texts), dropped


def main() -> None:
    parser = add_config_args(argparse.ArgumentParser(description=__doc__,
                                                     formatter_class=argparse.RawDescriptionHelpFormatter))
    parser.add_argument("--train-file", default=None, help="default: <processed_dir>/train.jsonl")
    parser.add_argument("--val-file", default=None, help="default: <processed_dir>/val.jsonl")
    args = parser.parse_args()
    setup_logging()
    cfg = load_config(args.config, args.overrides)
    seed_everything(cfg.project.seed)
    sc, mc, lc = cfg.sft, cfg.model, cfg.lora
    out_dir = resolve(sc.output_dir)
    proc = resolve(cfg.data.processed_dir)

    model, tokenizer = FastLanguageModel.from_pretrained(
        model_name=mc.base_model, max_seq_length=mc.max_seq_length, load_in_4bit=mc.load_in_4bit, dtype=None,
    )
    peft_kwargs = {"use_dora": True} if lc.use_dora else {}
    model = FastLanguageModel.get_peft_model(
        model, r=lc.r, lora_alpha=lc.alpha, lora_dropout=lc.dropout, target_modules=lc.target_modules,
        bias="none", use_gradient_checkpointing="unsloth", random_state=cfg.project.seed,
        use_rslora=lc.use_rslora, max_seq_length=mc.max_seq_length, **peft_kwargs,
    )

    train_rows = read_jsonl(args.train_file or proc / "train.jsonl")
    val_rows = read_jsonl(args.val_file or proc / "val.jsonl")
    train_ds, dropped_tr = build_dataset(train_rows, tokenizer, mc.max_seq_length)
    val_ds, dropped_va = build_dataset(val_rows, tokenizer, mc.max_seq_length)
    log.info("SFT data: %d train (%d dropped > %d tokens), %d val (%d dropped)", len(train_ds), dropped_tr,
             mc.max_seq_length, len(val_ds), dropped_va)

    bf16 = is_bfloat16_supported()
    trainer = SFTTrainer(
        model=model,
        processing_class=tokenizer,
        train_dataset=train_ds.remove_columns("reference"),
        eval_dataset=val_ds.remove_columns("reference"),
        args=SFTConfig(
            output_dir=str(out_dir / "checkpoints"),
            dataset_text_field="text",
            max_length=mc.max_seq_length,
            packing=False,
            per_device_train_batch_size=sc.per_device_batch_size,
            per_device_eval_batch_size=sc.per_device_batch_size,
            gradient_accumulation_steps=sc.gradient_accumulation_steps,
            num_train_epochs=sc.epochs,
            max_steps=sc.max_steps,
            learning_rate=sc.learning_rate,
            warmup_steps=warmup_steps(len(train_ds), sc.per_device_batch_size, sc.gradient_accumulation_steps,
                                      sc.epochs, sc.max_steps, sc.warmup_ratio),
            lr_scheduler_type=sc.lr_scheduler,
            weight_decay=sc.weight_decay,
            max_grad_norm=sc.max_grad_norm,
            optim=sc.optim,
            logging_steps=sc.logging_steps,
            eval_strategy="steps",
            eval_steps=sc.eval_steps,
            save_strategy="steps",
            save_steps=sc.eval_steps,
            save_total_limit=2,
            load_best_model_at_end=True,
            metric_for_best_model="eval_loss",
            greater_is_better=False,
            bf16=bf16,
            fp16=not bf16,
            seed=cfg.project.seed,
            report_to="none",
            dataset_num_proc=4,
        ),
    )
    markers = response_markers(tokenizer, mc.chat_template_markers)
    trainer = train_on_responses_only(trainer, instruction_part=markers["instruction"],
                                      response_part=markers["response"])
    check_label_mask(trainer, tokenizer, train_ds[0]["reference"])

    with Timer() as t:
        trainer.train()
    adapter_dir = out_dir / "adapter"
    model.save_pretrained(str(adapter_dir))
    tokenizer.save_pretrained(str(adapter_dir))
    metrics = save_metrics(out_dir, trainer, {
        "stage": "sft", "base_model": mc.base_model, "train_examples": len(train_ds), "val_examples": len(val_ds),
        "dropped_too_long": dropped_tr + dropped_va, "train_seconds": t.seconds, "lora": lc.model_dump(),
        "effective_batch_size": sc.per_device_batch_size * sc.gradient_accumulation_steps,
    })
    log.info("SFT done: %s", metrics)
    if sc.save_merged:
        merged = out_dir / "merged"
        log.info("Merging LoRA into 16-bit weights -> %s", merged)
        model.save_pretrained_merged(str(merged), tokenizer, save_method="merged_16bit")


if __name__ == "__main__":
    main()
