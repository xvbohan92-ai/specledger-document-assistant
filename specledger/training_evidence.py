"""Historical experiment evidence with provenance checks; no model calls on page load."""

import json

from .config import ROOT
from .parsing import digest


def evidence(backend=None):
    try:
        files = {
            "training": "finetuning-training.json",
            "evaluation": "finetuning-evaluation.json",
            "scope_diagnostics": "finetuning-scope-diagnostics.json",
            "ocr_evidence": "finetuning-ocr-evidence.json",
        }
        reports = {key: json.loads((ROOT / "reports" / name).read_text()) for key, name in files.items()}
        values = json.loads((ROOT / "docs/figures/field-finetuning-values.json").read_text())
        if any(values[key + "_sha256"] != digest(ROOT / "reports" / name) for key, name in files.items()):
            return {"available": False, "reason": "图表与源实验报告不匹配。"}
        evaluated, trained, scoped = reports["evaluation"], reports["training"], reports["scope_diagnostics"]
        if (
            evaluated["training_sha256"] != values["training_sha256"]
            or scoped["initial_test_report_sha256"] != values["evaluation_sha256"]
        ):
            return {"available": False, "reason": "训练、评测和追加诊断的版本链不一致。"}
        if scoped["scope_guard_sha256"] != digest(ROOT / "specledger/mapping_scope.py"):
            return {"available": False, "reason": "范围检查规则已变化；追加诊断不能继续沿用。"}
        if backend and backend.get("model_manifest") != reports["ocr_evidence"]["backend"]["model_manifest"]:
            return {"available": False, "reason": "当前 OCR 后端与固定实验不同。"}
        return {
            "available": True,
            "components": values["component_summary"],
            "raw": values["raw_summary"],
            "scope_diagnostic": scoped["summary"]["test"],
            "rows": trained["dataset"]["rows"],
            "parameters": trained["parameters"],
            "selected_epoch": trained["selected"]["epoch"],
            "raw_delta_pp": values["raw_field_delta_pp"],
            "diagnostic_note": scoped["scope"],
            "limits": evaluated["limitations"],
            "active_mapper": (backend or {}).get("field_mapper", {}).get("variant", "rules"),
        }
    except (KeyError, ValueError, TypeError, OSError):
        return {"available": False, "reason": "尚无完整、匹配的微调实验记录。"}
