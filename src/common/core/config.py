"""Configuration management using pydantic-settings."""

from functools import lru_cache
from typing import Literal, Optional

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    # App
    app_name: str = "awesome-project"
    debug: bool = False
    api_prefix: str = "/api/v1"

    # Database
    database_url: str = "postgresql://postgres:postgres@localhost:5432/awesome"

    # JWT
    jwt_secret_key: str = "your-secret-key-change-in-production"
    jwt_algorithm: str = "HS256"
    jwt_access_token_expire_minutes: int = 30

    # LLM
    llm_base_url: Optional[str] = None
    llm_api_key: Optional[str] = None
    llm_model: str = "gpt-4o-mini"

    # Team 模式编排：``legacy`` 为手写协程；``langgraph`` 为 LangGraph StateGraph（默认）。
    team_orchestrator: Literal["legacy", "langgraph"] = "langgraph"


@lru_cache
def get_settings() -> Settings:
    """Get cached settings instance."""
    return Settings()
