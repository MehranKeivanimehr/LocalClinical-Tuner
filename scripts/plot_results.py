#!/usr/bin/env python
"""Render results/figures/key_metrics_{light,dark}.png - one small multiple per headline metric (each panel has its
own scale, so there is never a dual axis), bars per model with 95% CI whiskers and direct value labels.

Palette: the validated reference categorical palette; every model has a fixed slot (base blue, SFT orange, SFT+ORPO
aqua, GGUF yellow, 2-shot magenta, RAG violet), so a model keeps its color in every panel and every figure. The
six colors in chart order pass the palette validator (lightness, chroma, adjacent CVD separation) in both modes.

Usage:
    uv run python scripts/plot_results.py
"""

from __future__ import annotations

import argparse

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

from clinical_tuner.config import add_config_args, load_config  # noqa: E402
from clinical_tuner.utils import read_json, resolve  # noqa: E402

MODELS = [  # (tag, axis label, palette slot) in chart order
    ("base", "Base", 0), ("base_fewshot", "Base\n2-shot", 4), ("base_rag", "Base\nRAG", 6),
    ("sft", "SFT", 1), ("orpo", "SFT+\nORPO", 2), ("gguf_q4", "Q4_K_M\nGGUF", 3),
]
THEMES = {  # slots: blue, orange, aqua, yellow, magenta, green, violet, red
    "light": {"surface": "#fcfcfb", "ink": "#0b0b0b", "muted": "#52514e", "grid": "#e4e3df",
              "series": ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#4a3aa7", "#e34948"]},
    "dark": {"surface": "#1a1a19", "ink": "#ffffff", "muted": "#c3c2b7", "grid": "#33332f",
             "series": ["#3987e5", "#d95926", "#199e70", "#c98500", "#d55181", "#008300", "#9085e9", "#e66767"]},
}
PANELS = [  # (task, metric key, title, percent?)
    ("summary", "rougeL", "Summary ROUGE-L", False),
    ("soap", "rougeL", "SOAP ROUGE-L", False),
    ("summary", "length_compliance", "Summary length <= 132 words (limit 120 + 10%)", True),
    ("entities", "entity_f1_relaxed", "Entity F1 (relaxed)", False),
    ("entities", "entity_f1_strict", "Entity F1 (strict)", False),
    ("judge", "overall", "LLM judge overall (1-5)", False),
]


def value(metrics: dict, model: str, task: str, key: str):
    if task == "judge":
        v = (metrics.get("judge") or {}).get("absolute", {}).get(model, {}).get(key)
    else:
        v = metrics["models"].get(model, {}).get("by_task", {}).get(task, {}).get(key)
    if isinstance(v, dict):
        return v["mean"], v.get("ci95")
    return v, None


def render(metrics: dict, theme: str, out) -> None:
    t = THEMES[theme]
    models = [(m, label, slot) for m, label, slot in MODELS if m in metrics["models"]]
    fig, axes = plt.subplots(2, 3, figsize=(13, 6.6), facecolor=t["surface"])
    for ax, (task, key, title, pct) in zip(axes.flat, PANELS, strict=True):
        ax.set_facecolor(t["surface"])
        xs, labels = [], []
        for i, (m, label, slot) in enumerate(models):
            v, ci = value(metrics, m, task, key)
            if v is None:
                continue
            scale = 100 if pct else 1
            ax.bar(i, v * scale, width=0.62, color=t["series"][slot], zorder=2,
                   edgecolor=t["surface"], linewidth=2)
            if ci and ci[0] is not None:
                ax.errorbar(i, v * scale, yerr=[[(v - ci[0]) * scale], [(ci[1] - v) * scale]], fmt="none",
                            ecolor=t["muted"], elinewidth=1.2, capsize=3, zorder=3)
            top = max(v, ci[1]) if ci and ci[1] is not None else v
            pad = 0.02 * (4.4 if task == "judge" else 110 if pct else 1.08)
            ax.text(i, top * scale + pad, f"{v * scale:.0f}%" if pct else f"{v:.2f}", ha="center", va="bottom",
                    color=t["ink"], fontsize=8.5, zorder=4)
            xs.append(i)
            labels.append(label)
        ax.set_xticks(xs, labels, color=t["muted"], fontsize=8.5)
        ax.set_title(title, color=t["ink"], loc="left", fontsize=11, pad=10)
        ax.tick_params(axis="y", colors=t["muted"], labelsize=8)
        ax.grid(axis="y", color=t["grid"], linewidth=0.8, zorder=0)
        ax.spines[["top", "right", "left"]].set_visible(False)
        ax.spines["bottom"].set_color(t["grid"])
        if task == "judge":
            ax.set_ylim(1, 5.6)
        elif pct:
            ax.set_ylim(0, 118)
        else:
            ax.set_ylim(0, 1.15)
    fig.suptitle("Base model, training-free baselines and fine-tuned models - held-out test set (95% bootstrap CIs; "
                 "judge not run on the baselines)", color=t["ink"], x=0.01,
                 ha="left", fontsize=12)
    fig.tight_layout(rect=(0, 0, 1, 0.95))
    fig.savefig(out, dpi=160, facecolor=t["surface"])
    plt.close(fig)


def main() -> None:
    parser = add_config_args(argparse.ArgumentParser(description=__doc__))
    args = parser.parse_args()
    cfg = load_config(args.config, args.overrides)
    rdir = resolve(cfg.project.results_dir)
    metrics = read_json(rdir / "metrics.json")
    (rdir / "figures").mkdir(parents=True, exist_ok=True)
    for theme in THEMES:
        render(metrics, theme, rdir / "figures" / f"key_metrics_{theme}.png")
    print("wrote", rdir / "figures")


if __name__ == "__main__":
    main()
