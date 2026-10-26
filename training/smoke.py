"""Real OCR legacy regression plus isolated, unapproved deployment examples."""

import json
import time

from common import ROOT, read, sha

from specledger.config import Settings
from specledger.evaluation import equal
from specledger.fields import SCHEMA, validate
from specledger.mapping_scope import ScopedFieldMapper
from specledger.models import QueryRequest
from specledger.parsing import Parser
from specledger.store import Ledger


def main():
    output = ROOT / "reports/finetuning-live-verification.json"
    if output.exists():
        raise ValueError("Preserve the completed live verification")
    original_digest = (ROOT / "data/field-original-ledger.sha256").read_text()
    assert sha(ROOT / "data/ledger.sqlite") == original_digest
    parser = Parser(Settings(ROOT / "data", backend="docling"))
    mapper = ScopedFieldMapper(ROOT / "data/field-models", "finetuned")
    legacy = read("data/demo/manifest.json")
    results = []
    for case in legacy["cases"]:
        if case["split"] != "test":
            continue
        assert sha(ROOT / "data" / case["path"]) == case["sha256"]
        pages, _ = parser.parse(ROOT / "data" / case["path"], cached=False)
        fields, issues = validate(mapper.extract(pages))
        expected = dict(zip(SCHEMA, [case["model"]] + case["expected"], strict=True))
        wanted = {tuple(item) for item in case["issues"]}
        actual_issues = {(i["field"], i["code"]) for i in issues}
        correct = sum(
            (
                equal(fields.get(key, {}).get("value"), value)
                if value is not None
                else fields.get(key) is None
                and any((key, code) in actual_issues for field, code in wanted if field == key)
            )
            for key, value in expected.items()
        )
        results.append({"id": case["id"], "correct": correct, "total": 7})
        print(json.dumps(results[-1]), flush=True)
    data = ROOT / "data/finetuning-live"
    data.mkdir(exist_ok=True)
    models = data / "models"
    if not models.exists():
        models.symlink_to(ROOT / "data/models", target_is_directory=True)
    settings = Settings(
        data, backend="docling", field_mapper="finetuned", field_models=ROOT / "data/field-models"
    )
    ledger = Ledger(settings, parser)
    manifest = read("training/datasets/documents.json")
    live = []
    for case_id in ("valid-00", "valid-01"):
        case = next(c for c in manifest["cases"] if c["id"] == case_id)
        started = time.perf_counter()
        document = ledger.ingest(
            ROOT / case["path"], source="New self-authored validation fixture; not a manufacturer"
        )["document"]
        live.append(
            {
                "id": document["id"],
                "fixture": case_id,
                "fields": len(document["fields"]),
                "status": document["status"],
                "mapped_candidates": sum(o.get("mapping") is not None for o in document["observations"]),
                "elapsed_ms": round((time.perf_counter() - started) * 1000, 2),
            }
        )
    assert ledger.query(QueryRequest())["count"] == 0
    assert all(d["status"] != "approved" for d in ledger.list_documents())
    assert sha(ROOT / "data/ledger.sqlite") == original_digest
    report = {
        "backend": ledger.describe_backend(),
        "legacy": results,
        "legacy_correct": sum(r["correct"] for r in results),
        "legacy_total": 56,
        "live_documents": live,
        "pending_query_count": 0,
        "original_ledger_unchanged": True,
        "scope": "Actual Docling pass on original 8 test fixtures and two new validation PDFs. "
        "Isolated ledger, no automatic approvals, no change to the existing business database.",
    }
    output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report), flush=True)


if __name__ == "__main__":
    main()
