import hashlib
import json
from pathlib import Path

import pytest

from specledger.config import Settings
from specledger.field_mapper import FieldMapper, mapped_extract, split_candidate
from specledger.fields import SCHEMA, validate
from specledger.models import EvidenceLine, ParsedPage, ProjectError
from specledger.store import Ledger

ROOT = Path(__file__).resolve().parents[1]


def page(*texts):
    return [
        ParsedPage(
            number=2,
            width=595,
            height=842,
            lines=[EvidenceLine(text=text, page=2, bbox=[0.1, 0.2, 0.8, 0.3]) for text in texts],
        )
    ]


class Mapper:
    def predict(self, labels):
        return [
            {
                "field": "voltage_v" if "potential" in label.casefold() else "ignore",
                "model_weight_sha256": "test-model",
                "margin": 2.0,
            }
            for label in labels
        ]


def test_mapping_assigns_only_schema_and_copies_raw_evidence_without_generating_values():
    lines = page("Mains potential | 0.024 kV DC", "Shipping mass | 12 kg")
    observations = mapped_extract(lines, Mapper())
    assert len(observations) == 1
    row = observations[0]
    assert row.raw_value == "0.024 kV DC" and row.exact_value == "24" and row.signal == "DC"
    assert row.evidence == lines[0].lines[0] and row.evidence.page == 2
    assert row.mapping["raw_label"] == "Mains potential"
    assert row.origin == "extracted"


def test_explicit_rules_take_precedence_and_duplicate_rows_do_not_create_duplicate_values():
    class NeverPredict:
        def predict(self, labels):
            raise AssertionError("Known labels should not reach the model")

    observations = mapped_extract(page("Input voltage | 24 V DC", "Input voltage | 24 V DC"), NeverPredict())
    assert len(observations) == 1
    assert observations[0].mapping is None


def test_model_assignment_still_goes_through_range_unit_and_conflict_checks():
    observations = mapped_extract(page("Mains potential | 24 hp", "Mains potential | 12 V DC"), Mapper())
    fields, issues = validate(observations)
    assert "voltage_v" not in fields
    assert any(i["field"] == "voltage_v" and i["code"] == "unsupported_unit" for i in issues)
    assert observations[0].raw_value == "24 hp"


@pytest.mark.parametrize("bad", ["unknown", "wrong-count"])
def test_invalid_model_decisions_are_not_silently_published(bad):
    class Invalid:
        def predict(self, labels):
            return [] if bad == "wrong-count" else [{"field": "invented_schema_key"}]

    with pytest.raises(ProjectError):
        mapped_extract(page("Mains potential | 24 V"), Invalid())


def test_checkpoint_missing_or_tampered_rejects_before_loading_or_creating_database(tmp_path):
    with pytest.raises(ProjectError):
        Ledger(Settings(tmp_path / "business", backend="text", field_mapper="finetuned"), None)
    assert not (tmp_path / "business/ledger.sqlite").exists()
    folder = tmp_path / "models"
    (folder / "base").mkdir(parents=True)
    (folder / "base/model.safetensors").write_bytes(b"changed weights")
    (folder / "manifest.json").write_text(
        json.dumps(
            {
                "descriptions_sha256": hashlib.sha256(
                    (ROOT / "specledger/field_descriptions.json").read_bytes()
                ).hexdigest(),
                "variants": {"base": {"files": {"model.safetensors": "old"}}},
            }
        )
    )
    with pytest.raises(ProjectError, match="指纹"):
        FieldMapper(folder, "base")


def test_source_candidate_boundaries_and_header_rows():
    assert split_candidate("A | B | C") is None
    assert split_candidate("Parameter | Value") is None
    assert split_candidate("unstructured prose without a separator") is None
    assert split_candidate("Mains potential: 24 V DC") == ("Mains potential", "24 V DC")


def test_training_labels_and_descriptor_contract_are_frozen_and_disjoint():
    manifest = json.loads((ROOT / "training/datasets/manifest.json").read_text())
    labels = []
    for split in ("train", "valid", "test"):
        rows = json.loads((ROOT / f"training/datasets/{split}.json").read_text())
        assert len(rows) == manifest["rows"][split]
        labels += [row["label"].casefold() for row in rows]
        assert set(row["field"] for row in rows) == set(SCHEMA) | {"ignore"}
    assert len(labels) == len(set(labels))
    for path, digest in manifest["files"].items():
        assert hashlib.sha256((ROOT / path).read_bytes()).hexdigest() == digest
