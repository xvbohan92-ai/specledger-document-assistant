"""Post-test engineering diagnostic. Never presented as a fresh held-out SFT score."""

import json

from common import ROOT, read, sha
from evaluate import field_result

from specledger.fields import extract_fields, validate
from specledger.mapping_scope import ScopedFieldMapper
from specledger.models import ParsedPage


def main():
    output = ROOT / "reports/finetuning-scope-diagnostics.json"
    if output.exists():
        raise ValueError("Preserve the diagnostic rather than overwrite it")
    cases = read("training/datasets/documents.json")
    evidence = {r["id"]: r for r in read("reports/finetuning-ocr-evidence.json")["records"]}
    mappers = {mode: ScopedFieldMapper(ROOT / "data/field-models", mode) for mode in ("base", "finetuned")}
    records = []
    for case in cases["cases"]:
        source = evidence[case["id"]]
        pages = [ParsedPage.model_validate(p) for p in source["pages"]]
        for mode in ("rules", "base", "finetuned"):
            observations = extract_fields(pages) if mode == "rules" else mappers[mode].extract(pages)
            fields, issues = validate(observations)
            slots = {
                name: field_result(name, expected, fields, issues, source["error"])
                for name, expected in case["expected"].items()
            }
            records.append(
                {
                    "id": case["id"],
                    "split": case["split"],
                    "mode": mode,
                    "field_correct": sum(r["correct"] for r in slots.values()),
                    "field_total": 7,
                    "slots": slots,
                    "issues": issues,
                    "invalid_ready": bool(case["issues"]) and not issues,
                    "clean_blocked": not case["issues"] and bool(issues),
                }
            )
    summary = {}
    for split in ("valid", "test"):
        summary[split] = {}
        for mode in ("rules", "base", "finetuned"):
            rows = [r for r in records if r["split"] == split and r["mode"] == mode]
            summary[split][mode] = {
                "documents": len(rows),
                "correct": sum(r["field_correct"] for r in rows),
                "total": sum(r["field_total"] for r in rows),
                "all_fields_correct": sum(r["field_correct"] == 7 for r in rows),
                "invalid_ready": sum(r["invalid_ready"] for r in rows),
                "clean_blocked": sum(r["clean_blocked"] for r in rows),
            }
    report = {
        "initial_test_report_sha256": sha(ROOT / "reports/finetuning-evaluation.json"),
        "scope_guard_sha256": sha(ROOT / "specledger/mapping_scope.py"),
        "summary": summary,
        "records": records,
        "scope": "Scope exclusions added after inspecting initial test failures, derived from the finite "
        "schema and validation distractors. Same guard applied to base and fine-tuned models. "
        "This is post-hoc engineering diagnosis on reused test data, not an independent held-out "
        "model gain, new training, or proof of broad safety/generalization.",
    }
    output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(summary), flush=True)


if __name__ == "__main__":
    main()
