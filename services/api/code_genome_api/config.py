from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="CODE_GENOME_", extra="ignore")

    database_url: str = "sqlite:///./code_genome.db"
    redis_url: str = "redis://localhost:6379/0"
    job_backend: Literal["inline", "arq", "manual"] = "inline"
    job_delay_seconds: float = 0.2
    bootstrap_demo: bool = False
    cors_origins: str = "http://localhost:3000"

    @property
    def allowed_origins(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
