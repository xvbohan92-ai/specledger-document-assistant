"""Explicit equipment schema; never infer missing fields or silently repair OCR digits."""

import math
import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, localcontext

from .models import Observation

SCHEMA = {
    "model": {"label": "型号", "aliases": ["Model", "Model number", "Product code"], "unit": None},
    "power_w": {
        "label": "额定功率",
        "aliases": ["Rated power", "Power rating", "Nominal power"],
        "unit": "W",
    },
    "voltage_v": {
        "label": "输入电压",
        "aliases": ["Input voltage", "Supply voltage", "Rated voltage"],
        "unit": "V",
    },
    "current_a": {
        "label": "输入电流",
        "aliases": ["Input current", "Rated current", "Current draw"],
        "unit": "A",
    },
    "mass_kg": {"label": "净重", "aliases": ["Net mass", "Weight", "Mass"], "unit": "kg"},
    "max_temp_c": {
        "label": "最高工作温度",
        "aliases": ["Max operating temperature", "Maximum temperature", "Temperature limit"],
        "unit": "C",
    },
    "ip_rating": {
        "label": "防护等级",
        "aliases": ["Ingress protection", "IP rating", "Protection class"],
        "unit": None,
    },
}
FACTORS = {
    "power_w": {"W": 1, "kW": 1000, "kw": 1000, "mW": 0.001, "MW": 1e6},
    "voltage_v": {"V": 1, "kV": 1000, "mV": 0.001},
    "current_a": {"A": 1, "mA": 0.001},
    "mass_kg": {"kg": 1, "g": 0.001, "lb": 0.45359237, "lbs": 0.45359237},
    "max_temp_c": {"C": 1, "F": 1},
}
LIMITS = {
    "power_w": (0, 1e6),
    "voltage_v": (0, 10000),
    "current_a": (0, 2000),
    "mass_kg": (0, 10000),
    "max_temp_c": (-100, 500),
}
ERROR_MESSAGES = {
    "missing_value": "缺少数值，不能用空值代替实际测量值",
    "unparsed_value_or_unit": "数值或单位不明确，范围值需要人工确认",
    "unsupported_unit": "单位不在该字段的支持范围内，请核查量纲与大小写",
    "outside_supported_range": "数值超出本项目支持的设备规格范围",
    "invalid_model": "型号应由大写字母、数字和连字符组成",
    "invalid_ip_rating": "请填写明确的防护等级，例如 IP65",
    "incompatible_signal": "AC/DC 仅适用于输入电压或电流，不能附加到重量、温度或功率单位",
    "precision_loss": "数值小于当前查询存储精度，不能无提示地转为零",
}


@dataclass(frozen=True)
class Quantity:
    value: str | float | None = None
    error: str | None = None
    signal: str | None = None
    exact_value: str | None = None


def parse_quantity(name, raw):
    text = raw.strip().replace("\u00a0", " ").replace("°", "").strip()
    if text.casefold() in ("", "n/a", "na", "not specified", "-", "--"):
        return Quantity(error="missing_value")
    if name == "model":
        return (
            Quantity(text)
            if re.fullmatch(r"[A-Z0-9][A-Z0-9-]{2,39}", text)
            else Quantity(error="invalid_model")
        )
    if name == "ip_rating":
        value = text.upper().replace(" ", "")
        return (
            Quantity(value) if re.fullmatch(r"IP[0-6][0-9]", value) else Quantity(error="invalid_ip_rating")
        )
    # A single value with a stated unit is required. Ranges and ambiguous decimals stay unresolved.
    match = re.fullmatch(r"([+-]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?)\s*([A-Za-z]+)(?:\s+(DC|AC))?", text)
    if not match or len(text) > 256:
        return Quantity(error="unparsed_value_or_unit")
    unit, signal = match[2], match[3]
    if signal and name not in ("voltage_v", "current_a"):
        return Quantity(error="incompatible_signal")
    if unit not in FACTORS[name]:
        return Quantity(error="unsupported_unit")
    try:
        with localcontext() as context:
            context.prec = max(50, len(match[1]) + 20)
            number = Decimal(match[1].replace(",", ""))
            value = number * Decimal(str(FACTORS[name][unit]))
            if name == "max_temp_c" and unit == "F":
                value = (number - 32) * 5 / 9
            low, high = (Decimal(str(v)) for v in LIMITS[name])
            if not value.is_finite() or value < low or value > high or (value == 0 and name != "max_temp_c"):
                return Quantity(error="outside_supported_range")
            numeric = float(value)
            if not math.isfinite(numeric) or (numeric == 0 and value != 0):
                return Quantity(error="precision_loss")
            exact = format(value, "f") if value else "0"
            if "." in exact:
                exact = exact.rstrip("0").rstrip(".")
            return Quantity(numeric, signal=signal, exact_value=exact)
    except (InvalidOperation, OverflowError):
        return Quantity(error="outside_supported_range")


def normalize(name, raw):
    quantity = parse_quantity(name, raw)
    return quantity.value, quantity.error


def normalized_observation(observation):
    quantity = parse_quantity(observation.name, observation.raw_value)
    return observation.model_copy(
        update={
            "value": quantity.value,
            "error": quantity.error,
            "unit": SCHEMA[observation.name]["unit"],
            "signal": quantity.signal,
            "exact_value": quantity.exact_value,
        }
    )


def extract_fields(pages):
    observations = []
    for page in pages:
        for line in page.lines:
            text = line.text.strip().strip("| ").replace("**", "")
            for name, field in SCHEMA.items():
                aliases = "|".join(
                    re.escape(alias) for alias in sorted(field["aliases"], key=len, reverse=True)
                )
                match = re.match(rf"^(?:{aliases})(?:\s*[:|]\s*|\s{{2,}})(.+)$", text, re.I)
                if not match:
                    continue
                raw = " ".join(match[1].replace("|", " ").split()).strip()
                observation = normalized_observation(Observation(name=name, raw_value=raw, evidence=line))
                # Suppress duplicate table + text extraction at the same page location.
                if not any(
                    o.name == name and o.raw_value == raw and o.evidence.page == line.page
                    for o in observations
                ):
                    observations.append(observation)
                break
    return observations


def validate(observations):
    fields, issues = {}, []
    for name in SCHEMA:
        candidates = [normalized_observation(o) for o in observations if o.name == name]
        manual = [o for o in candidates if o.origin == "manual"]
        chosen = manual[-1:] or candidates
        if not chosen:
            issues.append({"field": name, "code": "missing_field", "message": f"缺少{SCHEMA[name]['label']}"})
            continue
        if any(o.error for o in chosen):
            issues.append(
                {"field": name, "code": chosen[0].error or "invalid_value", "message": "原文值或单位需要复核"}
            )
            continue
        values = {(o.exact_value or o.value, o.signal) for o in chosen}
        if len(values) > 1:
            issues.append({"field": name, "code": "conflicting_values", "message": "多处原文数值冲突"})
            continue
        fields[name] = chosen[0].model_dump()
    return fields, issues
