"""角色权限矩阵测试：admin / hr / store_manager 三种角色对全部后台端点的访问边界。

安全语义（见 docs/安全改造-账号密码登录.md §3）：

- **store_manager（店长）**：只能操作线下复试工作台（含到店直招、打分、
  绑妙记、采纳 AI 分、转写、改期/状态），外加岗位下拉（GET /admin/jobs）。
  **看不到**简历、面试对话全文、汇总排名、Offer、模板与账号管理。
- **hr**：全部业务功能；**不能**管理账号。
- **admin**：全部功能含账号管理。

若新增管理端路由忘了挂角色，本文件的「漏挂检测」会直接失败。
"""
import uuid

import pytest
from fastapi import FastAPI
from fastapi.routing import APIRoute
from starlette.testclient import TestClient

from app.api.deps import require_user
from app.main import app

client = TestClient(app)


def _collect_roles(dependant, acc):
    """递归收集一个路由依赖树上所有角色标记。"""
    call = dependant.call
    roles = getattr(call, "_ia_allowed_roles", None)
    if roles:
        acc.add(roles)
    for dep in dependant.dependencies:
        _collect_roles(dep, acc)


def _route_matrix():
    """返回 {path+method: (guarded, allowed_roles_set or None)}。

    ``allowed_roles=None`` 表示只有 require_user（登录即可，不限角色）。
    """
    matrix = {}
    for route in app.routes:
        if not isinstance(route, APIRoute):
            continue
        dependant = route.dependant
        calls = set()
        _collect_calls(dependant, calls)
        guarded = require_user in calls
        role_sets = set()
        _collect_roles(dependant, role_sets)
        allowed = None  # None = 登录即可
        if role_sets:
            # 同一路由挂多个角色依赖时取并集
            allowed = set().union(*role_sets)
        for method in route.methods or {"GET"}:
            matrix[(route.path, method)] = (guarded, allowed)
    return matrix


def _collect_calls(dependant, acc):
    acc.add(dependant.call)
    for dep in dependant.dependencies:
        _collect_calls(dep, acc)


MATRIX = _route_matrix()


# ── 漏挂检测：管理端路由必须全部有保护 ────────────────────────
def test_all_admin_routes_guarded():
    """任何 /admin/* 端点不得裸奔（漏挂 require_user 直接失败）。"""
    unguarded = [
        f"{method} {path}"
        for (path, method), (guarded, _) in MATRIX.items()
        if path.startswith("/admin") and not guarded
    ]
    assert not unguarded, f"管理端路由漏挂鉴权：{unguarded}"


def test_hr_data_routes_exclude_store_manager():
    """HR 职责数据（简历/会话/汇总/Offer/模板/公司档案）不得对店长开放。"""
    forbidden_prefixes = (
        "/admin/screenings",
        "/admin/candidates",
        "/admin/sessions",
        "/admin/summary",
        "/admin/offers",
        "/admin/templates",
        "/admin/company-profile",
    )
    for (path, method), (_, allowed) in MATRIX.items():
        if path.startswith(forbidden_prefixes):
            assert allowed is not None and "store_manager" not in allowed, (
                f"{method} {path} 允许了店长访问 HR 数据"
            )


def test_store_manager_workbench_accessible():
    """店长工作台（线下复试读写 + 岗位下拉）必须是「登录即可」或显式含店长。

    注意 DELETE 不在此列 —— 删除仅 HR/管理员（另有专门用例断言）。
    """
    workbench = [
        ("/admin/offline-interviews", "GET"),
        ("/admin/offline-interviews", "POST"),
        ("/admin/offline-interviews/walk-in", "POST"),
        ("/admin/offline-interviews/{offline_id}", "PUT"),
        ("/admin/offline-interviews/{offline_id}/score", "POST"),
        ("/admin/offline-interviews/{offline_id}/audio", "GET"),
        ("/admin/offline-interviews/{offline_id}/bind-minutes", "POST"),
        ("/admin/offline-interviews/{offline_id}/adopt-ai-score", "POST"),
        ("/admin/offline-interviews/{offline_id}/transcribe", "POST"),
        ("/admin/jobs", "GET"),
    ]
    for path, method in workbench:
        key = (path, method)
        assert key in MATRIX, f"工作台端点不存在：{method} {path}"
        guarded, allowed = MATRIX[key]
        assert guarded, f"{method} {path} 未受保护"
        assert allowed is None or "store_manager" in allowed, (
            f"{method} {path} 未对店长开放"
        )


def test_offline_delete_restricted_to_hr():
    """删除线下复试记录仅 HR/管理员（店长改用爽约/取消状态）。"""
    _, allowed = MATRIX[("/admin/offline-interviews/{offline_id}", "DELETE")]
    assert allowed is not None
    assert "store_manager" not in allowed
    assert "hr" in allowed and "admin" in allowed


def test_account_management_admin_only():
    """账号管理仅 admin。"""
    for (path, method), (_, allowed) in MATRIX.items():
        if path.startswith("/auth/users"):
            assert allowed is not None and allowed == {"admin"}, (
                f"{method} {path} 应仅 admin 可用，实际 {allowed}"
            )


def test_job_write_operations_exclude_store_manager():
    """岗位增删改仅 HR/管理员（店长只有 GET /admin/jobs 只读）。"""
    _, post_allowed = MATRIX[("/admin/jobs", "POST")]
    assert post_allowed is not None and "store_manager" not in post_allowed
    _, del_allowed = MATRIX[("/admin/jobs/{job_id}", "DELETE")]
    assert del_allowed is not None and "store_manager" not in del_allowed
