import pytest

from specledger.mapping_scope import exclusion_reason


@pytest.mark.parametrize(
    "label",
    [
        "Output voltage",
        "Current available at output",
        "Gross packed mass",
        "Maximum storage ambient",
        "Minimum operating temperature",
        "Serial number",
        "Production lot code",
        "Voltage delivered to the load",
    ],
)
def test_explicitly_out_of_scope_validation_fields_are_rejected(label):
    assert exclusion_reason(label)


@pytest.mark.parametrize(
    "label",
    [
        "Supply voltage at rated load",
        "Mass excluding shipping crate",
        "Weight without packaging",
        "Net product mass",
        "Maximum operating temperature",
        "Rated power",
        "Manufacturer part number",
    ],
)
def test_positive_schema_qualifiers_are_not_rejected(label):
    assert exclusion_reason(label) is None
