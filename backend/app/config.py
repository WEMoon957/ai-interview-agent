"""全局配置：通过 pydantic-settings 从环境变量 / .env 加载。

所有 LLM 与数据库配置均从这里读取，业务代码禁止硬编码或绕过本模块。
"""
from functools import lru_cache
from pathlib import Path

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

    # ── 招聘汇总权重（各阶段对最终分的贡献，缺失环节会自动重新归一化） ──
    summary_weight_screening: float = 0.20  # 简历初筛
    summary_weight_ai: float = 0.40  # AI 面试
    summary_weight_offline: float = 0.40  # 线下复试

    # ── 文件存储 ─────────────────────────────────────────────────
    # 上传文件落盘根目录（相对后端工作目录）。
    # 服务器/容器内 api 的工作目录是 /app，且 compose 已把宿主机 backend/ 绑定挂载进来，
    # 因此写到 /app/uploads 即持久化到 /home/ubuntu/hragent/backend/uploads。
    upload_dir: str = "uploads"
    # 单个面试录音大小上限（MB），超出直接拒绝（避免大文件撑爆内存/磁盘）
    max_audio_mb: int = 50

    # ── 对外链接 ─────────────────────────────────────────────────
    # 候选人面试页的对外基址（卡片/待办里的绝对链接由它拼出，如 http://host:3000/i/{token}）。
    # 部署到服务器或走内网穿透时，改成 HR/候选人实际能访问的地址。
    public_base_url: str = "http://localhost:3000"

    # ── 飞书 HR 通知（面试达标自动推送） ─────────────────────────
    # 总开关：False 时 final_report 不触发任何外部推送
    feishu_notify_enabled: bool = False
    # 触发阈值：综合分 >= 该值即推送（默认 65，与系统「推荐」档对齐）
    feishu_notify_threshold: float = 65.0
    # 分环节开关：简历初筛完成推送推荐名单 / 招聘汇总完成提醒 HR 复核与办理 offer
    feishu_screening_notify_enabled: bool = True
    # 初筛推送门槛：初筛分 >= 该值才进入「推荐 AI 面试」名单
    feishu_screening_min_score: float = 65.0
    # 初筛通过（达到门槛）即自动生成 AI 面试链接，供推送卡片/待办直接带上
    feishu_screening_auto_invite: bool = True
    # 推送时顺带为 HR 创建飞书待办（任务），确保「提醒」落到待办列表而不是刷过去
    feishu_task_enabled: bool = True
    # 待办负责人 open_id（ou_ 开头），多个用英文逗号分隔；
    # 留空 = 每个飞书收件人各建一条待办
    feishu_task_assignee_open_ids: str = ""
    # 待办创建身份（仅 cli 传输生效）：bot=应用身份（服务器部署推荐，需应用申请 task:task:write）
    #                               user=本机登录用户身份（联调便捷，需给 lark-cli 补授权该 scope）
    feishu_task_identity: str = "bot"
    # 待办截止时间（天数，相对今天）
    feishu_task_due_days: int = 2
    feishu_summary_notify_enabled: bool = True
    # 单张卡片最多列出的候选人条数（超出只显示汇总数字，避免卡片过长）
    feishu_notify_max_items: int = 20
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
    def task_assignee_list(self) -> list[str]:
        """待办负责人 open_id 列表；为空时由调用方回退为「所有收件人」。"""
        return [
            item.strip()
            for item in (self.feishu_task_assignee_open_ids or "").split(",")
            if item.strip()
        ]

    @property
    def public_base_url_normalized(self) -> str:
        """对外基址（去掉尾部斜杠，便于直接拼 /i/{token}）。"""
        return (self.public_base_url or "").rstrip("/")

    @property
    def upload_root(self) -> Path:
        """上传文件落盘根目录的绝对路径（相对路径按当前工作目录解析）。"""
        return Path(self.upload_dir).expanduser().resolve()

    @property
    def max_audio_bytes(self) -> int:
        """面试录音大小上限（字节）。"""
        return max(1, int(self.max_audio_mb)) * 1024 * 1024

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
