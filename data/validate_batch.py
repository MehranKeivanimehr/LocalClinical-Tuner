#!/usr/bin/env python
"""Fast, model-free validation of externally generated batches (Claude app / Claude Code / any JSONL source).

For each response file it checks, per encounter:
  * format     - valid JSON line, required fields, SOAP/entity schema, JSON null (not "null") for missing doses;
  * ids        - every scenario of the batch present exactly once, no unknown ids;
  * note       - length within the global limits (and near the scenario's target), no SOAP headings, no identifier
                 patterns, stated age/sex consistent with the scenario;
  * labels     - summary length (3-6 sentences, <= 120 words), complete SOAP, every entity a verbatim span of
                 the note, no duplicates;
  * clinical   - the full rule set used by the quality filter (vital/lab ranges, max doses, allergy conflicts,
                 age/sex terms, medications in labels that are absent from the note, negation consistency).

Errors make the exit code non-zero; warnings are informational. Use it after every batch:

    uv run python data/validate_batch.py data/external/claude_app/responses/batch_007.jsonl
    uv run python data/validate_batch.py --all            # every response file + coverage summary
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from pydantic import ValidationError

sys.path.insert(0, str(Path(__file__).resolve().parent))
from import_external_notes import parse_file  # noqa: E402

from clinical_tuner.quality.grounding import NoteIndex, is_grounded  # noqa: E402
from clinical_tuner.quality.phi import find_phi  # noqa: E402
from clinical_tuner.quality.rules import run_rules  # noqa: E402
from clinical_tuner.schemas import SOAP, Entities  # noqa: E402
from clinical_tuner.text import normalize, sentences, word_count  # noqa: E402
from clinical_tuner.utils import PROJECT_ROOT, read_jsonl  # noqa: E402

DEFAULT_DIR = PROJECT_ROOT / "data" / "external" / "claude_app"
RULES = SimpleNamespace(max_major=1, min_entities=3, max_ungrounded_entity_frac=0.0, summary_words=(25, 130))
_SOAP_HEADING_RE = re.compile(r"^\s*(subjective|objective|assessment|plan)\s*[:\-]", re.IGNORECASE | re.MULTILINE)


def validate_record(obj: dict[str, Any], scen: dict[str, Any] | None) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    note = obj.get("note")
    if not isinstance(note, str) or not note.strip():
        return ["missing note"], []
    if "\\n" in note and "\n" not in note:
        errors.append("note contains literal '\\n' sequences (double-escaped line breaks)")
    n = word_count(note)
    if not 100 <= n <= 1000:
        errors.append(f"note has {n} words (allowed 100-1000)")
    elif scen:
        lo, hi = scen["target_words"]
        if n < lo * 0.75 or n > hi * 1.25:
            warnings.append(f"note has {n} words; scenario target {lo}-{hi}")
    if len({m.group(1).lower() for m in _SOAP_HEADING_RE.finditer(note)}) >= 2:
        errors.append("note uses SOAP headings (it must not)")
    if phi := find_phi(note):
        errors.append(f"identifier-like pattern(s) in note: {dict(phi)}")

    summary = obj.get("summary")
    if not isinstance(summary, str) or not summary.strip():
        errors.append("missing summary")
    else:
        sw, ns = word_count(summary), len(sentences(summary))
        if sw > 130 or sw < 25:
            errors.append(f"summary has {sw} words (must be <= 120)")
        elif sw > 120:
            warnings.append(f"summary has {sw} words (target <= 120)")
        if not 2 <= ns <= 7:
            warnings.append(f"summary has {ns} sentences (target 3-6)")
    try:
        soap = SOAP.model_validate(obj.get("soap"))
        if not soap.plan:
            errors.append("SOAP plan is empty")
        if normalize(soap.assessment) in {"", "not documented"}:
            errors.append("SOAP assessment is empty/not documented")
        if any(re.match(r"^\s*\d+[.)]\s", p) for p in soap.plan):
            warnings.append("SOAP plan items are numbered (they should not be)")
    except ValidationError as e:
        errors.append(f"SOAP schema: {str(e).splitlines()[0]}")
        soap = None
    try:
        ent = Entities.model_validate(obj.get("entities"))
    except ValidationError as e:
        errors.append(f"entities schema: {str(e).splitlines()[0]}")
        ent = None
    if ent is not None:
        for m in obj["entities"].get("medications", []):
            for k in ("dose", "frequency"):
                if isinstance(m.get(k), str) and m[k].strip().lower() in {"null", "none", "n/a", ""}:
                    warnings.append(f"medication {m.get('name')!r} {k} is the string {m[k]!r}; use JSON null")
        idx = NoteIndex(note)
        terms = ([("symptom", s.text) for s in ent.symptoms] + [("diagnosis", d) for d in ent.diagnoses]
                 + [("medication", m.name) for m in ent.medications] + [("procedure", p) for p in ent.procedures])
        ungrounded = [f"{k}:{t!r}" for k, t in terms if not is_grounded(t, idx)]
        if ungrounded:
            errors.append(f"entities not found verbatim in the note: {ungrounded}")
        dup = [t for t, c in Counter((k, normalize(t)) for k, t in terms).items() if c > 1]
        if dup:
            warnings.append(f"duplicate entities: {dup}")

    if soap is not None and ent is not None and isinstance(summary, str):
        record = {"source": "synthetic", "style": scen["style"] if scen else None, "note": note,
                  "patient": {"age": scen["age"], "sex": scen["sex"]} if scen else {},
                  "annotation": {"summary": summary, "soap": soap.model_dump(), "entities": ent.model_dump()}}
        res = run_rules(record, RULES)
        for f in res.findings:
            if f.check == "grounding" and "not found in note" in f.message:
                continue  # already reported above with the exact terms
            if f.check == "summary_format" and "words" in f.message:
                continue  # already reported above
            msg = f"rule {f.check} ({f.field}): {f.message}"
            if f.severity == "critical":
                errors.append(msg)
            elif f.severity == "major":
                warnings.append(msg)
    return errors, warnings


def validate_file(path: Path, scenarios: dict[str, dict], batch_of: dict[str, str]) -> dict[str, Any]:
    objs, bad = parse_file(path)
    expected = [i for i, b in batch_of.items() if b == path.stem]
    report: dict[str, Any] = {"file": path.name, "lines": len(objs), "unparseable_lines": bad, "errors": {},
                              "warnings": {}, "missing_ids": [], "unknown_ids": [], "duplicate_ids": []}
    seen: Counter = Counter()
    for obj in objs:
        eid = str(obj.get("encounter_id", "?"))
        seen[eid] += 1
        if scenarios and eid not in scenarios:
            report["unknown_ids"].append(eid)
            continue
        if expected and eid not in expected:
            report["warnings"].setdefault(eid, []).append(f"id belongs to {batch_of.get(eid)}, not {path.stem}")
        errs, warns = validate_record(obj, scenarios.get(eid))
        if errs:
            report["errors"][eid] = errs
        if warns:
            report["warnings"].setdefault(eid, []).extend(warns)
    report["duplicate_ids"] = [i for i, c in seen.items() if c > 1]
    report["missing_ids"] = [i for i in expected if i not in seen]
    report["ok"] = not (bad or report["errors"] or report["missing_ids"] or report["unknown_ids"]
                        or report["duplicate_ids"])
    return report


def print_report(r: dict[str, Any]) -> None:
    status = "OK" if r["ok"] else "FAIL"
    print(f"[{status}] {r['file']}: {r['lines']} records, {len(r['errors'])} with errors, "
          f"{r['unparseable_lines']} unparseable lines, missing {len(r['missing_ids'])}, "
          f"unknown {len(r['unknown_ids'])}, duplicates {len(r['duplicate_ids'])}")
    for k in ("missing_ids", "unknown_ids", "duplicate_ids"):
        if r[k]:
            print(f"   {k}: {r[k]}")
    for eid, errs in r["errors"].items():
        for e in errs:
            print(f"   ERROR {eid}: {e}")
    for eid, warns in r["warnings"].items():
        for w in warns:
            print(f"   warn  {eid}: {w}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("files", nargs="*", type=Path)
    parser.add_argument("--dir", type=Path, default=DEFAULT_DIR)
    parser.add_argument("--all", action="store_true", help="validate every file in <dir>/responses")
    parser.add_argument("--json", type=Path, default=None, help="also write the full report as JSON")
    parser.add_argument("--quiet", action="store_true", help="only print the per-file status lines")
    args = parser.parse_args()
    rows = read_jsonl(args.dir / "scenarios.jsonl") if (args.dir / "scenarios.jsonl").exists() else []
    scenarios = {r["encounter_id"]: r for r in rows}
    batch_of = {r["encounter_id"]: r.get("batch") for r in rows}
    files = args.files or (sorted((args.dir / "responses").glob("*.jsonl")) if args.all else [])
    if not files:
        parser.error("give response files or --all")
    reports = [validate_file(f, scenarios, batch_of) for f in files]
    for r in reports:
        if args.quiet:
            print(f"[{'OK' if r['ok'] else 'FAIL'}] {r['file']}: {r['lines']} records, {len(r['errors'])} with errors")
        else:
            print_report(r)
    if args.all:
        good = defaultdict(int)
        for r in reports:
            good["records"] += r["lines"]
            good["records_with_errors"] += len(r["errors"])
        answered = {f.stem for f in files}
        total_batches = len(set(batch_of.values()))
        print(f"\nSUMMARY: {len(reports)} files ({sum(r['ok'] for r in reports)} OK), {good['records']} records, "
              f"{good['records_with_errors']} with errors; {len(answered & set(batch_of.values()))}/{total_batches} "
              "batches answered")
    if args.json:
        args.json.write_text(json.dumps(reports, indent=2))
    sys.exit(0 if all(r["ok"] for r in reports) else 1)


if __name__ == "__main__":
    main()
