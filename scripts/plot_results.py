#!/usr/bin/env python
"""Render results/figures/key_metrics_{light,dark}.png - one small multiple per headline metric (each panel has its
own scale, so there is never a dual axis), bars per model with 95% CI whiskers and direct value labels.

Palette: the validated reference categorical order (blue, orange, aqua, yellow) - slot order is fixed per model, so a
model keeps its color in every panel and every figure.

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

MODELS = [("base", "Base"), ("sft", "SFT"), ("orpo", "SFT+ORPO"), ("gguf_q4", "SFT+ORPO\nQ4_K_M GGUF")]
THEMES = {
    "light": {"surface": "#fcfcfb", "ink": "#0b0b0b", "muted": "#52514e", "grid": "#e4e3df",
              "series": ["#2a78d6", "#eb6834", "#1baf7a", "#eda100"]},
    "dark": {"surface": "#1a1a19", "ink": "#ffffff", "muted": "#c3c2b7", "grid": "#33332f",
             "series": ["#3987e5", "#d95926", "#199e70", "#c98500"]},
}
PANELS = [  # (task, metric key, title, percent?)
    ("summary", "rougeL", "Summary ROUGE-L", False),
    ("soap", "rougeL", "SOAP ROUGE-L", False),
    ("soap", "soap_structure", "SOAP structure adherence", True),
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
    models = [(m, label) for m, label in MODELS if m in metrics["models"]]
    fig, axes = plt.subplots(2, 3, figsize=(12, 6.2), facecolor=t["surface"])
    for ax, (task, key, title, pct) in zip(axes.flat, PANELS, strict=True):
        ax.set_facecolor(t["surface"])
        xs, labels = [], []
        for i, (m, label) in enumerate(models):
            v, ci = value(metrics, m, task, key)
            if v is None:
                continue
            scale = 100 if pct else 1
            ax.bar(i, v * scale, width=0.56, color=t["series"][[x for x, _ in MODELS].index(m)], zorder=2,
                   edgecolor=t["surface"], linewidth=2)
            if ci and ci[0] is not None:
                ax.errorbar(i, v * scale, yerr=[[(v - ci[0]) * scale], [(ci[1] - v) * scale]], fmt="none",
                            ecolor=t["muted"], elinewidth=1.2, capsize=3, zorder=3)
            top = max(v, ci[1]) if ci and ci[1] is not None else v
            pad = 0.02 * (4.4 if task == "judge" else 110 if pct else 1.08)
            ax.text(i, top * scale + pad, f"{v * scale:.1f}%" if pct else f"{v:.2f}", ha="center", va="bottom",
                    color=t["ink"], fontsize=9, zorder=4)
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
    fig.suptitle("Base vs fine-tuned on the held-out test set (95% bootstrap CIs)", color=t["ink"], x=0.01,
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
