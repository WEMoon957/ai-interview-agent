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
    # checkpointer 连接池大小（多线程并发面试时按需检出独立连接，避免共享单连接）
    checkpointer_pool_min_size: int = 1
    checkpointer_pool_max_size: int = 20

    # ── 面试业务配置 ─────────────────────────────────────────────
    default_max_follow_ups: int = 2
    # MVP 兜底：置 True 时启动自动建表（默认走 Alembic）
    auto_create_tables: bool = False

    # ── 飞书 HR 通知（面试达标自动推送） ─────────────────────────
    # 总开关：False 时 final_report 不触发任何外部推送
    feishu_notify_enabled: bool = False
    # 触发阈值：综合分 >= 该值即推送（默认 65，与系统「推荐」档对齐）
    feishu_notify_threshold: float = 65.0
    # 传输方式：cli（复用本机已登录的 lark-cli，默认）| api（飞书 Open API 直连）
    feishu_notify_transport: str = "cli"
    # HR 收件人 open_id（ou_ 开头），多个用英文逗号分隔
    feishu_hr_open_ids: str = ""
    # HR 收件部门（open_department_id，od- 开头），多个用英文逗号分隔；
    # 推送时按部门实时解析成员 open_id（自动纳入新入职成员，过滤离职/冻结）
    feishu_hr_department_ids: str = ""
    # cli 传输：lark-cli 可执行文件路径（留空则自动探测 PATH 与受管安装目录）
    feishu_lark_cli_path: str = ""
    # api 传输：自建应用凭据（transport=api 时必填）
    feishu_app_id: str = ""
    feishu_app_secret: str = ""
    # 单次发送超时（秒）
    feishu_notify_timeout: float = 10.0

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

    @property
    def hr_open_id_list(self) -> list[str]:
        """HR 收件人 open_id 列表（解析逗号分隔串，忽略空白项）。"""
        return [item.strip() for item in (self.feishu_hr_open_ids or "").split(",") if item.strip()]

    @property
    def hr_department_id_list(self) -> list[str]:
        """HR 收件部门 open_department_id 列表（解析逗号分隔串，忽略空白项）。"""
        return [
            item.strip()
            for item in (self.feishu_hr_department_ids or "").split(",")
            if item.strip()
        ]

    @property
    def feishu_notify_ready(self) -> bool:
        """通知是否具备发送条件：已启用 + 有收件人（人或部门）+ 传输凭据齐备。"""
        if not self.feishu_notify_enabled:
            return False
        if not self.hr_open_id_list and not self.hr_department_id_list:
            return False
        if (self.feishu_notify_transport or "cli").lower() == "api":
            return bool(self.feishu_app_id and self.feishu_app_secret)
        return True


@lru_cache
def get_settings() -> Settings:
    """返回全局唯一的 Settings 实例（进程内缓存）。"""
    return Settings()
