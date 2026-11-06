"""Configuration management using pydantic-settings."""

from functools import lru_cache
from pathlib import Path
from typing import Literal, Optional

from pydantic import AliasChoices, BaseModel, Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_REPO_ROOT = Path(__file__).resolve().parents[3]
_ENV_FILE = _REPO_ROOT / ".env"


class DataSemanticBinding(BaseModel):
    bundle_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,99}$")
    manifest_hash: str = Field(pattern=r"^[a-f0-9]{64}$")


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=str(_ENV_FILE) if _ENV_FILE.is_file() else ".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        hide_input_in_errors=True,
    )

    # App
    app_name: str = "awesome-project"
    debug: bool = False
    api_prefix: str = "/api/v1"

    # Database
    database_url: str = "postgresql://postgres:postgres@localhost:5432/awesome"
    # 学情业务库。聊天问数及学校、知识点等管理功能使用这一条连接。
    database_edu_url: str = ""

    # JWT
    jwt_secret_key: str = "your-secret-key-change-in-production"
    jwt_algorithm: str = "HS256"
    jwt_access_token_expire_minutes: int = 1440

    # 智能问数。max_tokens 不设宽裕值时，部分服务商默认偏小，长思考和工具调用 JSON 会被截断。
    llm_chat_base_url: Optional[str] = Field(
        None, validation_alias=AliasChoices("llm_chat_base_url", "llm_base_url")
    )
    llm_chat_api_key: Optional[str] = Field(
        None, validation_alias=AliasChoices("llm_chat_api_key", "llm_api_key")
    )
    llm_chat_model: Optional[str] = Field(
        None, validation_alias=AliasChoices("llm_chat_model", "llm_model")
    )
    llm_chat_max_tokens: int = 8192
    # 可选单轮问数上限，默认不限制（沿用 DeepAgents 的图执行上限）。
    # 设置后用于收敛失控长尾；留空表示不限。
    chat_max_steps: int | None = Field(default=None, ge=1)
    chat_max_total_tokens: int | None = Field(default=None, ge=1)
    # 思考模式：``disabled`` / ``adaptive``。仅部分模型支持。留空不发送该参数。
    llm_chat_thinking: Optional[str] = None
    # 流式回复时向服务商索取 usage 分片（stream_options.include_usage）。
    # 个别网关不支持该参数时置 false，代价是本轮 token 统计改用估算值。
    llm_chat_stream_usage: bool = True
    # 知识点匹配
    llm_match_base_url: Optional[str] = None
    llm_match_api_key: Optional[str] = None
    llm_match_model: Optional[str] = None
    llm_match_max_tokens: Optional[int] = None
    llm_match_thinking: Optional[str] = None
    # 试卷 AI 识别
    llm_paper_base_url: Optional[str] = None
    llm_paper_api_key: Optional[str] = None
    llm_paper_model: Optional[str] = None
    llm_paper_max_tokens: Optional[int] = None
    llm_paper_thinking: Optional[str] = None

    # DataAnalyst 口径分叉 HITL：off=不声明；shadow=声明并打日志、不弹窗；
    # on=ReAct 中用 question 工具让用户选择。
    edu_da_question_mode: Literal["off", "shadow", "on"] = "shadow"

    # Bind datasource IDs to reviewed DataSemantic publications.
    datasemantic_bindings: dict[int, DataSemanticBinding] = Field(default_factory=dict)
    datasemantic_url: str = ""
    datasemantic_token: str = ""
    datasemantic_datasource_id: int | None = None
    datasemantic_bundle_id: str = ""
    datasemantic_manifest_hash: str = ""

    # 侧边栏「系统文档」。留空则用代码内默认地址，修改后重启服务生效。
    edu_doc_commitment_url: str = ""
    edu_doc_training_url: str = ""
    edu_doc_manual_url: str = ""

    @field_validator("chat_max_steps", "chat_max_total_tokens", mode="before")
    @classmethod
    def empty_chat_budget(cls, value):
        return None if value == "" else value

    @model_validator(mode="before")
    @classmethod
    def ignore_retired_runtime_settings(cls, values):
        # Existing .env files may still contain settings from the previous chat runtime.
        if isinstance(values, dict):
            for key in (
                "team_orchestrator",
                "generate_sql_query_history_round_count",
                "edu_llm_slot_extraction",
                "edu_slot_mode",
                "chat_max_queries",
            ):
                values.pop(key, None)
        return values


@lru_cache
def get_settings() -> Settings:
    """Get cached settings instance."""
    return Settings()
