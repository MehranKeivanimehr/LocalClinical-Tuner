#!/usr/bin/env python
"""Stage 2 - Preference optimization with ORPO (default) or DPO, on top of the SFT model, with Unsloth + QLoRA.

* Starts from the merged SFT model (``outputs/sft/merged``) loaded in 4-bit, with a fresh LoRA adapter.
* ORPO (Hong et al., 2024) adds an odds-ratio penalty to the NLL of the chosen answer: no reference model, no
  reward model, no PPO. ``method: dpo`` switches to DPO (reference = the same weights with the adapter disabled).
* Pairs come from ``scripts/build_preference_data.py`` (on-policy SFT failures + targeted hard negatives).
* Prompts are rendered with the model's chat template exactly as in SFT/inference; pairs that would not fit in
  ``model.max_seq_length`` are dropped up front (TRL would otherwise truncate them silently).
* ORPO's NLL term is computed on the chosen *response* only (``orpo.nll_on_prompt: false``, see
  ``response_only_orpo``). TRL computes it over prompt + response, which here means mostly the input clinical note.

Usage:
    uv run python training/orpo_train.py
    uv run python training/orpo_train.py --set orpo.method=dpo --set orpo.beta=0.1
"""

from __future__ import annotations

import argparse
import logging

import unsloth  # noqa: F401  # import first so Unsloth patches transformers/TRL
from datasets import Dataset
from unsloth import FastLanguageModel, is_bfloat16_supported

from clinical_tuner.config import add_config_args, load_config
from clinical_tuner.training_utils import Timer, n_tokens, render_chat, save_metrics, warmup_steps
from clinical_tuner.utils import read_jsonl, resolve, seed_everything, setup_logging

log = logging.getLogger("orpo")


def response_only_orpo(trainer_cls):
    """Subclass TRL's ORPOTrainer so the NLL term covers the chosen response tokens only.

    TRL computes ORPO's NLL over the full prompt + response ("orpo chosen nll loss is computed over the full prompt
    and response"). Our prompts are long clinical notes, so most of that term trains the model to reproduce its
    *input*, and the logged ``nll_loss`` (~1.7 at the start of training) is dominated by prompt tokens. The ORPO
    paper defines L_SFT as -log P(y_w | x), and the SFT stage here trains on responses only, so the NLL is computed
    on response tokens. The length-normalised log-probabilities used by the odds-ratio term are unchanged (TRL
    already masks the prompt there); both come from one log-softmax pass, which also avoids TRL's extra full-vocab
    cross-entropy copy and lowers peak memory.
    """
    import torch
    from trl.trainer.utils import selective_log_softmax

    class ResponseOnlyORPOTrainer(trainer_cls):
        def concatenated_forward(self, model, batch):
            if self.is_encoder_decoder:
                return super().concatenated_forward(model, batch)
            cb = self.concatenated_inputs(batch, is_encoder_decoder=False, padding_value=self.padding_value,
                                          device=self.accelerator.device)
            n = batch["chosen_labels"].shape[0]
            kwargs = {"output_router_logits": True} if self.aux_loss_enabled else {}
            out = model(cb["concatenated_input_ids"], attention_mask=cb["concatenated_attention_mask"],
                        use_cache=False, **kwargs)
            logits = out.logits[:, :-1, :]
            labels = cb["concatenated_labels"][:, 1:]
            mask = labels != -100  # prompt and padding positions carry -100
            tok = selective_log_softmax(logits, torch.where(mask, labels, 0)) * mask
            logps = tok.sum(-1) / mask.sum(-1)  # = get_batch_logps(average_log_prob=True)
            nll = -tok[:n].sum() / mask[:n].sum()  # token-level mean over the chosen responses
            res = (logps[:n], logps[n:], logits[:n], logits[n:], nll)
            return (*res, out.aux_loss) if self.aux_loss_enabled else res

    return ResponseOnlyORPOTrainer


def build_pairs(rows, tokenizer, max_len: int) -> tuple[Dataset, int]:
    eos = tokenizer.eos_token
    out, dropped = [], 0
    for r in rows:
        prompt = render_chat(tokenizer, r["messages"])
        chosen, rejected = r["chosen"].strip() + eos, r["rejected"].strip() + eos
        longest = max(n_tokens(tokenizer, prompt + chosen), n_tokens(tokenizer, prompt + rejected))
        if longest > max_len:
            dropped += 1
            continue
        out.append({"prompt": prompt, "chosen": chosen, "rejected": rejected})
    return Dataset.from_list(out), dropped


def main() -> None:
    parser = add_config_args(argparse.ArgumentParser(description=__doc__,
                                                     formatter_class=argparse.RawDescriptionHelpFormatter))
    parser.add_argument("--init-model", default=None, help="default: <sft.output_dir>/merged")
    args = parser.parse_args()
    setup_logging()
    cfg = load_config(args.config, args.overrides)
    seed_everything(cfg.project.seed)
    oc, mc, lc = cfg.orpo, cfg.model, cfg.lora
    out_dir = resolve(oc.output_dir)
    proc = resolve(cfg.data.processed_dir)
    init = args.init_model or str(resolve(cfg.sft.output_dir) / "merged")

    model, tokenizer = FastLanguageModel.from_pretrained(model_name=init, max_seq_length=mc.max_seq_length,
                                                         load_in_4bit=mc.load_in_4bit, dtype=None)
    peft_kwargs = {"use_dora": True} if lc.use_dora else {}
    model = FastLanguageModel.get_peft_model(
        model, r=lc.r, lora_alpha=lc.alpha, lora_dropout=lc.dropout, target_modules=lc.target_modules,
        bias="none", use_gradient_checkpointing="unsloth", random_state=cfg.project.seed,
        use_rslora=lc.use_rslora, max_seq_length=mc.max_seq_length, **peft_kwargs,
    )

    train_ds, d1 = build_pairs(read_jsonl(proc / "pref_pairs_train.jsonl"), tokenizer, mc.max_seq_length)
    val_ds, d2 = build_pairs(read_jsonl(proc / "pref_pairs_val.jsonl"), tokenizer, mc.max_seq_length)
    log.info("Preference pairs: %d train, %d val (%d dropped as too long)", len(train_ds), len(val_ds), d1 + d2)

    bf16 = is_bfloat16_supported()
    common = dict(
        output_dir=str(out_dir / "checkpoints"), beta=oc.beta, max_length=mc.max_seq_length,
        per_device_train_batch_size=oc.per_device_batch_size, per_device_eval_batch_size=oc.per_device_batch_size,
        gradient_accumulation_steps=oc.gradient_accumulation_steps, num_train_epochs=oc.epochs,
        max_steps=oc.max_steps, learning_rate=oc.learning_rate,
        warmup_steps=warmup_steps(len(train_ds), oc.per_device_batch_size, oc.gradient_accumulation_steps,
                                  oc.epochs, oc.max_steps, oc.warmup_ratio),
        lr_scheduler_type=oc.lr_scheduler, weight_decay=oc.weight_decay, max_grad_norm=oc.max_grad_norm,
        optim=oc.optim, logging_steps=oc.logging_steps, eval_strategy="steps", eval_steps=oc.eval_steps,
        save_strategy="steps", save_steps=oc.eval_steps, save_total_limit=2, bf16=bf16, fp16=not bf16,
        seed=cfg.project.seed, report_to="none", dataset_num_proc=4,
    )
    if oc.method == "orpo":
        from trl.experimental.orpo import ORPOConfig, ORPOTrainer

        trainer_cls = ORPOTrainer if oc.nll_on_prompt else response_only_orpo(ORPOTrainer)
        trainer = trainer_cls(model=model, args=ORPOConfig(**common), train_dataset=train_ds, eval_dataset=val_ds,
                              processing_class=tokenizer)
    else:
        from trl import DPOConfig, DPOTrainer

        trainer = DPOTrainer(model=model, ref_model=None, args=DPOConfig(**common), train_dataset=train_ds,
                             eval_dataset=val_ds, processing_class=tokenizer)

    with Timer() as t:
        trainer.train()
    adapter_dir = out_dir / "adapter"
    model.save_pretrained(str(adapter_dir))
    tokenizer.save_pretrained(str(adapter_dir))
    hist = trainer.state.log_history
    evals = [h for h in hist if "eval_rewards/accuracies" in h]
    metrics = save_metrics(out_dir, trainer, {
        "stage": oc.method, "init_model": init, "train_pairs": len(train_ds), "val_pairs": len(val_ds),
        "dropped_too_long": d1 + d2, "train_seconds": t.seconds, "beta": oc.beta,
        "learning_rate": oc.learning_rate, "nll_on_prompt": oc.nll_on_prompt,
        "final_eval_reward_accuracy": evals[-1]["eval_rewards/accuracies"] if evals else None,
        "final_eval_margin": evals[-1].get("eval_rewards/margins") if evals else None,
    })
    log.info("%s done: %s", oc.method.upper(), metrics)
    if oc.save_merged:
        merged = out_dir / "merged"
        log.info("Merging LoRA into 16-bit weights -> %s", merged)
        model.save_pretrained_merged(str(merged), tokenizer, save_method="merged_16bit")


if __name__ == "__main__":
    main()
