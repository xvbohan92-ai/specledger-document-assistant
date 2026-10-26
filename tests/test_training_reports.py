import hashlib
import json
from decimal import Decimal
from pathlib import Path

import pytest

from specledger.training_evidence import evidence

ROOT = Path(__file__).resolve().parents[1]


def read(name):
    return json.loads((ROOT / name).read_text())


def test_component_and_document_scores_recompute_independently_from_frozen_records():
    report = read("reports/finetuning-evaluation.json")
    for result in report["components"].values():
        rows = result["records"]
        assert len(rows) == 48
        assert result["correct"] == sum(r["predicted"] == r["field"] for r in rows)
        total_f1 = 0
        for name in read("specledger/field_descriptions.json"):
            tp = sum(r["predicted"] == name and r["field"] == name for r in rows)
            denom = sum(r["predicted"] == name for r in rows) + sum(r["field"] == name for r in rows)
            total_f1 += 2 * tp / denom if denom else 0
        assert result["macro_f1"] == pytest.approx(total_f1 / 8)
    for mode in ("rules", "base", "finetuned"):
        rows = [r for r in report["records"] if r["mode"] == mode]
        total = 0
        for row in rows:
            correct = 0
            for name, slot in row["field_slots"].items():
                target, actual = slot["expected"], slot["actual"]
                if "error" in target:
                    ok = actual is None and any(
                        i["field"] == name and i["code"] == target["error"] for i in row["issues"]
                    )
                elif actual is None:
                    ok = False
                else:
                    ok = actual["signal"] == target["signal"]
                    ok &= (
                        (Decimal(actual["exact_value"]) == Decimal(target["exact_value"]))
                        if "exact_value" in target
                        else actual["value"] == target["value"]
                    )
                ok = bool(ok and row["ocr_error"] is None)
                assert slot["correct"] == ok
                correct += ok
            assert correct == row["field_correct"]
            total += correct
        assert total == report["summary"][mode]["field_correct"]


def test_evidence_requires_matching_report_chain_and_current_ocr_identity():
    captured = read("reports/finetuning-ocr-evidence.json")
    assert evidence(captured["backend"])["available"]
    assert not evidence({"kind": "text", "model_manifest": None})["available"]
    values = read("docs/figures/field-finetuning-values.json")
    for key, name in (
        ("training", "finetuning-training.json"),
        ("evaluation", "finetuning-evaluation.json"),
        ("scope_diagnostics", "finetuning-scope-diagnostics.json"),
        ("ocr_evidence", "finetuning-ocr-evidence.json"),
    ):
        assert values[key + "_sha256"] == hashlib.sha256((ROOT / "reports" / name).read_bytes()).hexdigest()
