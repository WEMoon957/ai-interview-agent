"""管理端：飞书妙记个人授权（OAuth 2.0 设备授权流）。

为什么需要个人授权
------------------
应用身份（bot）只能读「已公开」妙记，而公开只能由所有者在客户端逐条设置。
改为保存妙记所有者本人的 user_access_token 后，私有妙记也能读，无需逐条公开。

交互流程（免回调域名，适合内网服务器）
--------------------------------------
1. HR/管理员在本页点「生成授权链接」→ 后端申请设备码，返回链接与 user_code；
2. 把链接发给对应的人（店长/HR），对方在飞书里打开并确认授权；
3. 前端轮询 ``/authorizations/poll/{session_id}``，成功后自动落库并刷新列表。

权限：仅 HR / 管理员可发起与查看（店长不接触授权管理）。
"""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import get_db, ok, require_user
from app.db.models import AdminUser
from app.exceptions import BizError
from app.services.minutes_auth import (
    list_authorizations,
    poll_device_authorization,
    revoke_authorization,
    serialize_authorization,
    start_device_authorization,
)

router = APIRouter(prefix="/admin/feishu-minutes", tags=["feishu-minutes"])


@router.post("/authorizations/start")
def start_authorization(
    user: AdminUser = Depends(require_user),
):
    """申请设备码，返回授权链接与 user_code（供前端展示/复制）。"""
    ok_flag, data, err = start_device_authorization(authorized_by=user.username or "")
    if not ok_flag:
        raise BizError(502, f"生成授权链接失败：{err}", http_status=502)
    return ok(data)


@router.get("/authorizations/poll/{session_id}")
def poll_authorization(
    session_id: str,
    db: Session = Depends(get_db),
):
    """轮询一次授权结果：pending / success / denied / expired / error。"""
    status, authorization, message = poll_device_authorization(db, session_id)
    if status == "error":
        raise BizError(502, f"授权轮询失败：{message}", http_status=502)
    return ok({"status": status, "authorization": authorization, "message": message})


@router.get("/authorizations")
def list_authorizations_api(db: Session = Depends(get_db)):
    """授权列表（只返回状态与过期时间，**不返回 token 原文**）。"""
    rows = list_authorizations(db)
    return ok([serialize_authorization(row) for row in rows])


@router.delete("/authorizations/{open_id}")
def revoke_authorization_api(open_id: str, db: Session = Depends(get_db)):
    """撤销某用户的授权（清空 token，置 revoked）。"""
    if not revoke_authorization(db, open_id):
        raise BizError(404, "授权不存在", http_status=404)
    return ok({"open_id": open_id, "status": "revoked"})
