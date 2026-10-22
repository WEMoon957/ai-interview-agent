# 安全改造：个人访问令牌（PAT）

> 新增于 2026-10-22（迁移 `0020_personal_access_tokens`）
> 前置阅读：[安全改造-账号密码登录.md](./安全改造-账号密码登录.md)

## 1. 为什么需要

原系统的身份只有两种：

| 通道 | 适用者 | 问题 |
|---|---|---|
| 会话 Cookie + CSRF | 浏览器 | 12 小时过期，程序无法自然持有 |
| `X-API-Key`（仅 `/api/scores`） | 外部系统 | 全局唯一密钥，**无法追溯到人**，且只有只读 |

于是所有程序化客户端（桌面 Agent、CLI、未来的产品内 Copilot）只能二选一：
要么把 HR 的账号密码存在本地（密码落盘、改密即失效、审计分不清人机），
要么模拟浏览器登录（脆、CSRF 三处值要同步、12 小时重登）。

PAT 是第三条路：**绑定到具体用户的长期凭据**。

## 2. 设计

### 2.1 存储

表 `personal_access_tokens`：

| 字段 | 说明 |
|---|---|
| `user_id` | 归属账号（`ON DELETE CASCADE`） |
| `name` | 设备/用途标识，如「MacBook-招聘助手」 |
| `token_hash` | `sha256(原文)`，**永不存原文** |
| `token_prefix` | 原文前 12 字符，仅供展示与日志识别，不足以冒用 |
| `scopes` | `read` / `read,write`（逗号分隔） |
| `expires_at` | 有效期，`NULL` 表示长期 |
| `last_used_at` / `last_used_ip` | 惰性刷新，60 秒节流 |
| `revoked_at` | 撤销时间，不物理删除以保留审计线索 |

令牌原文形如 `ia_pat_` + 43 位 URL-safe 随机串，**只在签发响应中出现一次**。

### 2.2 解析顺序

`app/api/deps.py::require_user` 是唯一鉴权入口：

```
有 Authorization: Bearer ? ── 是 ──> 走令牌通道（不校验 CSRF）
        │
        否
        ▼
   走会话通道（校验 CSRF）
```

之所以令牌不做 CSRF：CSRF 的前提是"浏览器会自动带上凭据"，
而 `Authorization` 头不会被自动附带，威胁模型不成立。

### 2.3 三道闸（叠加生效）

令牌的能力**必须小于**人，否则 Agent 就是在放大人的权限：

1. **路径白名单**——只允许
   `/admin/jobs`、`/admin/screenings`、`/admin/candidates`、
   `/admin/offline-interviews`、`/admin/summary`、`GET /auth/me`。
   账号管理、公司档案、模板、Offer、会话全文一律 403。
2. **禁止 DELETE**——Agent 只该"查、加、改"，删除是人的决定。
3. **写操作要求 `write` scope**——只读令牌连上传都发不出去。

额外的两条不变式：

- **令牌不能签发新令牌**（`/auth/tokens` 不在白名单），否则只读令牌可自我提权；
- **角色限制照常生效**——令牌只是换了个身份通道，`require_role` 依然拦截
  （店长的令牌仍然进不了初筛榜）。

### 2.4 scope 语义

| scope | 覆盖动作 |
|---|---|
| `read` | 查岗位、查榜单、查汇总 |
| `write` | 上传简历、发面试链接、约复试、到店直招（**隐含 read**） |

`write` 自动补 `read`：能写的人必然要读，避免出现"能传不能查"的怪配置。

## 3. 用法

### 3.1 签发（管理员）

```bash
cd backend

# 只读（Agent 只查数据）
python scripts/create_pat.py create --username hr01 --name "MacBook-招聘助手" --scopes read

# 可写（能传简历、发面试链接）
python scripts/create_pat.py create --username hr01 --name "MacBook-招聘助手" --scopes read,write --days 90
```

### 3.2 查看与回收

```bash
python scripts/create_pat.py list --username hr01
python scripts/create_pat.py revoke --token-id <id>
```

也可走接口（本人操作，需浏览器会话）：

```
POST   /auth/tokens        签发（响应含原文，仅此一次）
GET    /auth/tokens        列出本人全部令牌（不含原文）
DELETE /auth/tokens/{id}   撤销（只能撤销自己的）
```

### 3.3 客户端调用

```bash
curl -H "Authorization: Bearer ia_pat_xxx" https://hr.example.com/admin/jobs
curl -H "Authorization: Bearer ia_pat_xxx" https://hr.example.com/auth/me
```

## 4. 审计

`AdminWriteAuditMiddleware` 会区分操作来源，写进 `audit_logs.detail`：

```
status=200; actor=human
status=200; actor=agent; pat=MacBook-招聘助手(ia_pat_5zpf)
```

用 `pat=` 而不是 `token=` 是刻意的：`audit.scrub()` 会把 `token=xxx` 整段打码，
那样就丢掉了"是哪个令牌"的关键线索；而 12 位前缀不足以冒用，足够定位到设备。

## 5. 与账号生命周期的关系

| 事件 | 对令牌的影响 |
|---|---|
| 改密码 | **不影响**（PAT 独立于密码，Agent 不会因 HR 改密掉线） |
| 停用账号 | **立即全部失效**（`resolve_token` 里校验 `is_active`） |
| 删除账号 | 记录随 `ON DELETE CASCADE` 删除 |
| 撤销单个令牌 | 立即失效，幂等 |

停用账号是"一键回收所有设备凭据"的兜底，不需要逐个撤销。

## 6. 已知限制

- **无幂等键**：写接口重试仍可能重复入库（与令牌无关，是上传接口本身的问题）。
- **无 IP 绑定**：令牌可异地使用，仅记录 `last_used_ip` 供事后排查。
- **无自动轮换**：到期需重新签发，没有 refresh 机制。
- **前端页面未做**：目前签发/撤销走 CLI 或接口，后台界面留待二期。

## 7. 测试

`backend/tests/test_pat.py`，22 个用例覆盖：

- 身份通道（Bearer 可读、写接口无需 CSRF）
- 能力边界（只读不能写、禁 DELETE、白名单外 403、不能自我签发、角色限制保留）
- 生命周期（撤销 / 过期 / 停用账号立即失效）
- 存储安全（只存 sha256、无字段含原文）
- scope 规范化（write 隐含 read、非法 scope 422、有效期边界）
