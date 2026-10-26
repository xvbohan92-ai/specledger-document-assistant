import pytest

from specledger.fields import extract_fields, normalize, validate
from specledger.parsing import parse_text


@pytest.mark.parametrize(
    "name,raw,expected",
    [
        ("power_w", "0.25 kW", 250),
        ("power_w", "1 mW", 0.001),
        ("power_w", "1 MW", 1e6),
        ("power_w", "1,200 W", 1200),
        ("voltage_v", "0.024 kV", 24),
        ("voltage_v", "24 V DC", 24),
        ("current_a", "500 mA", 0.5),
        ("mass_kg", "1200 g", 1.2),
        ("mass_kg", "1 lb", 0.453592),
        ("max_temp_c", "140 F", 60),
        ("max_temp_c", "-10 C", -10),
        ("ip_rating", "IP 65", "IP65"),
        ("model", "ALP-100", "ALP-100"),
    ],
)
def test_units_preserve_dimensions_and_prefix_case(name, raw, expected):
    value, error = normalize(name, raw)
    assert error is None
    assert value == pytest.approx(expected) if isinstance(expected, (int, float)) else value == expected


@pytest.mark.parametrize(
    "name,raw",
    [
        ("power_w", "100-150 W"),
        ("power_w", "0,25 kW"),
        ("power_w", "5 mw"),
        ("power_w", "5 hp"),
        ("power_w", "nan W"),
        ("voltage_v", "-2 V"),
        ("mass_kg", "0 kg"),
        ("max_temp_c", "900 C"),
        ("ip_rating", "N/A"),
    ],
)
def test_ambiguous_or_unsupported_values_remain_unresolved(name, raw):
    value, error = normalize(name, raw)
    assert value is None
    assert error


def test_conflicting_values_across_pages_require_review(demo_data):
    fields, issues = validate(extract_fields(parse_text(demo_data / "demo/pdfs/test-05.pdf")))
    assert "power_w" not in fields
    assert issues == [{"field": "power_w", "code": "conflicting_values", "message": "多处原文数值冲突"}]
    assert fields["mass_kg"]["evidence"]["page"] == 2


def test_scan_fixture_has_no_hidden_text(demo_data):
    assert all(not page.lines for page in parse_text(demo_data / "demo/pdfs/dev-02.pdf"))
