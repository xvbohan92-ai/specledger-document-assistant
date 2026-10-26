import os
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PIPELINE_VERSION = "specledger-fields-v2"
UNIT_RULES_VERSION = "decimal-signal-v2"


@dataclass(frozen=True)
class Settings:
    data: Path
    backend: str = "docling"
    field_mapper: str = "rules"
    field_models: Path | None = None

    @property
    def mapping_models(self):
        return self.field_models or self.data / "field-models"

    @property
    def database(self):
        return self.data / "ledger.sqlite"

    @classmethod
    def from_env(cls):
        path = Path(os.getenv("SPECLEDGER_DATA", str(ROOT / "data")))
        models = os.getenv("SPECLEDGER_FIELD_MODELS")
        return cls(
            path.resolve(),
            os.getenv("SPECLEDGER_BACKEND", "docling"),
            os.getenv("SPECLEDGER_FIELD_MAPPER", "rules"),
            Path(models).resolve() if models else None,
        )
