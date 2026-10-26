from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ProjectError(ValueError):
    pass


class EvidenceLine(BaseModel):
    text: str
    page: int = Field(ge=1)
    bbox: list[float] = Field(min_length=4, max_length=4)
    precision: Literal["line", "table", "cell"] = "line"


class ParsedPage(BaseModel):
    number: int = Field(ge=1)
    width: float = Field(gt=0)
    height: float = Field(gt=0)
    lines: list[EvidenceLine]


class Observation(BaseModel):
    name: str
    raw_value: str
    value: str | float | None = None
    unit: str | None = None
    signal: Literal["AC", "DC"] | None = None
    exact_value: str | None = None
    error: str | None = None
    evidence: EvidenceLine
    origin: Literal["extracted", "manual"] = "extracted"
    reason: str | None = None
    mapping: dict | None = None


class Correction(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    raw_value: str = Field(min_length=1, max_length=150)
    reason: str = Field(min_length=3, max_length=500)
    page: int = Field(ge=1, le=20)
    revision: int = Field(ge=1)


class Approval(BaseModel):
    model_config = ConfigDict(extra="forbid")
    revision: int = Field(ge=1)


class ReparseRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    revision: int = Field(ge=1)
    reason: str = Field(min_length=3, max_length=500)


class QueryFilter(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    field: Literal["model", "power_w", "voltage_v", "current_a", "mass_kg", "max_temp_c", "ip_rating"]
    op: Literal["=", "<=", ">="] = "<="
    raw_value: str = Field(min_length=1, max_length=100)


class QueryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    filters: list[QueryFilter] = Field(default_factory=list, max_length=5)
    limit: int = Field(default=20, ge=1, le=100)
