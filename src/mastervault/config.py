"""Configuration: mastervault.toml + MV_* env overrides + plain-env secrets.

Precedence (highest wins): env vars -> .env file -> mastervault.toml -> defaults.
Secrets (API keys, DATABASE_URL) are read ONLY from the environment, never from
the TOML file.
"""

from __future__ import annotations

import os
import tomllib
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, PrivateAttr
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
)

CONFIG_FILENAME = "mastervault.toml"


class StorageCfg(BaseModel):
    backend: Literal["auto", "postgres", "sqlite"] = "auto"


class EmbeddingCfg(BaseModel):
    provider: Literal["openai", "local", "mock"] = "local"
    model: str = "BAAI/bge-small-en-v1.5"
    batch_size: int = 128
    cost_cap_usd: float = 1.00


class LLMCfg(BaseModel):
    # Default to the keyless mock provider so a fresh clone runs `ask`/`ingest`
    # without an API key (deterministic extractive answers). Override with
    # anthropic/openai + a key for real generative synthesis.
    provider: Literal["anthropic", "openai", "mock"] = "mock"
    base_url: str | None = None  # provider="openai" only: compatible gateways
    model_small: str = "claude-haiku-4-5"
    model_medium: str = "claude-sonnet-5"
    model_large: str = "claude-sonnet-5"


class RerankerCfg(BaseModel):
    backend: Literal["auto", "local-bge", "cohere", "mock", "null"] = "auto"


class RetrievalCfg(BaseModel):
    k: int = 10
    rrf_k: int = 60
    rerank_pool: int = 30
    mmr_lambda: float = 0.7


class IngestionCfg(BaseModel):
    band_exists: float = 0.80
    band_candidate: float = 0.60
    max_claims_per_doc: int = 10


class DocumentCfg(BaseModel):
    pdf_parser: Literal["pypdf", "docling"] = "pypdf"
    docling_artifacts_path: Path | None = None


class AskCfg(BaseModel):
    max_rounds: int = 3
    budget_usd: float = 0.25


class BudgetsCfg(BaseModel):
    ingest: float = 5.00
    lint: float = 0.50
    deliberate: float = 0.75


class PathsCfg(BaseModel):
    workspace: Path = Path("./vault_workspace")

    @property
    def vault_dir(self) -> Path:
        return self.workspace / "vault"

    @property
    def review_pending(self) -> Path:
        return self.workspace / "review" / "pending"

    @property
    def review_archive(self) -> Path:
        return self.workspace / "review" / "archive"

    @property
    def runs_dir(self) -> Path:
        return self.workspace / "runs"

    @property
    def assets_dir(self) -> Path:
        return self.workspace / "assets"

    @property
    def parsed_documents_dir(self) -> Path:
        return self.workspace / "parsed"

    @property
    def sqlite_path(self) -> Path:
        return self.workspace / "index.db"

    @property
    def change_control_db_path(self) -> Path:
        return self.workspace / "change_control" / "state.sqlite3"

    @property
    def change_control_checkpoint_path(self) -> Path:
        return self.workspace / "change_control" / "checkpoints.sqlite3"

    @property
    def change_control_generation_root(self) -> Path:
        """Dedicated root for immutable managed generations.

        This deliberately lives outside both the legacy canonical vault and
        its rebuildable serving index.  Application services pass the exact
        derived path into the managed repository boundary; public callers do
        not assemble generation paths themselves.
        """

        return self.workspace / "change_control" / "generations"


def resolve_config_path() -> Path | None:
    """Return the exact TOML source selected by the settings precedence."""
    candidates = []
    if env_path := os.environ.get("MV_CONFIG"):
        candidates.append(Path(env_path))
    candidates.append(Path.cwd() / CONFIG_FILENAME)
    candidates.append(Path(__file__).resolve().parents[2] / CONFIG_FILENAME)
    return next((path.resolve() for path in candidates if path.is_file()), None)


def _toml_source(settings_cls: type[BaseSettings]) -> dict[str, Any]:  # noqa: ARG001
    """Load mastervault.toml from MV_CONFIG, CWD, or the repo default."""
    if path := resolve_config_path():
        with path.open("rb") as fh:
            return tomllib.load(fh)
    return {}


class TomlSettingsSource(PydanticBaseSettingsSource):
    def __init__(self, settings_cls: type[BaseSettings]):
        super().__init__(settings_cls)
        self._data = _toml_source(settings_cls)

    def get_field_value(self, field: Any, field_name: str) -> tuple[Any, str, bool]:  # noqa: ARG002
        return self._data.get(field_name), field_name, False

    def __call__(self) -> dict[str, Any]:
        return {k: v for k, v in self._data.items() if v is not None}


class Settings(BaseSettings):
    _config_source: Path | None = PrivateAttr(default=None)

    model_config = SettingsConfigDict(
        env_prefix="MV_",
        env_nested_delimiter="__",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    storage: StorageCfg = Field(default_factory=StorageCfg)
    embedding: EmbeddingCfg = Field(default_factory=EmbeddingCfg)
    llm: LLMCfg = Field(default_factory=LLMCfg)
    reranker: RerankerCfg = Field(default_factory=RerankerCfg)
    retrieval: RetrievalCfg = Field(default_factory=RetrievalCfg)
    ingestion: IngestionCfg = Field(default_factory=IngestionCfg)
    document: DocumentCfg = Field(default_factory=DocumentCfg)
    ask: AskCfg = Field(default_factory=AskCfg)
    budgets: BudgetsCfg = Field(default_factory=BudgetsCfg)
    paths: PathsCfg = Field(default_factory=PathsCfg)

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        return (
            init_settings,
            env_settings,
            dotenv_settings,
            TomlSettingsSource(settings_cls),
            file_secret_settings,
        )

    # --- secrets: environment only ---------------------------------------
    @property
    def database_url(self) -> str | None:
        return os.environ.get("DATABASE_URL")

    @property
    def anthropic_api_key(self) -> str | None:
        return os.environ.get("ANTHROPIC_API_KEY")

    @property
    def openai_api_key(self) -> str | None:
        return os.environ.get("OPENAI_API_KEY")

    @property
    def cohere_api_key(self) -> str | None:
        return os.environ.get("COHERE_API_KEY")

    @property
    def config_source(self) -> Path | None:
        """TOML file actually selected by :func:`load_settings`, if any."""
        return self._config_source


def load_settings() -> Settings:
    source = resolve_config_path()
    settings = Settings()
    settings._config_source = source
    return settings
