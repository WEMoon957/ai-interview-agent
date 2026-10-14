"""到店直招：来源渠道 + 面试录音字段。

Revision ID: 0007_walk_in_audio
Revises: 0006_summary_notified
Create Date: 2026-10-14 09:55:00

说明：
- ``candidates.source_channel``：来源渠道（门店海报 / 员工介绍 / 自然到访 …），到店直招必填。
- ``offline_interviews.source``：来源路径，``online``（线上投递）/ ``walk_in``（到店直招）。
  存量记录一律回填为 ``online``，与改造前的语义一致。
- ``offline_interviews.audio_*``：店长上传的**整场面试录音**（本地磁盘相对路径 + 原始文件名 + 字节数 + 上传时间）。
  为后续 ASR 转写（阶段 B）预留落点。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "0007_walk_in_audio"
down_revision: Union[str, None] = "0006_summary_notified"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """新增来源渠道、来源路径与面试录音字段。"""
    op.add_column(
        "candidates",
        sa.Column("source_channel", sa.String(length=64), nullable=False, server_default=""),
    )
    op.add_column(
        "offline_interviews",
        sa.Column("source", sa.String(length=32), nullable=False, server_default="online"),
    )
    op.add_column(
        "offline_interviews",
        sa.Column("audio_path", sa.String(length=512), nullable=True),
    )
    op.add_column(
        "offline_interviews",
        sa.Column("audio_name", sa.String(length=255), nullable=False, server_default=""),
    )
    op.add_column(
        "offline_interviews",
        sa.Column("audio_size", sa.Integer(), nullable=True),
    )
    op.add_column(
        "offline_interviews",
        sa.Column("audio_uploaded_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    """回滚：移除本轮新增字段。"""
    op.drop_column("offline_interviews", "audio_uploaded_at")
    op.drop_column("offline_interviews", "audio_size")
    op.drop_column("offline_interviews", "audio_name")
    op.drop_column("offline_interviews", "audio_path")
    op.drop_column("offline_interviews", "source")
    op.drop_column("candidates", "source_channel")
