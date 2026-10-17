# 招聘 BI 看板

一个**独立的、只读的**招聘数据分析看板，用于大屏展示 / 投屏 / 内网分享。

- 端口：**8500**（与业务前端 3000、后端 8000 完全隔离）
- 页面：单页 HTML + 本地 ECharts，**不依赖外网 CDN**，断网也能正常渲染
- 数据：直连 PostgreSQL，只发 `SELECT`

---

## 为什么单独做一个服务

不是把后台页面抄一遍，而是有三个实打实的理由：

1. **鉴权边界**。现有后台接口全部挂在 `require_user` 登录态后面。大屏挂在墙上，
   不应该为此在后台开一个公开口子，也不该让大屏去维护后台 Session。
2. **口径不同**。后台接口服务于「操作」（分页、筛选、全字段）；BI 服务于「看趋势」
   （聚合、分桶、转化率）。两者混在一起，查询和 UI 都会别扭。
3. **故障隔离**。看板挂了不影响招聘业务；看板被反复刷新也不该拖垮业务库连接
   （只读连接 + 60 秒缓存）。

---

## 目录结构

```
bi/
├── app.py                # 只读数据服务（FastAPI）
├── static/
│   ├── index.html        # 看板页面（单文件，内联 JS/CSS）
│   └── echarts.min.js    # 本地 ECharts（不依赖 CDN）
├── Dockerfile
├── requirements.txt      # 只有 3 个依赖
└── README.md
```

---

## 本地运行

```bash
cd bi

# 方式一：复用后端已装好的依赖（推荐，省得再建虚拟环境）
../backend/.venv/bin/uvicorn app:app --host 127.0.0.1 --port 8500

# 方式二：独立虚拟环境
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
uvicorn app:app --host 0.0.0.0 --port 8500
```

然后打开 <http://127.0.0.1:8500/>。

默认读 `DATABASE_URL`（与业务后端同一个变量），所以只要根目录的 `.env` 配好了就能直接跑。

---

## Docker 部署

`docker-compose.yml` 里已经加好了 `bi` 服务：

```bash
# 只重建并启动看板（不影响 api / web）
docker compose up -d --build bi

# 看日志
docker compose logs -f bi
```

启动后访问 `http://<服务器IP>:8500/`。

腾讯云轻量服务器记得在**防火墙 / 安全组**放行 8500 端口。

### 只用 Docker 跑看板（不含其它服务）

如果看板要单独放一台机器：

```bash
docker build -t interview-bi ./bi
docker run -d --name interview-bi -p 8500:8500 \
  -e DATABASE_URL="postgresql://user:pass@<db-host>:5432/interview" \
  interview-bi
```

---

## 环境变量

| 变量 | 默认值 | 说明 |
|---|---|---|
| `BI_DATABASE_URL` | 取 `DATABASE_URL` | BI 专用连接串（优先级更高） |
| `DATABASE_URL` | `postgresql://interview:interview@localhost:5432/interview` | 复用业务后端的变量 |
| `BI_ACCESS_TOKEN` | 空 | 设置后访问需带 `?token=xxx`，校验通过写 12 小时 Cookie |
| `BI_DEFAULT_SCOPE` | `all` | `all`=全部数据；`real`=排除「【联调】/【演示】/测试」岗位 |
| `BI_CACHE_TTL` | `60` | 聚合结果缓存秒数，`0` 表示禁用 |

### 关于默认口径

系统试运行期库里以联调数据为主，所以默认 `all`，看板一打开就是满的。

**正式对外展示前建议改成 `real`**（只统计真实招聘岗位）：

```yaml
# docker-compose.yml
environment:
  BI_DEFAULT_SCOPE: real
```

页面上也可以随时手动切，切换会同步到 URL，例如：

```
http://<IP>:8500/?scope=real&days=90
```

大屏可以直接把固定视图写死在 URL 里，开机自启浏览器指向它即可。

---

## 只读保证（三层）

「只能看」不是靠 UI 藏按钮，而是三层兜底：

1. **代码层**：`app.py` 里没有任何 `INSERT / UPDATE / DELETE`，路由只有 `GET`。
2. **连接层**：连接建立后置 `conn.read_only = True`，psycopg 会发出
   `SET SESSION CHARACTERISTICS AS TRANSACTION READ ONLY`，之后任何写操作由 Postgres 直接拒绝。
3. **账号层（推荐在生产启用）**：给 BI 单独开一个只有 `SELECT` 权限的数据库账号。

创建只读账号：

```sql
-- 在 interview 库里执行
CREATE ROLE bi_reader WITH LOGIN PASSWORD '<换成强密码>';

GRANT CONNECT ON DATABASE interview TO bi_reader;
GRANT USAGE ON SCHEMA public TO bi_reader;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO bi_reader;

-- 之后新建的表也自动授权
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO bi_reader;
```

然后把连接串换成：

```
BI_DATABASE_URL=postgresql://bi_reader:<密码>@postgres:5432/interview
```

这样即使有人改了代码、或者容器被入侵，也写不进业务库。

---

## 统计口径

看板上的数字要能对得上账，口径写在这里：

### 漏斗（按人）

以**时间窗内建档的候选人**为一个队列，追踪他们**当前**走到了哪一步：

```
候选人建档 → 完成简历初筛 → 完成 AI 面试 → 完成线下复试
          → 发出 Offer → Offer 接受 → 确认入职
```

- 按**候选人去重**：一个人投了 3 个岗位也只算 1 个，保证漏斗严格单调递减。
- 某一层为 0 而下一层有数（例如「初筛 0 → AI 面试 7」），说明这批人跳过了该环节
  （管理端支持直接发起 AI 面试）。此时该层转化率显示 `—`，不做无意义的除法。

### Offer 接受率 / 到岗率（按记录）

与业务后端 `services/offer.py` 一致，按 **Offer 记录数**算：

- 接受率 = `accepted` ÷ (`accepted` + `declined` + `expired`)，其中 `accepted` 含
  其后转为 `onboarded` / `no_show` 的记录
- 到岗率 = `onboarded` ÷ `accepted`

> 所以页面上「接受率 82%」和漏斗里「按人 100%」并不矛盾：
> 前者看的是「发出的每一份 Offer」，后者看的是「收到过 Offer 的每一个人」。

### 趋势（按事件）

按天统计**当天实际发生**的事件（建档 / AI 面试完成 / 发 Offer / 入职），
与漏斗的队列口径互补。时区统一按 `Asia/Shanghai` 切日。

### 分数分桶

四个环节（简历初筛 / AI 面试 / 线下复试 / 最终得分）统一按百分制分 6 档：
`0-49 / 50-59 / 60-69 / 70-79 / 80-89 / 90-100`。

### 测试数据过滤

`scope=real` 时排除岗位名含 `【联调】`、`【演示】`、`测试` 的岗位及其全部下游数据。
过滤以**岗位**为锚点，因为所有链路数据最终都挂在岗位下。

---

## 接口

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/` | 看板页面 |
| GET | `/api/dashboard?days=30&scope=all` | 全部图表数据（一次拉完） |
| GET | `/api/health` | 健康检查（含数据库连通性） |

`days` 支持 `7 / 30 / 90 / 0`（0 = 全部时间），`scope` 支持 `real / all`。

响应统一 `{"code":0,"message":"ok","data":{...}}`，与主项目一致。

---

## 常见问题

**页面打不开 / 一直转圈**

```bash
curl http://127.0.0.1:8500/api/health
```

返回 503 说明连不上数据库，检查 `DATABASE_URL` 里的 host（容器内要用服务名 `postgres`，
不能用 `localhost`）和密码。

**数字和后台对不上**

对照上面的「统计口径」。最常见的两个差异来源：

1. 时间窗：后台列表默认全量，看板默认近 30 天；
2. 口径：后台列表是**记录数**，看板漏斗是**人数**（一人多岗位只算一次）。

**图表空白**

先看浏览器控制台。如果 `/api/dashboard` 返回 500，响应里 `message` 会带上具体的 SQL 错误。

**auto refresh 不生效**

页面在后台标签页时会暂停轮询（省资源），切回前台会立刻补一次。

---

## 维护

改动后重建镜像：

```bash
docker compose up -d --build bi
```

排查数据问题可以直接跑 SQL，看板本身不会写库，怎么查都安全：

```bash
docker exec -it interview-agent-postgres psql -U interview -d interview
```
