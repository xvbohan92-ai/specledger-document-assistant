"""Sealed test: identical pretrained/scored inputs; unit/state/page and readiness comparisons."""

import json
import statistics
import time
from decimal import Decimal

import torch
from common import ROOT, read, sha, verify_frozen
from runtime import evaluate, load_model

from specledger.field_mapper import FieldMapper
from specledger.fields import extract_fields, validate
from specledger.models import ParsedPage


def field_result(name, expected, fields, issues, parse_error):
    actual = fields.get(name)
    if "error" in expected:
        correct = actual is None and any(
            i["field"] == name and i["code"] == expected["error"] for i in issues
        )
        sourced = correct  # Unresolved states do not claim an extracted value or bounding box.
    else:
        correct = actual is not None and actual.get("signal") == expected["signal"]
        if correct:
            correct = (
                (Decimal(actual["exact_value"]) == Decimal(expected["exact_value"]))
                if "exact_value" in expected
                else actual["value"] == expected["value"]
            )
        sourced = correct and actual["evidence"]["page"] == expected["page"]
    return {
        "expected": expected,
        "actual": actual,
        "correct": bool(correct and not parse_error),
        "value_or_state_and_page_correct": bool(sourced and not parse_error),
    }


def main():
    verify_frozen()
    output = ROOT / "reports/finetuning-evaluation.json"
    if output.exists():
        raise ValueError("Do not overwrite frozen test outputs after inspecting them")
    trained = read("reports/finetuning-training.json")
    documents = read("training/datasets/documents.json")
    captured = read("reports/finetuning-ocr-evidence.json")
    assert captured["document_manifest_sha256"] == sha(ROOT / "training/datasets/documents.json")
    for case in documents["cases"]:
        assert sha(ROOT / case["path"]) == case["sha256"]
    torch.set_num_threads(2)
    components = {}
    for mode in ("base", "finetuned"):
        folder = ROOT / "data/field-models" / mode
        expected_sha = trained["base_weight_sha256"] if mode == "base" else trained["selected_weight_sha256"]
        assert sha(folder / "model.safetensors") == expected_sha
        model, tokenizer = load_model(folder)
        components[mode] = evaluate(model, tokenizer, "test")
        del model
    mappers = {mode: FieldMapper(ROOT / "data/field-models", mode) for mode in ("base", "finetuned")}
    ocr = {r["id"]: r for r in captured["records"]}
    records = []
    for case in documents["cases"]:
        if case["split"] != "test":
            continue
        source = ocr[case["id"]]
        pages = [ParsedPage.model_validate(page) for page in source["pages"]]
        assert source["pdf_sha256"] == case["sha256"]
        for mode in ("rules", "base", "finetuned"):
            started = time.perf_counter()
            observations = extract_fields(pages) if mode == "rules" else mappers[mode].extract(pages)
            fields, issues = validate(observations)
            slots = {
                name: field_result(name, target, fields, issues, source["error"])
                for name, target in case["expected"].items()
            }
            records.append(
                {
                    "id": case["id"],
                    "kind": case["kind"],
                    "layout": case["layout"],
                    "mode": mode,
                    "pdf_sha256": case["sha256"],
                    "field_slots": slots,
                    "field_correct": sum(r["correct"] for r in slots.values()),
                    "field_total": 7,
                    "source_bound_correct": sum(r["value_or_state_and_page_correct"] for r in slots.values()),
                    "issues": issues,
                    "expected_issues": case["issues"],
                    "ocr_error": source["error"],
                    "invalid_ready": bool(case["issues"]) and not issues and source["error"] is None,
                    "clean_blocked": not case["issues"] and (bool(issues) or source["error"] is not None),
                    "mapped_observations": sum(o.mapping is not None for o in observations),
                    "mapper_ms": round((time.perf_counter() - started) * 1000, 3),
                    "shared_parse_ms": source["parse_ms"],
                }
            )
    summary = {}
    for mode in ("rules", "base", "finetuned"):
        rows = [r for r in records if r["mode"] == mode]
        summary[mode] = {
            "documents": len(rows),
            "field_correct": sum(r["field_correct"] for r in rows),
            "field_total": 112,
            "source_bound_correct": sum(r["source_bound_correct"] for r in rows),
            "accuracy": sum(r["field_correct"] for r in rows) / 112,
            "all_fields_correct_documents": sum(r["field_correct"] == 7 for r in rows),
            "invalid_documents": sum(bool(r["expected_issues"]) for r in rows),
            "invalid_ready": sum(r["invalid_ready"] for r in rows),
            "clean_documents": sum(not r["expected_issues"] for r in rows),
            "clean_blocked": sum(r["clean_blocked"] for r in rows),
            "ocr_errors": sum(r["ocr_error"] is not None for r in rows),
            "p50_shared_parse_ms": statistics.median(r["shared_parse_ms"] for r in rows),
            "p50_mapping_ms": statistics.median(r["mapper_ms"] for r in rows),
            "digital_correct": sum(r["field_correct"] for r in rows if r["kind"] == "digital"),
            "scan_correct": sum(r["field_correct"] for r in rows if r["kind"] == "scan"),
        }
    paired = []
    for case in documents["cases"]:
        if case["split"] == "test":
            base = next(r for r in records if r["id"] == case["id"] and r["mode"] == "base")
            tuned = next(r for r in records if r["id"] == case["id"] and r["mode"] == "finetuned")
            paired.append(
                {
                    "id": case["id"],
                    "base_correct": base["field_correct"],
                    "finetuned_correct": tuned["field_correct"],
                    "delta": tuned["field_correct"] - base["field_correct"],
                }
            )
    report = {
        "training_sha256": sha(ROOT / "reports/finetuning-training.json"),
        "ocr_evidence_sha256": sha(ROOT / "reports/finetuning-ocr-evidence.json"),
        "document_manifest_sha256": sha(ROOT / "training/datasets/documents.json"),
        "source_sha256": sha(__file__),
        "components": components,
        "summary": summary,
        "records": records,
        "paired_documents": paired,
        "deployment_variant": trained["deployment_variant"],
        "limitations": [
            documents["scope"],
            trained["dataset"]["scope"],
            "Closed-class field mapping weights only; OCR/layout weights are unchanged.",
            "State correctness requires the expected error type, not just any blocked or missing value.",
            "Source-bound metric checks values/states and expected pages, not bbox accuracy.",
            "Readiness is not approval: no record is automatically approved or published to the query table.",
            "Mapping timing includes repeated-label caching. Shared OCR was measured once; no speedup claim.",
        ],
    }
    output.write_text(json.dumps(report, indent=2) + "\n")
    print(
        json.dumps(
            {
                "components": {
                    k: {x: v[x] for x in ["correct", "rows", "macro_f1", "distractor_false_accepts"]}
                    for k, v in components.items()
                },
                "summary": summary,
            }
        ),
        flush=True,
    )


if __name__ == "__main__":
    main()
