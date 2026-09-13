from functools import lru_cache
from typing import Literal

from pydantic import AliasChoices, Field, SecretStr
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
    environment: Literal["development", "test", "production"] = "development"
    auth_mode: Literal["development", "oidc"] = "development"
    oidc_issuer: str | None = None
    oidc_audience: str | None = None
    oidc_jwks_url: str | None = None
    retention_days: int = 365
    rate_limit_per_minute: int = 120
    max_request_bytes: int = 1_000_000
    gemini_api_key: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices("GEMINI_API_KEY", "CODE_GENOME_GEMINI_API_KEY"),
    )
    gemini_model: str = Field(
        default="gemini-2.5-flash",
        validation_alias=AliasChoices("GEMINI_MODEL", "CODE_GENOME_GEMINI_MODEL"),
    )
    gemini_timeout_seconds: float = 20
    gemini_max_output_tokens: int = 700

    def validate_runtime(self) -> None:
        if self.environment == "production" and self.auth_mode != "oidc":
            raise RuntimeError("Production requires CODE_GENOME_AUTH_MODE=oidc")
        if self.auth_mode == "oidc" and not all(
            (self.oidc_issuer, self.oidc_audience, self.oidc_jwks_url)
        ):
            raise RuntimeError("OIDC mode requires issuer, audience, and JWKS URL")
        if not 1 <= self.retention_days <= 3650:
            raise RuntimeError("Retention days must be between 1 and 3650")
        if not 10 <= self.rate_limit_per_minute <= 100_000:
            raise RuntimeError("Rate limit must be between 10 and 100000 requests per minute")
        if not 100_000 <= self.max_request_bytes <= 10_000_000:
            raise RuntimeError("Maximum request bytes must be between 100000 and 10000000")
        if not 1 <= self.gemini_timeout_seconds <= 60:
            raise RuntimeError("Gemini timeout must be between 1 and 60 seconds")
        if not 128 <= self.gemini_max_output_tokens <= 4096:
            raise RuntimeError("Gemini output tokens must be between 128 and 4096")

    @property
    def allowed_origins(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
