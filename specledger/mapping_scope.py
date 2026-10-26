"""Explicit finite-schema exclusions, separate from learned weights and raw SFT scores."""

import hashlib
import re
from pathlib import Path

from .field_mapper import FieldMapper

SCOPE_VERSION = "equipment-field-scope-v1"


def exclusion_reason(label):
    words = set(re.findall(r"[a-z]+", label.casefold()))
    if words & {"serial", "lot", "frequency", "dimensions"}:
        return "outside_supported_schema"
    if "storage" in words:
        return "storage_is_not_operating_limit"
    if words & {"minimum", "min"} and words & {"temperature", "thermal", "ambient"}:
        return "minimum_is_not_maximum"
    if words & {"mass", "weight"}:
        excludes_packaging = bool(
            words & {"net", "without", "excluding", "unpackaged", "unpacked", "unboxed", "bare", "uncrated"}
        )
        if words & {"gross", "packed", "shipping", "carton", "crate", "transport"} and not excludes_packaging:
            return "packaged_is_not_net_mass"
    if words & {"voltage", "current", "amperage"}:
        explicit_input = bool(words & {"input", "supply", "mains", "incoming", "inlet"})
        explicit_output = bool(words & {"output", "delivered", "available", "load", "connected", "supplied"})
        if explicit_output and not explicit_input:
            return "output_is_not_input_rating"
    return None


class ScopedFieldMapper(FieldMapper):
    def describe(self):
        return {
            **super().describe(),
            "scope_guard": SCOPE_VERSION,
            "scope_guard_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        }

    def predict(self, labels):
        decisions = super().predict(labels)
        for label, decision in zip(labels, decisions, strict=True):
            if reason := exclusion_reason(label):
                decision.update(raw_predicted_field=decision["field"], field="ignore", scope_exclusion=reason)
        return decisions
