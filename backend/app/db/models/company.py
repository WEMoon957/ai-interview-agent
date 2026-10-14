"""公司文化档案表模型（全局单例，一段公司介绍文字）。"""
from sqlalchemy import String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


class CompanyProfile(TimestampMixin, Base):
    """公司文化档案（company_profiles）。

    全局单例：业务上只保留一行，``get_active_profile`` 取最近更新的一条。
    内容为 HR 自由撰写的一段公司介绍（公司是谁 + 现状如何 + 团队氛围 + 用工特点）。

    发起面试时把 ``content`` 快照进 ``interview_sessions.company_profile_snapshot``，
    因此事后修改本表不会影响已发出的面试会话（CC-4 快照隔离）。
    """

    __tablename__ = "company_profiles"

    # 公司介绍自由文字（唯一业务字段；空串表示未配置）
    content: Mapped[str] = mapped_column(Text, nullable=False, default="")
    # 最近保存人（HR 姓名/账号，可选）
    updated_by: Mapped[str] = mapped_column(String(128), nullable=False, default="")
