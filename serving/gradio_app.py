#!/usr/bin/env python
"""Local Gradio UI for the fine-tuned clinical documentation model.

Talks to any OpenAI-compatible endpoint on localhost (Ollama by default: http://127.0.0.1:11434/v1), builds prompts
with the exact training-time prompt builder (``clinical_tuner.prompts.build_messages``), streams the answer, and
highlights extracted entities in the note. Binds to 127.0.0.1, never creates a public share link, and disables
Gradio analytics: no clinical text leaves the machine.

Usage:
    uv run python serving/gradio_app.py                          # endpoint "ollama" from training/config.yaml
    uv run python serving/gradio_app.py --compare-endpoint base  # side-by-side with another local model
"""

from __future__ import annotations

import argparse
import os
import re
import urllib.request

os.environ.setdefault("GRADIO_ANALYTICS_ENABLED", "False")

import gradio as gr  # noqa: E402
from openai import OpenAI  # noqa: E402

from clinical_tuner.config import add_config_args, load_config  # noqa: E402
from clinical_tuner.metrics import parse_entities_output  # noqa: E402
from clinical_tuner.prompts import build_messages  # noqa: E402
from clinical_tuner.utils import PROJECT_ROOT, read_jsonl, resolve  # noqa: E402

TASKS = {"Summary": "summary", "SOAP note": "soap", "Entity extraction": "entities"}
COLORS = {"symptom": "#f59e0b", "negated symptom": "#9ca3af", "diagnosis": "#ef4444", "medication": "#3b82f6",
          "procedure/test": "#10b981"}


def load_examples(cfg, n: int = 8) -> dict[str, str]:
    """A few held-out *synthetic* notes (test split) to try the demo; falls back to the bundled examples file."""
    path = resolve(cfg.data.processed_dir) / "test.jsonl"
    if not path.exists():
        path = PROJECT_ROOT / "serving" / "examples.jsonl"
    if not path.exists():
        return {}
    seen, out = set(), {}
    for r in read_jsonl(path):
        if r.get("source", "synthetic") != "synthetic" or r["encounter_id"] in seen:
            continue
        seen.add(r["encounter_id"])
        label = f"{r.get('specialty') or 'clinic'} - {(r.get('style') or 'note').replace('_', ' ')} ({r['encounter_id']})"
        out[label] = r["note"]
        if len(out) >= n:
            break
    return out


def highlight(note: str, raw_json: str) -> list[tuple[str, str | None]]:
    ent = parse_entities_output(raw_json)
    if ent is None:
        return [(note, None)]
    terms: list[tuple[str, str]] = [(s.text, "negated symptom" if s.negated else "symptom") for s in ent.symptoms]
    terms += [(d, "diagnosis") for d in ent.diagnoses] + [(m.name, "medication") for m in ent.medications]
    terms += [(p, "procedure/test") for p in ent.procedures]
    spans: list[tuple[int, int, str]] = []
    for text, label in sorted(terms, key=lambda t: -len(t[0])):
        if not text.strip():
            continue
        for m in re.finditer(re.escape(text), note, re.IGNORECASE):
            if all(m.end() <= s or m.start() >= e for s, e, _ in spans):
                spans.append((m.start(), m.end(), label))
    spans.sort()
    out, pos = [], 0
    for s, e, label in spans:
        if s > pos:
            out.append((note[pos:s], None))
        out.append((note[s:e], label))
        pos = e
    if pos < len(note):
        out.append((note[pos:], None))
    return out


def endpoint_ok(base_url: str) -> bool:
    try:
        with urllib.request.urlopen(base_url.rstrip("/") + "/models", timeout=3) as r:
            return r.status == 200
    except Exception:  # noqa: BLE001
        return False


def build_app(cfg, endpoint: str, compare: str | None) -> gr.Blocks:
    primary = cfg.endpoint(endpoint)
    secondary = cfg.endpoint(compare) if compare else None
    clients = {"primary": OpenAI(base_url=primary.base_url, api_key="local")}
    if secondary:
        clients["secondary"] = OpenAI(base_url=secondary.base_url, api_key="local")
    examples = load_examples(cfg)
    ev = cfg.evaluation

    def stream(which: str, model: str, messages: list[dict[str, str]]):
        resp = clients[which].chat.completions.create(model=model, messages=messages, temperature=0.0,
                                                      max_tokens=ev.max_tokens, stream=True)
        text = ""
        for chunk in resp:
            if chunk.choices and chunk.choices[0].delta.content:
                text += chunk.choices[0].delta.content
                yield text

    def run(task_label: str, note: str):
        if not note.strip():
            raise gr.Error("Paste a clinical note or doctor-patient transcript first.")
        task = TASKS[task_label]
        messages = build_messages(task, note)
        out1, out2 = "", ""
        try:
            for out1 in stream("primary", primary.model, messages):
                yield out1, out2, gr.update(visible=False)
            if secondary:
                for out2 in stream("secondary", secondary.model, messages):
                    yield out1, out2, gr.update(visible=False)
        except Exception as e:  # noqa: BLE001
            raise gr.Error(f"Model endpoint error: {e}") from e
        if task == "entities":
            yield out1, out2, gr.update(value=highlight(note, out1), visible=True)
        else:
            yield out1, out2, gr.update(visible=False)

    status = "connected" if endpoint_ok(primary.base_url) else "NOT reachable - start it with scripts/run_demo.sh"
    with gr.Blocks(title="LocalClinical-Tuner", analytics_enabled=False) as demo:
        gr.Markdown(
            "# LocalClinical-Tuner - local clinical documentation assistant\n"
            f"Model `{primary.model}` at `{primary.base_url}` ({status}). Inference runs entirely on this machine: "
            "no note text is sent to any external service.\n\n"
            "> Research demo only - not a medical device. Outputs must be reviewed by a clinician.")
        with gr.Row():
            with gr.Column():
                example = gr.Dropdown(choices=list(examples), label="Load a synthetic example note", value=None)
                note = gr.Textbox(lines=20, max_lines=40, label="Clinical note or doctor-patient transcript")
                task = gr.Radio(list(TASKS), value="Summary", label="Task")
                btn = gr.Button("Generate", variant="primary")
            with gr.Column():
                out1 = gr.Textbox(lines=20, max_lines=60, label=f"Fine-tuned model ({primary.model})")
                out2 = gr.Textbox(lines=20, max_lines=60, label=f"Comparison ({secondary.model if secondary else '-'})",
                                  visible=secondary is not None)
                ents = gr.HighlightedText(label="Extracted entities highlighted in the note", color_map=COLORS,
                                          show_legend=True, combine_adjacent=False, visible=False)
        example.change(lambda k: examples.get(k, ""), inputs=example, outputs=note)
        btn.click(run, inputs=[task, note], outputs=[out1, out2, ents])
    return demo


def main() -> None:
    parser = add_config_args(argparse.ArgumentParser(description=__doc__,
                                                     formatter_class=argparse.RawDescriptionHelpFormatter))
    parser.add_argument("--endpoint", default=None, help="endpoint name in config (default: serving.endpoint)")
    parser.add_argument("--compare-endpoint", default=None, help="optional second endpoint for side-by-side")
    parser.add_argument("--host", default=None)
    parser.add_argument("--port", type=int, default=None)
    args = parser.parse_args()
    cfg = load_config(args.config, args.overrides)
    demo = build_app(cfg, args.endpoint or cfg.serving.endpoint, args.compare_endpoint or cfg.serving.compare_endpoint)
    demo.queue().launch(server_name=args.host or cfg.serving.gradio_host, server_port=args.port or cfg.serving.gradio_port,
                        share=False, show_error=True)


if __name__ == "__main__":
    main()
