# GitHub 站内 SEO 方案 — interview-agent（当前仓库名：`hragent`）

> 适用仓库：`github.com/WEMoon957/hragent`
> 目标用户（按你的填写）：**小白**（想开箱即用、不会写代码的 HR / 门店店长等最终使用者）+ **FDE**（本文理解为会 fork + 自部署的技术型用户，Frontend/Fullstack Dev / Engineer；若你指的是别的含义请纠正）
> 主要语言：**Python（后端 FastAPI + LangGraph）+ TypeScript（前端 Vue3）**
> 本文所有英文文案均可直接复制；竞争强度标「估算」，热度基于 GitHub 搜索逻辑与实测 `total_count` 推断，未编造具体数字。

---

## 0. 现状基线（为什么现在 SEO 几乎是 0）

通过 GitHub API 拉取仓库现状：

| 项 | 现状 | SEO 影响 |
| --- | --- | --- |
| 仓库名 | `hragent` | ❌ 完全不包含任何关键词，用户搜不到 |
| Description | 空 | ❌ 失去 60 字符黄金展示位 + 搜索匹配 |
| Topics | 空 | ❌ 失去 topic 搜索入口与标签展示 |
| Homepage | 空 | ❌ 失去外部官网权重 |
| LICENSE | 无 | ❌ 降低可信度与 fork 意愿 |
| Stars / Forks / Issues | 0 / 0 / 0 | ❌ 无任何社交证明 |
| README 首屏 | 纯中文长段落，无英文关键词、无徽章、无 TOC、无快速安装 | ❌ 国际/英文搜索几乎 0 命中 |

**结论**：这是一座未开发的金矿——只要补齐「名称 + Description + Topics + 双语首屏 README + LICENSE」，就能让搜索可见度从 0 直接上一个数量级。下面的方案就是按「投资回报率」排序的。

---

## 1. 关键词调研

### 1.1 实测搜索量水位（GitHub `search/repositories`，2026-10-17 抓取）

| 查询词 | 命中仓库数（total_count，估算水位） | 说明 |
| --- | --- | --- |
| `interview agent` | ~8,770 | ⚠️ 但绝大多数命中是「面试题库 / 八股文 / 面试准备」，**不是** AI 面试官产品，真实竞品极少 |
| `ai recruitment` | ~14,456 | 同上，混有大量招聘平台/课程，专用「AI 招聘 Agent」稀缺 |
| `langgraph` | ~85,818 | 框架名，非直接竞品，但作为 topic 能精准触达 LangGraph 开发者群体 |
| `ai recruiter` / `recruitment automation` | 远小于上面（niche 蓝海） | 我们真正的赛道，竞争低、意图精准 |

### 1.2 关键词表（10–15 个，按类型 + 竞争 + 优先级）

> 竞争判定：高 = 总命中数万级且大量泛内容；中 = 有量但可差异化；低 = 长尾精准、容易排上。
> 优先级：P0 = 必须埋入名称/Description/Topics/README 首屏；P1 = 埋入 README 章节与 Topics；P2 = 长尾，埋入正文与文档。

| # | 关键词（含同义） | 类型 | 竞争 | 优先级 | 理由 |
| --- | --- | --- | --- | --- | --- |
| 1 | `ai recruiter` / `AI 招聘` | 核心 | 中 | **P0** | 最贴合产品本质，意图精准，竞品少（蓝海） |
| 2 | `recruitment automation` / `招聘自动化` | 核心 | 中 | **P0** | 直击“自动化招聘流程”卖点，英文搜索友好 |
| 3 | `ai interview` / `AI 面试` | 核心 | 高* | **P0** | 量大但被“面试题库”污染；我们靠「interview **agent** / **automation**」长尾避其锋芒 |
| 4 | `langgraph` | 核心（技术信号） | 中 | **P0** | 85k 量级但作为 topic 能精准触达框架用户，且我们确用 LangGraph |
| 5 | `llm hiring` / `llm` | 核心（技术信号） | 中 | P1 | 大模型招聘/LLM 应用热点，利于技术圈传播 |
| 6 | `hr automation` / `HR 自动化` | 核心 | 中 | P1 | 目标买家（HR/管理者）常用词 |
| 7 | `resume screening` / `简历初筛` | 长尾 | 低 | **P1** | 我们的核心功能之一，竞争低、转化高 |
| 8 | `interview scoring` / `面试评分` | 长尾 | 低 | P1 | 差异化功能点，易排上 |
| 9 | `feishu` / `lark` / `飞书` | 长尾（差异化） | 低 | **P1** | 我们的独家集成，几乎无竞品，强差异化 |
| 10 | `agentic recruitment` | 长尾 | 低 | P2 | 趋势词，早期占领 |
| 11 | `offer management` / `入职闭环` | 长尾 | 低 | P2 | 全流程卖点，竞品少 |
| 12 | `walk-in interview` / `到店直招` | 长尾 | 低 | P2 | 餐饮/零售线下场景独家词 |
| 13 | `restaurant hiring` / `餐饮招聘` | 长尾（垂直） | 低 | P2 | 垂直行业词，精准吸目标用户 |
| 14 | `candidate evaluation` / `候选人评估` | 同义 | 中 | P2 | 与 resume screening 互补 |
| 15 | `fastapi` / `vue` | 同义（技术信号） | 中 | P2 | 技术栈词，利于开发者 fork |

**同义词/近义词补充（用于 README 正文自然覆盖，不堆砌）**：recruiter bot、hiring agent、talent screening、CV screening、HR tech、HRIS、AI agent、multi-agent。

> 建议的“搜索主战场”策略：**不与 `interview prep` 红海硬刚**，主投 `ai recruiter` + `recruitment automation` + `langgraph` + `feishu` 四条精准赛道，再用长尾功能词兜底。

---

## 2. 仓库命名建议

当前 `hragent` 对搜索极不友好。候选名（均含核心词、简短好拼）：

| 候选名 | 优点 | 缺点 | 推荐度 |
| --- | --- | --- | --- |
| **`interview-agent`** | 含 `interview`+`agent` 双核心词；与本地目录/前端包名一致；好记好拼 | 已被部分“面试题库”占用近似名，但 `.io/.dev` 不同；我们靠描述差异化 | ⭐ 首选 |
| `ai-recruiter` | 直击核心词 `ai`+`recruiter`，搜索意图最准 | 偏业务、弱化“技术/agent”属性；开发者向弱 | ⭐⭐ |
| `ai-interview-agent` | 关键词最全（ai+interview+agent） | 偏长（17 字符），口语传播略吃亏 | ⭐⭐ |
| `recruitment-agent` | `recruitment`+`agent`，蓝海精准 | 弱化“面试”动作 | ⭐⭐ |
| `hiring-agent` | `hiring`+`agent`，简短 | `hiring` 搜索量略低于 `recruitment` | ⭐ |

**重要警示（改名有运维代价）**：仓库改名后 Git URL 会变（`github.com/WEMoon957/hragent` → `…/interview-agent`）。你的服务器靠 `git pull origin main` 自动部署，**SSH 地址不会自动重定向**，需在服务器执行 `git remote set-url origin git@github.com:WEMoon957/interview-agent.git` 并确认 `WEBHOOK`/部署脚本里的仓库路径。因此：
- 若改名叫 `interview-agent`：我可一并用服务器通道改远程地址 + 重启部署验证（需你确认）。
- 若暂时不改名：用 `interview-agent` 作为 **Description 与 README 主标题**里的展示名，搜索仍能吃到 `interview`+`agent` 词，损失可控。

---

## 3. About（仓库简介）优化

### 3.1 Description 候选（≤350 字符；前 60 字符内埋核心词；一句话讲清“是什么+解决什么+给谁用”）

> 以下均以“展示名 interview-agent”为前提；前 60 字符已加粗示意。

1. **AI recruiter & interview agent** that automates hiring for restaurants/stores: resume screening → AI interview → scoring → Offer, with Feishu/Lark alerts. 中文：餐饮门店 AI 招聘闭环。
2. **Recruitment automation** via an LLM interview agent (LangGraph + FastAPI + Vue). Screen resumes, auto-interview candidates, score, and close the Offer-to-onboard loop. Built for HR & store managers.
3. **LangGraph-powered AI interview agent** for end-to-end recruitment: resume screening, conversational AI interviews, structured scoring, and Feishu/Lark HR notifications.
4. **AI hiring agent** that runs the full recruitment loop — resume screening, AI interviews, scoring, Offer & onboarding — with one-click Feishu/Lark push. Great for high-volume frontline hiring.
5. Open-source **AI recruiter** (LangGraph + FastAPI + Vue3): automate resume screening, conversational interviews, structured scoring, and Offer management, integrated with Feishu/Lark.

> 推荐用 **#1 或 #2**：#1 把 `ai recruiter`+`interview agent` 前置，#2 把 `recruitment automation` 前置且句子最顺。两版都不堆砌、自然含 3+ 关键词。

### 3.2 推荐 Topics（15 个，按优先级；GitHub 单仓上限 20）

| 优先级 | Topic | 作用 |
| --- | --- | --- |
| P0 | `ai-agent` | 最大流量入口，覆盖“AI 智能体”搜索 |
| P0 | `langgraph` | 精准触达框架开发者（85k 量级池） |
| P0 | `recruitment` | 核心领域词 |
| P0 | `ai-recruiter` | 核心产品词，蓝海 |
| P0 | `hr-automation` | 目标买家词 |
| P1 | `interview` | 领域词（注意与题库红海共存，但仍是必要覆盖） |
| P1 | `resume-screening` | 核心功能长尾词 |
| P1 | `feishu` | 差异化集成词（独家） |
| P1 | `llm` | 技术热点词 |
| P1 | `fastapi` | 技术栈词，利于 fork |
| P2 | `vue` | 前端技术栈词 |
| P2 | `offer-management` | 全流程卖点长尾 |
| P2 | `candidate-evaluation` | 功能同义词 |
| P2 | `agentic-ai` | 趋势词 |
| P2 | `chinese` | 标注主要受众/文档语言，利于中文圈检索 |

> 备选可加：`lark`、`hiring`、`recruiting`、`rag`（若后续接 RAG）。不要加与项目无关的蹭量 topic（如 `machine-learning` 泛词），会被判定低质。

### 3.3 Homepage 建议

- 若有线上 Demo：`https://你的域名` 或 Demo 页。
- 当前没有公网站点时：**填 BI 看板或前端 Demo 地址**（你已有 `http://129.204.61.73:3000` 与 `:8500` BI）。建议填 `http://129.204.61.73:3000`（候选/HR 入口），并补一句“Demo (username/password 见 README)”。
- 若都不想暴露：至少填项目文档站点（可用 GitHub Pages 把 `docs/` 发布成站点）。

> ⚠️ 这些 About 字段（Description/Topics/Homepage）**当前 GitHub MCP 无法修改，且本机未装 `gh`**。我已在下文第 6 步给出“30 秒粘贴”直链与现成文案，你到 `Settings` 页粘贴即可。

---

## 4. README 优化

### 4.1 推荐目录结构（H1/H2/H3，标题即埋词）

```
# AI Interview Agent · 智能招聘闭环系统   ← H1：含 ai + interview + agent
> 一句话定位（含 ai recruiter / recruitment automation / feishu）

[徽章区] [GIF/截图占位] [Quick Install]

## Table of Contents   ← 锚点链接
## What is this? / 项目简介        ← 埋：ai recruiter, recruitment automation
## ✨ Features                    ← 埋：resume screening, interview scoring, offer management, feishu
## 🎯 Who is it for? / 适用场景    ← 埋：restaurant hiring, HR, store manager
## 🏗️ Architecture / 系统架构      ← 埋：langgraph, fastapi, vue
## 🤖 AI Interview Engine (LangGraph)  ← 埋：langgraph, stateful, structured scoring
## 🔄 Recruitment Workflow / 招聘闭环
## 🚀 Quick Start                 ← 埋：docker, fastapi
## 🐳 Deployment / 部署            ← 埋：docker compose
## 🛠️ Tech Stack
## 📊 Data Model & API
## 🗺️ Roadmap
## 📚 Documentation
## 🤝 Contributing
## 📄 License
```

要点：每个 H2 都带一个英文关键词；正文用「英文术语 + 中文解释」让中英文搜索都能命中。

### 4.2 首屏（不滚动即可见）文案

- **标题**：`# AI Interview Agent` 副标题 `智能招聘闭环系统 for Restaurants & Stores`
- **一句话定位**：`An open-source AI recruiter that automates the full hiring loop — resume screening → AI interview → structured scoring → Offer & onboarding — with Feishu/Lark HR alerts.`（中文一句兜底）
- **徽章**：`License` `Python` `Vue` `LangGraph` `CI` `Last Commit` `Repo Size`（用 shields.io / github 自带）
- **GIF/截图占位**：放一张「HR 后台榜单 / 候选人面试页」GIF（建议 30–60 秒录屏循环），若无则用 `![screenshot](docs/screenshot.png)` 占位并在 PR 说明里待补。
- **Quick Install（首屏直接可见）**：
  ```bash
  git clone https://github.com/WEMoon957/hragent.git
  cd hragent && cp .env.example .env && docker compose up -d
  ```

### 4.3 关键词分布建议（自然融入，禁止堆砌）

- **密度**：核心词（ai recruiter / recruitment automation / langgraph / feishu）在全文出现 3–6 次即可，功能长尾词各 1–2 次；README 总量控制在“读起来像人写”的程度。
- **位置权重**：标题/首屏 > H2 标题 > 第一段 > 正文 > 代码注释。把最强词放在「标题 + 首段 + Features 列表」三处。
- **写法示例（好 vs 差）**：
  - ✅ “Built on **LangGraph**, this AI interview agent runs a stateful, resumable conversation…”
  - ❌ “langgraph langgraph langgraph ai recruiter ai recruiter recruitment automation……”（堆砌会被降权 + 拉低点击率）
- **双语**：英文标题 + 中文小节说明，既吃英文搜索也吃中文搜索；中文用户也能看懂。

### 4.4 社交预览图（Social Preview，1280×640）建议

- **左/上**：大标题 `AI Interview Agent`；副标题 `Open-source AI recruiter for frontline hiring`。
- **中部功能标签（chip）**：`Resume Screening` · `AI Interview` · `Scoring` · `Offer Loop` · `Feishu/Lark`。
- **底部技术栈徽章**：`LangGraph` `FastAPI` `Vue 3` `PostgreSQL` `Docker`。
- **配色**：深色底 + 高对比亮色字（与仓库主题一致），含一个面试对话小插画/图标。
- 我已在仓库生成 `docs/social-preview.png`（见 PR），你到 `Settings → Social preview` 上传即可；不传时 GitHub 会自动用首图，但自定义点击率更高。

### 4.5 容易忽略的 SEO 细节

- **图片 alt 文本**：所有 `![...](...)` 写描述性 alt，如 `![HR dashboard showing resume screening leaderboard](docs/hr-dashboard.png)`，alt 文本会被索引。
- **内部锚点链接**：TOC 用 `#what-is-this` 这类稳定锚点，提升停留与导航（停留时长是排名信号之一）。
- **双语而非纯机翻**：保留中文详情，英文做标题与摘要，避免“中文仓库英文标题”割裂；GitHub 搜索对仓库名/描述/README 正文均做索引。
- **README 长度与深度**：保持现在的技术深度（架构/代码/数据模型），这是“真实项目”信号，利于停留与 fork；不要为 SEO 删掉实质内容。
- **链接用相对路径**（`docs/xxx.md`）而非绝对，避免 404；外链加 `rel="noopener"`。

---

## 5. 其他排名信号清单

### 5.1 见效快（1–2 周内可完成，权重高）

- [ ] 补 Description + Topics + Homepage（第 3 节，30 秒）
- [ ] 合并本 PR 的「双语首屏 README + LICENSE」（直接提升搜索匹配与可信度）
- [ ] 上传 Social Preview 图（提升点击率 CTR，CTR 是排序信号）
- [ ] 打一个 **Release / Tag（v1.0.0）**——有 Release 的仓库搜索权重与信任度更高
- [ ] 自己先 star + 让 3–5 个朋友/同事 star/fork（真实社交证明；**不是买**）
- [ ] 回复/关闭现有 issue；没有 issue 就开 2–3 个“good first issue”引导社区
- [ ] 在 `README` 顶部加 GIF/真实截图（提升停留）

### 5.2 长期做（持续积累权重）

- [ ] 保持 commit 活跃（每周至少 1–2 次实质提交；长期零提交会被降权）
- [ ] 规范发布 Release（语义化版本 + 变更说明）
- [ ] 开启 **Discussions**（社区活跃度信号）
- [ ] 外部反向链接：在掘金/知乎/CSDN/Dev.to/Medium/X(LinkedIn) 写“用 LangGraph 做 AI 面试官”这类文章，链回仓库
- [ ] 提交到 **awesome 列表**（如 `awesome-langgraph`、`awesome-ai-agents`、`awesome-hr-tech`）——带来精准流量与背书
- [ ] 完善 `CONTRIBUTING.md`、`CODE_OF_CONDUCT.md`、CI 绿勾，提升“可维护项目”观感
- [ ] 多语言 README（en/zh）或英文摘要，扩大国际搜索面

### 5.3 违规操作（绝对不要做，附后果）

| 违规行为 | 后果 |
| --- | --- |
| **买 star / 刷 fork / 刷 watch** | 违反 GitHub ToS，轻则清空异常 star、重则**封号**；算法识别异常增长会降权甚至把仓库标记为 spam |
| **star/fork 互刷群、机器人** | 同上；且“异常社交图谱”比数量更易被检测 |
| **关键词堆砌**（Description/README/Topics 硬塞） | 降低点击率 + 被判定低质，反而降权 |
| **无关 topic 蹭量**（如挂 `bitcoin`/`game` 等） | 被举报后移除 + 降权 |
| **克隆/伪装成知名项目** | DMCA / 封号 |
| **自动化批量发 issue/评论引流** | 限流 + 封号 |
| **隐藏文字/cloaking（对爬虫与用户展示不同内容）** | 直接封禁 |

> 原则：**只做“真实价值 + 真实传播”**。本方案全部动作均为白帽，可长期安全累积权重。

---

## 6. 落地执行清单（Checklist，按优先级排序）

### A. 你现在就能做（About 元数据，MCP 改不了，去 Settings）
- [ ] 打开 `https://github.com/WEMoon957/hragent/settings`
- [ ] Description 粘贴第 3.1 节 #1 或 #2
- [ ] Topics 粘贴第 3.2 节 15 个（一行一个）
- [ ] Homepage 填 `http://129.204.61.73:3000`（或你的域名）
- [ ] `Settings → Social preview` 上传 `docs/social-preview.png`

### B. 我已帮你提到 PR（审阅后合并）
- [ ] 审阅 PR `#xx`：双语首屏 README + 新增 MIT LICENSE
- [ ] 合并 PR（⚠️ 合并会触发服务器自动部署一次，仅重建、无害；如需先停自动部署请告知）
- [ ] （可选）确认是否**改名** `hragent → interview-agent`：若改，回复我，我用服务器通道改 `git remote` 并重启验证

### C. 一周内（低门槛高回报）
- [ ] 打 Release `v1.0.0`（含简短 changelog）
- [ ] 自己 star + 找 3–5 人真实 star/fork
- [ ] 补一张 HR 后台 / 候选人面试页 GIF 到 README 首屏（替换占位）
- [ ] 开 2–3 个 good first issue

### D. 长期
- [ ] 每周保持 commit；规范 Release
- [ ] 写 1 篇技术文（掘金/知乎/Dev.to）回流外链
- [ ] 申请加入 `awesome-langgraph` / `awesome-ai-agents` / `awesome-hr-tech`
- [ ] 开启 Discussions，沉淀社区问答

---

### 附：本次已落地的产出文件
- `docs/GITHUB-SEO-PLAN.md`（本方案）
- `docs/social-preview.png`（社交预览图，1280×640）
- 分支 `seo/readme-optimization` 的 PR：`README.md`（双语首屏重写）+ `LICENSE`（MIT）
