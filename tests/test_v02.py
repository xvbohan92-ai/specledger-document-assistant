import pytest

from specledger.fields import normalize
from specledger.models import Correction, ProjectError
from specledger.parsing import Parser
from specledger.store import Ledger


def query_for(ledger, field, raw, op="="):
    from specledger.models import QueryFilter, QueryRequest

    return ledger.query(QueryRequest(filters=[QueryFilter(field=field, raw_value=raw, op=op)]))["results"]


def ingest(ledger, name="dev-01"):
    return ledger.ingest(ledger.settings.data / f"demo/pdfs/{name}.pdf", source="regression fixture")[
        "document"
    ]


def db_state(ledger, document_id):
    with ledger.connect() as connection:
        row = dict(
            connection.execute("SELECT status,revision FROM documents WHERE id=?", (document_id,)).fetchone()
        )
        row["records"] = connection.execute("SELECT COUNT(*) FROM records").fetchone()[0]
        row["audit"] = connection.execute("SELECT COUNT(*) FROM audit").fetchone()[0]
    return row


def test_changed_source_approval_cannot_commit(ledger):
    doc = ingest(ledger)
    original = ledger.settings.data / doc["pdf_path"]
    original.write_bytes(original.read_bytes() + b"changed")
    with pytest.raises(ProjectError, match="指纹"):
        ledger.approve(doc["id"], doc["revision"])
    assert db_state(ledger, doc["id"]) == {"status": "ready", "revision": 1, "records": 0, "audit": 1}


def test_changed_source_correction_cannot_commit(ledger):
    doc = ingest(ledger, "dev-03")
    original = ledger.settings.data / doc["pdf_path"]
    original.write_bytes(original.read_bytes() + b"changed")
    with pytest.raises(ProjectError, match="指纹"):
        ledger.correct(
            doc["id"],
            "power_w",
            Correction(raw_value="150 W", reason="test review", page=1, revision=doc["revision"]),
        )
    assert db_state(ledger, doc["id"]) == {"status": "needs_review", "revision": 1, "records": 0, "audit": 1}


def test_incompatible_signal_suffix_is_rejected():
    assert normalize("mass_kg", "1 kg DC")[1] is not None
    assert normalize("max_temp_c", "60 C AC")[1] is not None


def test_small_positive_quantity_does_not_become_zero():
    value, error = normalize("mass_kg", "0.0001 g")
    assert error is None
    assert value == pytest.approx(1e-7, abs=1e-15)


def test_reparse_does_not_reuse_the_previous_parser_result(ledger):
    doc = ingest(ledger, "dev-02")
    assert doc["fields"] == {}

    class CountingParser(Parser):
        def __init__(self, settings):
            super().__init__(settings)
            self.calls = []

        def parse(self, path, *, cached=True):
            self.calls.append(cached)
            return super().parse(path, cached=cached)

    parser = CountingParser(ledger.settings)
    upgraded = Ledger(ledger.settings, parser)
    current = upgraded.reparse(doc["id"], doc["revision"], "Retry with current parser")
    assert parser.calls == [False]
    assert current["revision"] == 2
    assert upgraded.get_version(doc["id"], 1)["revision"] == 1


def test_reparse_preserves_approved_history_but_requires_new_approval(ledger):
    from specledger.models import QueryRequest

    doc = ingest(ledger)
    approved = ledger.approve(doc["id"], doc["revision"])
    current = ledger.reparse(doc["id"], approved["revision"], "Rebuild the extracted fields")
    assert current["status"] == "ready"
    assert ledger.query(QueryRequest())["count"] == 0
    historic = ledger.get_version(doc["id"], approved["revision"])
    assert historic["status"] == "approved"
    assert historic["fields"] == approved["fields"]
    assert ledger.get(doc["id"])["audit"][-1]["action"] == "reprocessed"


def test_reparse_failure_keeps_current_data_and_approval(ledger, monkeypatch):
    doc = ingest(ledger)
    approved = ledger.approve(doc["id"], doc["revision"])
    before = ledger.get(doc["id"])

    def fail(*args, **kwargs):
        raise ProjectError("simulated parser failure")

    monkeypatch.setattr(ledger.parser, "parse", fail)
    with pytest.raises(ProjectError, match="parser failure"):
        ledger.reparse(doc["id"], approved["revision"], "Try a new extraction")
    assert ledger.get(doc["id"]) == before


def test_stale_reparse_does_not_overwrite_a_concurrent_review(ledger, monkeypatch):
    doc = ingest(ledger)
    parse = ledger.parser.parse

    def concurrent(path, *, cached):
        result = parse(path, cached=cached)
        ledger.correct(
            doc["id"],
            "mass_kg",
            Correction(raw_value="1.3 kg", page=2, reason="Concurrent reviewer update", revision=1),
        )
        return result

    monkeypatch.setattr(ledger.parser, "parse", concurrent)
    with pytest.raises(ProjectError, match="版本"):
        ledger.reparse(doc["id"], 1, "Retry original file")
    assert ledger.get(doc["id"])["fields"]["mass_kg"]["value"] == 1.3


def test_approval_write_failure_rolls_back_all_tables(ledger, monkeypatch):
    doc = ingest(ledger)

    def fail(*args, **kwargs):
        raise ProjectError("simulated snapshot failure")

    monkeypatch.setattr(ledger, "_snapshot", fail)
    with pytest.raises(ProjectError, match="snapshot failure"):
        ledger.approve(doc["id"], 1)
    assert db_state(ledger, doc["id"]) == {"status": "ready", "revision": 1, "records": 0, "audit": 1}


def test_ac_dc_are_preserved_and_filtered_separately(ledger):
    ac = ingest(ledger)
    ac = ledger.correct(
        ac["id"],
        "voltage_v",
        Correction(raw_value="24 V AC", page=1, reason="Signal attribute test", revision=ac["revision"]),
    )
    ledger.approve(ac["id"], ac["revision"])
    dc = ingest(ledger, "test-01")
    ledger.approve(dc["id"], dc["revision"])
    assert [r["model"] for r in query_for(ledger, "voltage_v", "24 V AC")] == ["ALP-100"]
    assert [r["model"] for r in query_for(ledger, "voltage_v", "24 V DC")] == ["BETA-101"]
    assert len(query_for(ledger, "voltage_v", "24 V")) == 2
    field = ledger.get(dc["id"])["fields"]["voltage_v"]
    assert field["signal"] == "DC"
    assert field["exact_value"] == "24"


def test_signal_conflict_is_not_hidden_by_numeric_equality(ledger):
    from specledger.fields import validate
    from specledger.models import Observation

    doc = ingest(ledger)
    observations = [Observation.model_validate(o) for o in doc["observations"]]
    voltage = next(o for o in observations if o.name == "voltage_v")
    observations = [o for o in observations if o.name != "voltage_v"]
    observations += [voltage.model_copy(update={"raw_value": raw}) for raw in ("24 V AC", "24 V DC")]
    fields, issues = validate(observations)
    assert "voltage_v" not in fields
    assert any(i["field"] == "voltage_v" and i["code"] == "conflicting_values" for i in issues)


@pytest.mark.parametrize(
    "raw,exact", [("0.0001 g", "0.0000001"), ("1.000000000000000001 kg", "1.000000000000000001")]
)
def test_decimal_value_survives_review_storage_and_query(ledger, raw, exact):
    doc = ingest(ledger)
    doc = ledger.correct(
        doc["id"], "mass_kg", Correction(raw_value=raw, page=2, reason="Precision test", revision=1)
    )
    ledger.approve(doc["id"], doc["revision"])
    assert query_for(ledger, "mass_kg", raw)[0]["evidence"]["mass_kg"]["exact_value"] == exact
    if raw.startswith("1."):
        assert query_for(ledger, "mass_kg", "1.000000000000000002 kg", ">=") == []


def test_reparse_archives_manual_changes_without_reapplying_them(ledger):
    doc = ingest(ledger, "dev-03")
    corrected = ledger.correct(
        doc["id"], "power_w", Correction(raw_value="150 W", page=1, reason="Manual review test", revision=1)
    )
    result = ledger.reparse(doc["id"], corrected["revision"], "Start a fresh parse")
    assert result["status"] == "needs_review"
    assert all(o["origin"] == "extracted" for o in result["observations"])
    assert ledger.get_version(doc["id"], corrected["revision"])["fields"]["power_w"]["origin"] == "manual"


@pytest.mark.parametrize("case,retained", [("dev-01", True), ("test-01", False)])
def test_legacy_schema_upgrade_preserves_history_and_rechecks_semantics(ledger, case, retained):
    import json

    doc = ingest(ledger, case)
    approved = ledger.approve(doc["id"], 1)
    # Reproduce the actual v0.1 schema and stored payload, including lost AC/DC attributes.
    observations = approved["observations"]
    fields = approved["fields"]
    for observation in observations:
        observation.pop("signal", None)
        observation.pop("exact_value", None)
    for field in fields.values():
        field.pop("signal", None)
        field.pop("exact_value", None)
    with ledger.connect() as connection:
        connection.execute(
            "UPDATE documents SET observations=?,fields=? WHERE id=?",
            (json.dumps(observations), json.dumps(fields), doc["id"]),
        )
        connection.execute("DELETE FROM document_versions")
        connection.execute("ALTER TABLE documents DROP COLUMN rules_version")
        connection.execute("ALTER TABLE records DROP COLUMN voltage_kind")
        connection.execute("ALTER TABLE records DROP COLUMN current_kind")
    upgraded = Ledger(ledger.settings, Parser(ledger.settings))
    current = upgraded.get(doc["id"])
    assert current["revision"] == 3
    assert current["status"] == ("approved" if retained else "ready")
    assert upgraded.get_version(doc["id"], 2)["status"] == "approved"
    assert len(query_for(upgraded, "power_w", "1000 W", "<=")) == int(retained)
    # Restarting v0.2 is idempotent; it must not create another revision or audit entry.
    assert Ledger(ledger.settings, Parser(ledger.settings)).get(doc["id"]) == current
