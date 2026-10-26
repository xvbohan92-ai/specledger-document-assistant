"""Compare actual PDF text/OCR extraction against frozen, same-author labels."""

import json
import math
import re
import statistics
import time

from .config import Settings
from .fields import SCHEMA, extract_fields, validate
from .models import ProjectError
from .parsing import Parser, digest

NAMES = {"text": "PDF text + validation", "docling": "Docling OCR + validation"}


def equal(actual, expected):
    if isinstance(expected, (int, float)):
        return isinstance(actual, (int, float)) and math.isclose(actual, expected, rel_tol=1e-5, abs_tol=1e-5)
    return actual == expected


def evaluate(settings, *, split="test", output=None):
    manifest_path = settings.data / "demo" / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    cases = [c for c in manifest["cases"] if c["split"] == split]
    if split not in ("dev", "test") or not cases:
        raise ProjectError("评测划分无效或没有文档。")
    for case in manifest["cases"]:
        if digest(settings.data / case["path"]) != case["sha256"]:
            raise ProjectError("PDF 指纹与冻结标注不匹配，停止评测。")
    records, backends = [], {}
    for mode in ("text", "docling"):
        parser = Parser(Settings(settings.data, backend=mode))
        backends[mode] = parser.describe()
        # The separate dev document warms both the models and PDF parser; do not time model loading.
        warm = next(c for c in manifest["cases"] if c["split"] == "dev" and c["kind"] == "digital")
        parser.parse(settings.data / warm["path"], cached=False)
        for case in cases:
            started = time.perf_counter()
            error = None
            try:
                pages, _ = parser.parse(settings.data / case["path"], cached=False)
                observations = extract_fields(pages)
                fields, issues = validate(observations)
            except Exception as exc:
                observations, fields, issues = [], {}, []
                error = f"{type(exc).__name__}: {exc}"
            elapsed = (time.perf_counter() - started) * 1000
            expected = dict(zip(SCHEMA, [case["model"]] + case["expected"], strict=True))
            expected_issues = {tuple(issue) for issue in case["issues"]}
            actual_issues = {(issue["field"], issue["code"]) for issue in issues}
            correct, page_correct, mistakes = 0, 0, []
            naive_correct, numeric_total = 0, 0
            for index, (name, target) in enumerate(expected.items()):
                actual = fields.get(name, {}).get("value")
                field_ok = equal(actual, target)
                if target is None:
                    field_ok = actual is None and any(
                        (name, code) in actual_issues for field, code in expected_issues if field == name
                    )
                correct += field_ok and error is None
                if not field_ok or error:
                    mistakes.append({"field": name, "expected": target, "actual": actual})
                candidates = [o for o in observations if o.name == name]
                page_correct += any(o.evidence.page == (1 if index < 4 else 2) for o in candidates)
                if isinstance(target, (int, float)):
                    numeric_total += 1
                    first = re.search(r"[+-]?\d+(?:\.\d+)?", candidates[0].raw_value) if candidates else None
                    naive_correct += bool(first and equal(float(first[0]), target))
            records.append(
                {
                    "case_id": case["id"],
                    "kind": case["kind"],
                    "pipeline": mode,
                    "pages": 2,
                    "field_correct": correct,
                    "field_total": len(SCHEMA),
                    "page_correct": page_correct,
                    "expected_issues": sorted(expected_issues),
                    "actual_issues": sorted(actual_issues),
                    "bad_document_passed": bool(expected_issues) and not issues and error is None,
                    "clean_document_blocked": not expected_issues and (bool(issues) or error is not None),
                    "latency_ms": round(elapsed, 2),
                    "error": error,
                    "mistakes": mistakes,
                    "naive_numeric_correct": naive_correct,
                    "numeric_total": numeric_total,
                    "normalized_numeric_correct": sum(
                        equal(fields.get(k, {}).get("value"), v)
                        for k, v in expected.items()
                        if isinstance(v, (int, float))
                    ),
                }
            )
    summary = {}
    for mode in NAMES:
        rows = [r for r in records if r["pipeline"] == mode]
        latency = sorted(r["latency_ms"] for r in rows)
        total = sum(r["field_total"] for r in rows)
        summary[mode] = {
            "documents": len(rows),
            "pages": sum(r["pages"] for r in rows),
            "field_correct": sum(r["field_correct"] for r in rows),
            "field_total": total,
            "field_accuracy": sum(r["field_correct"] for r in rows) / total,
            "page_attribution_recall": sum(r["page_correct"] for r in rows) / total,
            "digital_accuracy": sum(r["field_correct"] for r in rows if r["kind"] == "digital")
            / sum(r["field_total"] for r in rows if r["kind"] == "digital"),
            "scan_accuracy": sum(r["field_correct"] for r in rows if r["kind"] == "scan")
            / sum(r["field_total"] for r in rows if r["kind"] == "scan"),
            "invalid_documents": sum(bool(r["expected_issues"]) for r in rows),
            "invalid_passed": sum(r["bad_document_passed"] for r in rows),
            "clean_documents": sum(not r["expected_issues"] for r in rows),
            "clean_blocked": sum(r["clean_document_blocked"] for r in rows),
            "errors": sum(r["error"] is not None for r in rows),
            "p50_ms": round(statistics.median(latency), 2),
            "p95_ms": latency[math.ceil(len(rows) * 0.95) - 1],
            "numeric_total": sum(r["numeric_total"] for r in rows),
            "naive_numeric_correct": sum(r["naive_numeric_correct"] for r in rows),
            "normalized_numeric_correct": sum(r["normalized_numeric_correct"] for r in rows),
        }
    report = {
        "dataset": manifest["name"],
        "scope": manifest["scope"],
        "split": split,
        "manifest_sha256": digest(manifest_path),
        "backend": backends[settings.backend],
        "backends": backends,
        "summary": summary,
        "records": records,
        "limits": [
            "12 self-authored 2-page PDFs: 4 dev and 8 test, each split balanced digital/scanned.",
            "Labels and layouts share the same author; no independent real-world accuracy claim.",
            "Field accuracy scores canonical values AND correct unresolved-field states; "
            "it is not OCR character accuracy.",
            "Page attribution checks the expected page, not bounding-box precision.",
            "Latency is warm parsing plus field rules, cache disabled; model initialization excluded.",
            "Numeric ablation deliberately takes the first number without unit conversion; "
            "it is not another OCR model.",
        ],
    }
    if output:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2))
    return report
