"""Pipeline configuration loaded from config/pipeline.yaml."""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class Paths(BaseModel):
    db: Path
    raw: Path
    exports: Path
    manual: Path

    def resolve(self, root: Path) -> Paths:
        return Paths(**{k: root / v for k, v in self.model_dump().items()})

    @property
    def releases(self) -> Path:
        return self.raw / "releases"

    @property
    def state(self) -> Path:
        return self.db.parent / "state.json"


class EsmisConfig(BaseModel):
    base_url: str = "https://esmis.nal.usda.gov"
    identifier: str = "wasde"
    sleep_seconds: float = 1.0


class Eras(BaseModel):
    xml_start: str
    txt_start: str


class PipelineConfig(BaseModel):
    paths: Paths
    esmis: EsmisConfig
    eras: Eras
    priority_tables: list[str]


def load_config(path: Path | None = None) -> PipelineConfig:
    path = path or PROJECT_ROOT / "config" / "pipeline.yaml"
    cfg = PipelineConfig(**yaml.safe_load(path.read_text()))
    cfg.paths = cfg.paths.resolve(path.resolve().parents[1])
    return cfg
