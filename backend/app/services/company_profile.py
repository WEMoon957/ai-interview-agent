"""公司文化档案服务：单例档案读写 + 发链接时快照 + 开场白文案。

对应 docs/PRD-企业文化融入面试题.md 的 CC-1 / CC-2 / CC-4 / CC-6。

设计要点：
- 档案是全局单例（业务上只保留一行），不拆结构化字段，就是一段自由文字；
- 「快照隔离」：发链接时把生效介绍写进会话，之后改档案不影响已发出的面试；
- 未配置介绍时全部走空值降级，链路行为与改造前完全一致。
"""
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.db.models.company import CompanyProfile

# 开场白中公司介绍的展示上限（超出截断，避免开场白过长）
OPENING_INTRO_MAX_CHARS = 300
# 截断标记
_ELLIPSIS = "……"


def get_active_profile(db: Session) -> CompanyProfile | None:
    """取当前生效的公司文化档案；未配置时返回 None。

    单例口径：取最近更新的一条（正常业务下表中至多一行有效档案）。
    """
    return (
        db.query(CompanyProfile)
        .order_by(CompanyProfile.updated_at.desc())
        .first()
    )


def save_profile(db: Session, content: str, updated_by: str = "") -> CompanyProfile:
    """整篇保存公司介绍（单例：首次创建，后续更新同一行）。

    仅写库不提交，由调用方统一 commit（与既有路由层事务口径一致）；
    显式刷新 ``updated_at``，保证「最近更新时间」反映本次保存动作。
    """
    row = get_active_profile(db)
    if row is None:
        row = CompanyProfile(content=content or "", updated_by=updated_by or "")
        db.add(row)
    else:
        row.content = content or ""
        row.updated_by = updated_by or ""
        row.updated_at = datetime.now(timezone.utc)
    return row


def snapshot_company_profile(db: Session) -> dict:
    """把生效的公司介绍快照成 dict，供发链接时写入会话。

    四个发链接入口（单个发起 / 批量邀请 / 免初筛直通 / 初筛达标自动发起）统一走这里。
    无介绍（或仅空白）时返回空对象 ``{}``，不阻断发链接流程。
    """
    row = get_active_profile(db)
    content = (row.content or "").strip() if row is not None else ""
    if not content:
        return {}
    return {"content": content}


def intro_for_opening(company_profile: dict | None) -> str:
    """从会话快照中取出用于开场白的公司介绍（限长截断，空档案返回空串）。

    纯函数，不触碰 DB，便于单测与在 graph 节点中直接调用。
    """
    content = str((company_profile or {}).get("content") or "").strip()
    if len(content) <= OPENING_INTRO_MAX_CHARS:
        return content
    return content[:OPENING_INTRO_MAX_CHARS] + _ELLIPSIS
