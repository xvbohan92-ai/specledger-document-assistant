import pytest

from specledger.models import Correction, ProjectError, QueryFilter, QueryRequest


def import_case(ledger, case="dev-01"):
    return ledger.ingest(ledger.settings.data / "demo/pdfs" / f"{case}.pdf", source="self-authored test")[
        "document"
    ]


def test_only_approved_records_are_queryable(ledger):
    doc = import_case(ledger)
    request = QueryRequest(filters=[QueryFilter(field="power_w", raw_value="0.2 kW")])
    assert ledger.query(request)["count"] == 0
    approved = ledger.approve(doc["id"], doc["revision"])
    assert approved["status"] == "approved"
    row = ledger.query(request)["results"][0]
    assert row["power_w"] == 120
    assert row["evidence"]["mass_kg"]["evidence"]["page"] == 2
    assert row["source"] == "self-authored test"


def test_manual_correction_keeps_original_evidence_and_audit(ledger):
    doc = import_case(ledger, "dev-03")
    with pytest.raises(ProjectError, match="修订"):
        ledger.approve(doc["id"], doc["revision"])
    corrected = ledger.correct(
        doc["id"],
        "power_w",
        Correction(
            raw_value="150 W",
            page=1,
            reason="Demo reviewer confirms the intended value",
            revision=doc["revision"],
        ),
    )
    assert corrected["status"] == "ready"
    assert corrected["fields"]["power_w"]["origin"] == "manual"
    assert any(o["raw_value"] == "150 VA" and o["origin"] == "extracted" for o in corrected["observations"])
    assert corrected["audit"][-1]["action"] == "corrected"
    approved = ledger.approve(doc["id"], corrected["revision"])
    assert approved["revision"] == 3
    assert ledger.query(QueryRequest())["count"] == 1


def test_stale_revision_does_not_overwrite_a_review(ledger):
    doc = import_case(ledger, "dev-03")
    change = Correction(raw_value="150 W", page=1, reason="Review note", revision=1)
    ledger.correct(doc["id"], "power_w", change)
    with pytest.raises(ProjectError, match="版本"):
        ledger.correct(doc["id"], "power_w", change)
    with pytest.raises(ProjectError, match="版本"):
        ledger.approve(doc["id"], 1)
    assert ledger.query(QueryRequest())["count"] == 0


def test_duplicate_bytes_do_not_create_duplicate_records(ledger):
    first = import_case(ledger)
    result = ledger.ingest(ledger.settings.data / "demo/pdfs/dev-01.pdf", filename="another-name.pdf")
    assert result["duplicate"]
    assert result["document"]["id"] == first["id"]
    assert len(ledger.list_documents()) == 1
    assert len(result["document"]["audit"]) == 1


def test_failed_import_preserves_existing_database(ledger, tmp_path):
    doc = import_case(ledger)
    path = tmp_path / "bad.pdf"
    path.write_bytes(b"this is not a pdf")
    with pytest.raises(ProjectError):
        ledger.ingest(path)
    assert len(ledger.list_documents()) == 1
    assert ledger.get(doc["id"])["sha256"] == doc["sha256"]


def test_original_file_tampering_invalidates_evidence(ledger):
    doc = import_case(ledger)
    path = ledger.settings.data / doc["pdf_path"]
    path.write_bytes(path.read_bytes() + b"changed")
    with pytest.raises(ProjectError, match="指纹"):
        ledger.get(doc["id"])


def test_missing_and_injected_filter_values_cannot_become_sql(ledger):
    doc = import_case(ledger)
    ledger.approve(doc["id"], doc["revision"])
    with pytest.raises(ProjectError):
        ledger.query(QueryRequest(filters=[QueryFilter(field="ip_rating", op="=", raw_value="IP65' OR 1=1")]))
    with pytest.raises(ProjectError):
        ledger.query(QueryRequest(filters=[QueryFilter(field="model", op="<=", raw_value="ALP-100")]))
    assert ledger.query(QueryRequest())["count"] == 1


def test_approved_records_cannot_be_silently_edited(ledger):
    doc = import_case(ledger)
    approved = ledger.approve(doc["id"], doc["revision"])
    with pytest.raises(ProjectError, match="不可直接修订"):
        ledger.correct(
            doc["id"],
            "power_w",
            Correction(raw_value="100 W", page=1, reason="Changed", revision=approved["revision"]),
        )


def test_broken_parsed_cache_is_rebuilt(ledger):
    path = ledger.settings.data / "demo/pdfs/dev-01.pdf"
    ledger.parser.parse(path)
    cache = next((ledger.settings.data / "cache/parsed").glob("*.json"))
    cache.write_bytes(b"broken json")
    pages, hit = ledger.parser.parse(path)
    assert not hit
    assert len(pages) == 2
