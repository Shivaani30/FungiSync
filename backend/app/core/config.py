from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from typing import Annotated

from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict
from sqlalchemy.engine import make_url


PROJECT_ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    """Settings loaded from environment variables and the root .env file."""

    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_env: str = "development"
    app_database_url: str
    backend_cors_origins: Annotated[list[str], NoDecode] = ["http://localhost:5173"]
    reservation_pending_ttl_seconds: Annotated[int, Field(gt=0)]

    @field_validator("app_database_url")
    @classmethod
    def require_runtime_database_role(cls, value: str) -> str:
        if make_url(value).username != "fungisync_app_runtime":
            raise ValueError("APP_DATABASE_URL must use the restricted fungisync_app_runtime login")
        return value

    @field_validator("backend_cors_origins", mode="before")
    @classmethod
    def parse_cors_origins(cls, value: str | list[str]) -> list[str]:
        if isinstance(value, str):
            return [origin.strip() for origin in value.split(",") if origin.strip()]
        return value


@lru_cache
def get_settings() -> Settings:
    return Settings()
