"""Freeze one actual OCR pass for a fair mapping-only comparison, without touching the ledger."""

import json
import time

from common import ROOT, read, sha, verify_frozen

from specledger.config import Settings
from specledger.parsing import Parser


def main():
    verify_frozen()
    manifest = read("training/datasets/documents.json")
    output = ROOT / "reports/finetuning-ocr-evidence.json"
    if output.exists():
        raise ValueError("A fixed OCR pass already exists; do not replace it after evaluating model outputs")
    assert sha(ROOT / "training/documents.py") == manifest["builder_sha256"]
    for case in manifest["cases"]:
        assert sha(ROOT / case["path"]) == case["sha256"]
    parser = Parser(Settings(ROOT / "data", backend="docling"))
    warm = next(case for case in manifest["cases"] if case["split"] == "valid" and case["kind"] == "digital")
    parser.parse(ROOT / warm["path"], cached=False)
    records = []
    for case in manifest["cases"]:
        started = time.perf_counter()
        try:
            pages, _ = parser.parse(ROOT / case["path"], cached=False)
            record = {
                "id": case["id"],
                "pdf_sha256": case["sha256"],
                "error": None,
                "pages": [page.model_dump() for page in pages],
            }
        except Exception as exc:
            record = {
                "id": case["id"],
                "pdf_sha256": case["sha256"],
                "error": f"{type(exc).__name__}: {str(exc)[:160]}",
                "pages": [],
            }
        record["parse_ms"] = round((time.perf_counter() - started) * 1000, 2)
        records.append(record)
        print(
            json.dumps({"id": case["id"], "parse_ms": record["parse_ms"], "error": record["error"]}),
            flush=True,
        )
    report = {
        "document_manifest_sha256": sha(ROOT / "training/datasets/documents.json"),
        "backend": parser.describe(),
        "source_sha256": sha(__file__),
        "model_manifest_sha256": sha(ROOT / "data/models/manifest.json"),
        "parser_source_sha256": sha(ROOT / "specledger/parsing.py"),
        "records": records,
        "startup_ms": parser.startup_ms,
        "scope": "Same actual uncached Docling/RapidOCR output reused by every mapper. "
        "One separate validation document warms models; no test-based label correction or retries.",
    }
    output.write_text(json.dumps(report, indent=2) + "\n")


if __name__ == "__main__":
    main()
