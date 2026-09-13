from functools import lru_cache
from typing import Literal

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="CODE_GENOME_", extra="ignore")

    database_url: str = "sqlite:///./code_genome.db"
    redis_url: str = "redis://localhost:6379/0"
    job_backend: Literal["inline", "arq", "manual"] = "inline"
    job_delay_seconds: float = 0.2
    bootstrap_demo: bool = False
    cors_origins: str = "http://localhost:3000"
    clone_depth: int = 200
    clone_timeout_seconds: int = 120
    max_source_files: int = 10_000
    max_source_file_bytes: int = 1_000_000
    max_source_total_bytes: int = 100_000_000
    max_manifest_files: int = 100_000
    max_history_commits: int = 200
    mirror_root: str = "/tmp/code-genome-mirrors"
    credential_encryption_key: SecretStr | None = None
    credential_key_version: str = "local-v1"

    @property
    def allowed_origins(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
