"""全局配置：通过 pydantic-settings 从环境变量 / .env 加载。

所有 LLM 与数据库配置均从这里读取，业务代码禁止硬编码或绕过本模块。
"""
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """应用配置。

    属性名与 .env 中的大写变量名一一对应（pydantic-settings 默认大小写不敏感）。
    """

    model_config = SettingsConfigDict(
        env_file=(".env", "../.env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ── LLM（OpenAI 兼容协议，切换提供商仅改配置） ────────────────
    llm_model: str = "deepseek-chat"
    llm_base_url: str = "https://api.deepseek.com"
    llm_api_key: str = ""
    llm_temperature: float = 0.7
    # 评分使用低温，保证结构化输出稳定（架构约定 0.2）
    scoring_temperature: float = 0.2
    # 最大输出 token 数：评分 comment 需引用原话且可能较长，避免被截断导致 JSON 不完整
    llm_max_tokens: int = 4096

    # ── 数据库（业务表 + LangGraph checkpointer 复用同一库） ─────
    database_url: str = "postgresql://interview:interview@localhost:5432/interview"

    # ── 面试业务配置 ─────────────────────────────────────────────
    default_max_follow_ups: int = 2
    # MVP 兜底：置 True 时启动自动建表（默认走 Alembic）
    auto_create_tables: bool = False

    @property
    def sqlalchemy_url(self) -> str:
        """返回 SQLAlchemy 可用的数据库 URL（强制使用 psycopg v3 驱动）。"""
        url = self.database_url
        if url.startswith("postgresql://"):
            return url.replace("postgresql://", "postgresql+psycopg://", 1)
        if url.startswith("postgres://"):
            return url.replace("postgres://", "postgresql+psycopg://", 1)
        return url

    @property
    def checkpointer_dsn(self) -> str:
        """返回 PostgresSaver 可用的 libpq DSN（去掉 SQLAlchemy 驱动后缀）。"""
        url = self.database_url
        for prefix in (
            "postgresql+psycopg://",
            "postgresql+psycopg2://",
            "postgresql+pg8000://",
        ):
            if url.startswith(prefix):
                return url.replace(prefix, "postgresql://", 1)
        return url


@lru_cache
def get_settings() -> Settings:
    """返回全局唯一的 Settings 实例（进程内缓存）。"""
    return Settings()
