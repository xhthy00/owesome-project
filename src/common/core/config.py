"""Configuration management using pydantic-settings."""

from functools import lru_cache
from pathlib import Path
from typing import Literal, Optional

from pydantic_settings import BaseSettings, SettingsConfigDict

_REPO_ROOT = Path(__file__).resolve().parents[3]
_ENV_FILE = _REPO_ROOT / ".env"


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=str(_ENV_FILE) if _ENV_FILE.is_file() else ".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    # App
    app_name: str = "awesome-project"
    debug: bool = False
    api_prefix: str = "/api/v1"

    # Database
    database_url: str = "postgresql://postgres:postgres@localhost:5432/awesome"
    # 学情业务库。知识点管理只读这一条，不使用数据源表里的连接。
    database_edu_url: str = ""

    # JWT
    jwt_secret_key: str = "your-secret-key-change-in-production"
    jwt_algorithm: str = "HS256"
    jwt_access_token_expire_minutes: int = 1440

    # 智能问数。max_tokens 不设宽裕值时，部分服务商默认偏小，长思考和工具调用 JSON 会被截断。
    llm_chat_base_url: Optional[str] = None
    llm_chat_api_key: Optional[str] = None
    llm_chat_model: Optional[str] = None
    llm_chat_max_tokens: int = 8192
    # 思考模式：``disabled`` / ``adaptive``。仅部分模型支持。留空不发送该参数。
    llm_chat_thinking: Optional[str] = None
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

    # Team 模式编排：``legacy`` 为手写协程；``langgraph`` 为 LangGraph StateGraph（默认）。
    team_orchestrator: Literal["legacy", "langgraph"] = "langgraph"

    # 多轮对话：按用户提问轮数截取 chat_log 历史（对齐 SQLBot GENERATE_SQL_QUERY_HISTORY_ROUND_COUNT）
    generate_sql_query_history_round_count: int = 3

    # 教育澄清：用 LLM 抽取意图槽位（对标 DB-GPT IntentDetection）。关掉则只走规则抽槽。
    edu_llm_slot_extraction: bool = True
    # 教育槽位流水线：legacy=旧字典合并；shadow=新旧并跑、旧结果执行；
    # semantic=语义帧驱动执行。灰度期间可随时回切 legacy。
    edu_slot_mode: Literal["legacy", "shadow", "semantic"] = "semantic"
    # DataAnalyst 口径分叉 HITL：off=不声明；shadow=声明并打日志、不弹窗；
    # on=ReAct 中用 question 工具让用户选择。
    edu_da_question_mode: Literal["off", "shadow", "on"] = "shadow"

    # 侧边栏「系统文档」。留空则用代码内默认地址，修改后重启服务生效。
    edu_doc_commitment_url: str = ""
    edu_doc_training_url: str = ""
    edu_doc_manual_url: str = ""



@lru_cache
def get_settings() -> Settings:
    """Get cached settings instance."""
    return Settings()
