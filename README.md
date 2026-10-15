# 面试智能体（AI Interview Agent）

面向**餐饮门店一线招聘**的 AI 招聘闭环系统：候选人通过与 AI Agent 多轮对话完成面试，Agent 依据岗位 JD 与简历生成/追问问题并逐题结构化评分；HR 在后台完成**简历初筛 → AI 面试 → 线下复试 → 汇总落库**，并在达标时自动把结果推送到飞书；店长可走**到店直招**分支并上传整场录音由飞书妙记转录、产出 AI 参考分；最后补齐 **Offer → 入职闭环**（审批 → 发链接 → 候选人确认 → 办入职）。

> 本仓库同时包含「MVP 核心面试引擎（LangGraph）」与「完整招聘业务闭环」。下文第 1~4 节讲面试引擎原理，第 5 节起讲业务闭环。

---

## 1. 为什么选 LangGraph（而非单独用 LangChain）

| 需求特征 | 实现方式 |
| --- | --- |
| 多轮有状态对话（问题流转、追问、进度） | LangGraph 状态图 + Checkpointer |
| 面试中断后恢复（候选人中途退出再进入） | Checkpointer 按 `thread_id` 恢复状态 |
| 条件分支（追问 / 下一题 / 结束） | 条件边 `route` |
| 等待候选人输入 | `interrupt` / 人工介入节点 |
| 逐题评分、维度汇总 | 节点内结构化输出（Pydantic） |
| 可观测、可调试（每个节点的输入输出与耗时） | LangSmith / LangGraph 自带追踪 |

结论：**LangGraph 负责编排（状态机），LangChain 负责生态组件（模型封装、Prompt 模板、结构化输出、文档加载）**，两者配合使用，不是二选一。

## 2. 整体架构

```mermaid
flowchart LR
    subgraph Client[前端 Vue3 + Element Plus]
        A[候选人面试页 /i/token]
        B[候选人 Offer 确认页 /o/token]
        C[HR 管理后台 /console]
    end
    subgraph Server[后端 FastAPI]
        D[管理 / 候选端 REST API]
        E[LangGraph 面试图]
        F[招聘流水线服务<br/>初筛 / 汇总 / 到店直招]
        G[Offer 状态机服务]
        H[简历解析 + 公司文化档案]
    end
    subgraph Store[存储]
        I[(PostgreSQL)]
        J[(上传文件盘)]
    end
    subgraph Feishu[飞书生态]
        K[HR 通知 / 待办]
        L[妙记轮询转录]
    end
    subgraph Model[模型层]
        M[LLM 提供商<br/>DeepSeek / Qwen / GPT / GLM / Claude]
    end

    A -->|SSE 流式对话| D
    B -->|确认 Offer| D
    C -->|CRUD + 评分报表| D
    D --> E
    D --> F
    D --> G
    H --> E
    E <-->|Checkpointer| I
    F --> I
    G --> I
    F --> J
    F -->|推送/转录| K
    F -->|轮询| L
    E -->|REST 兼容接口| M
    F --> M
    G --> K
```

## 3. LangGraph 面试流程图

```mermaid
stateDiagram-v2
    [*] --> load_context: 创建会话(岗位+简历+问题集)
    load_context --> ask_question
    ask_question --> wait_answer: interrupt 等待候选人
    wait_answer --> evaluate: 候选人提交回答
    evaluate --> route
    route --> ask_question: 追问(回答质量不足/有延伸空间)
    route --> ask_question: 下一题
    route --> final_report: 问题全部完成/时长上限
    final_report --> [*]: 生成评分报告
```

### 状态定义（核心）

```python
from typing import TypedDict, Annotated, List
from operator import add
from langgraph.graph.message import add_messages

class CandidateInfo(TypedDict):
    name: str
    resume_text: str
    job_id: str

class RubricDimension(TypedDict):
    key: str          # 如 "tech_depth"
    name: str         # 维名称，如 "技术深度"
    description: str  # 评分标准说明
    weight: float     # 汇总权重，如 0.4
    max_score: int    # 如 10

class Evaluation(TypedDict):
    question_id: str
    scores: dict     # {"tech_depth": 8, "communication": 7, ...}
    comment: str     # 评分理由
    follow_up_needed: bool

class InterviewState(TypedDict):
    thread_id: str
    candidate: CandidateInfo
    questions: List[dict]            # 预设问题集（支持单选 + 主观题混合）
    rubric: List[RubricDimension]    # 预设评分维度
    current_idx: int
    messages: Annotated[list, add_messages]
    evaluations: Annotated[List[Evaluation], add]
    follow_up_count: int
    status: str                      # running / finished
    report: dict
```

### 图构建（核心）

```python
from langgraph.graph import StateGraph, START, END
from langgraph.checkpoint.postgres import PostgresSaver
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

def _create_checkpointer() -> PostgresSaver:
    """进程级单例：psycopg 连接池 + 自建检查点表（多线程并发安全、坏连接自动重建）"""
    pool = ConnectionPool(
        conninfo=settings.checkpointer_dsn,
        min_size=settings.checkpointer_pool_min_size,
        max_size=settings.checkpointer_pool_max_size,
        kwargs={"autocommit": True, "row_factory": dict_row},  # PostgresSaver 硬性要求
        open=True,
    )
    saver = PostgresSaver(pool)   # conn 为 ConnectionPool 时不可再传 pipe
    saver.setup()                 # 幂等：自建 checkpoints/checkpoint_blobs/checkpoint_writes
    return saver

def build_interview_graph():
    g = StateGraph(InterviewState)

    g.add_node("load_context", load_context)
    g.add_node("ask_question", ask_question)
    g.add_node("evaluate", evaluate_answer)
    g.add_node("final_report", final_report)

    g.add_edge(START, "load_context")
    g.add_edge("load_context", "ask_question")
    g.add_edge("ask_question", "evaluate")
    g.add_conditional_edges(
        "evaluate",
        route,
        {"ask_question": "ask_question",
         "final_report": "final_report",
         "end": END}
    )
    g.add_edge("final_report", END)

    return g.compile(checkpointer=_create_checkpointer())
```

### 评分节点（结构化输出）

```python
from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate
from pydantic import BaseModel, Field

class AnswerScore(BaseModel):
    """按预设维度对单个回答评分"""
    scores: dict[str, float] = Field(description="维度key -> 得分(0~max_score)")
    comment: str = Field(description="评分理由，引用候选人原话佐证")
    follow_up_needed: bool = Field(description="是否需要追问")
    follow_up_question: str = Field(default="", description="追问问题")

def evaluate_answer(state: InterviewState) -> dict:
    llm = ChatOpenAI(model=LLM_MODEL, temperature=0.2)  # 评分用低温
    q = state["questions"][state["current_idx"]]
    doc = state["messages"][-1].content or ""

    prompt = ChatPromptTemplate.from_messages([
        ("system", """你是资深面试官。请根据评分维度对候选人的回答评分。
岗位JD: {job}
候选人简历: {resume}
评分维度: {rubric}
当前问题: {question}
候选人回答: {answer}

要求:
1. 逐维度打分，维度分上限为该维度 max_score
2. comment 中引用候选人原话作为评分依据
3. 回答明显缺失/敷衍/有延伸价值时置 follow_up_needed=True"""),
    ])

    chain = prompt | llm.with_structured_output(AnswerScore)
    result = chain.invoke({
        "job": state["candidate"]["job_desc"],
        "resume": state["candidate"]["resume_text"],
        "rubric": format_rubric(state["rubric"]),
        "question": q["text"],
        "answer": doc,
    })
    return {"evaluations": [result.model_dump() | {"question_id": q["id"]}]}
```

### 路由节点

```python
from langgraph.graph import END

MAX_FOLLOW_UPS = 2  # 每题最多追问次数

def route(state: InterviewState) -> str:
    last = state["evaluations"][-1]
    if last["follow_up_needed"] and state["follow_up_count"] < MAX_FOLLOW_UPS:
        return "ask_question"          # 追问
    if state["current_idx"] + 1 < len(state["questions"]):
        return "ask_question"          # 下一题
    return "final_report"              # 收尾
```

## 4. 评分机制

1. **维度预设**（面试模板配置，示例）：

   | 维度 key | 名称 | 满分 | 权重 |
   | --- | --- | --- | --- |
   | tech_depth | 技术深度 | 10 | 0.40 |
   | experience | 项目经验 | 10 | 0.30 |
   | communication | 沟通表达 | 10 | 0.15 |
   | job_fit | 岗位匹配 | 10 | 0.15 |

2. **逐题评分**：`evaluate` 节点用低温 LLM + 结构化输出，逐维度打分并给出理由与原话佐证。
3. **追问机制**：回答质量不足时自动生成追问（每题追问上限可配置）。
4. **题型混合**：面试模板的问题集（`questions` JSONB）同时支持**主观题**与**单选题**（`type=choice`），单选题由 Agent 按选项判定正误并计入评分。
5. **汇总报告**：维度得分 = 各题维度得分均值 × 权重 → 综合分（百分制）→ 等级建议（强烈推荐 / 推荐 / 待定 / 不推荐）+ 总体评价与录用风险提示。
6. **后台展示**：HR 后台按候选人查看每题得分明细、维度雷达图、综合分与建议。

---

## 5. 招聘全流程闭环

> 详见 [`docs/全流程说明.md`](docs/全流程说明.md)。下图为一句话概括：HR 上传岗位文件夹，系统自动初筛排序并发面试链接到飞书；候选人自助完成 AI 面试；HR/店长线下复试打分；最后一键汇总落库并提醒 HR 办 Offer 与入职。

```mermaid
flowchart TD
    A0[准备：建岗位 + 配面试模板] --> A[① 简历初筛]
    A -->|推荐名单| B[② AI 面试]
    B -->|达标| C[③ 线下复试]
    C -->|人工打分| D[④ 汇总落库]
    D --> E[HR 复核 → 发 Offer → 办入职]

    A -.自动.-> A1[生成面试链接<br/>推卡片 + 建飞书待办]
    B -.自动.-> B1[达标推 HR 飞书]
    D -.自动.-> D1[推复核卡片<br/>写好汇总快照]
```

### 5.1 四个阶段

| 阶段 | 谁来做 | 系统做什么 | 落库 |
| --- | --- | --- | --- |
| ① 简历初筛 | HR 上传岗位文件夹 | 逐份解析简历 → AI 按 JD 多维打分 → 榜单降序；**达到门槛者自动生成 AI 面试链接**；推推荐卡片 + 建 HR 飞书待办 | `resume_screenings` |
| ② AI 面试 | 候选人自助 | 候选人打开链接 → 同意《安全知情协议》→ 多轮问答（含自动追问）→ 逐题评分 → 生成综合报告；**达标自动推 HR 飞书** | `interview_sessions` / `answer_evaluations` / `session_reports` |
| ③ 线下复试 | HR / 店长 | 后台「待 HR 处理」列表驱动 → 约面（时间/地点/面试官/角色）→ 面试后录入分数 + 评语 + 结论；**到店直招**：店长现场建档并可上传**整场面试录音** | `offline_interviews` |
| ④ 汇总落库 | HR 点一下按钮 | 聚合三阶段分数 → 加权最终分 → 写快照；**自动推「复核 + 办 offer / 入职」卡片** | `recruitment_summaries` |

### 5.2 岗位「免初筛」分支

岗位可标记 `skip_screening = true`。此时流程跳过 AI 打分：**上传简历即直接生成 AI 面试链接**（不做初筛排序），汇总时也不计初筛分（权重自动重新归一化）。

### 5.3 到店直招分支（现场面试 + 录音节点）

候选人直接到门店找店长面试，**没有简历、不走初筛、不发 AI 面试链接**。

- 入口：后台「线下复试」页 → **到店直招**（一次建档：岗位 + 姓名 + 手机号 + **来源渠道**）
- 店长可上传**整场面试录音**（一段文件，mp3 / m4a / mp4 / wav 等，≤50MB），支持内联播放与下载
- 建档后直接人工打分（同线下复试打分口径），同一候选人同岗位重复建档返回 409
- **录音转录**：店长用飞书妙记录制（标题填入系统生成的 6 位 `walk_in_code`）→ 后端 `minutes_poller` 定时轮询 `minutes search` → 短码匹配 → 拉逐字稿 → 调 LLM 产出**整场面试 AI 参考分**（与店长人工打分并存，不覆盖）
- 汇总里以 **来源 = 到店直招** 标识，最终分 = 线下打分（其余环节自动重新归一化）

> 录音存储：落盘到 `UPLOAD_DIR/audio/YYYY/MM/<uuid>.<ext>`，容器内为 `/app/uploads`；nginx 已放开 `client_max_body_size 100m`。

### 5.4 公司文化档案融入

- 后台「公司文化」页维护一份**全局单例**公司介绍（`company_profiles` 表，自由文字）。
- 发面试链接的四个入口（单个发起 / 批量邀请 / 免初筛直通 / 初筛达标自动发起）统一经 `snapshot_company_profile()` 把生效介绍**快照**进 `interview_sessions.company_profile_snapshot`。
- 候选人开场白自动带上公司介绍（超出 `OPENING_INTRO_MAX_CHARS=300` 截断）；**快照隔离**——改档案只影响之后发起的面试，已发出的会话沿用当时快照。
- 无介绍时全部走空值降级，链路行为与改造前一致。

### 5.5 评分口径（汇总）

- **初筛分**（百分制）：按 JD 对「技能匹配 / 经验相关 / 稳定性 / 素质与态度」四维打分，权重归一化后重算总分。
- **AI 面试分**（百分制）：逐题按模板 rubric 打分 → 维度均分 × 权重 → 百分制。
- **线下复试分**（百分制）：HR / 店长人工录入，可带维度明细；到店直招另可选「AI 参考分」（妙记逐字稿整场评分）。
- **最终加权分** = 初筛 × 0.2 + AI 面试 × 0.4 + 线下 × 0.4（**缺环节时对已有环节重新归一化，不惩罚**）。
- **档位**：≥80 强烈推荐 / ≥65 推荐 / ≥50 待定 / <50 不推荐。

## 6. Offer → 入职闭环（第⑤阶段）

在「④ 汇总落库」之后补上最后一环：**发起 Offer → 老板审批 → 发链接给候选人 → 候选人接受/婉拒 → HR 办理入职**。

### 状态机（10 态，服务层唯一事实源）

```
draft ──提交审批──> pending_approval ──审批通过──> sent ──候选人接受──> accepted ──HR办入职──> onboarded
                       │                            │                                    └──未报到──> no_show
                       └──驳回──> rejected          ├──候选人婉拒──> declined
                                 （可修改重提）       ├──超时未回──> expired
                                                     └──HR 撤回──> withdrawn
```

- **token 只在「审批通过」时生成**；`expires_at` = 发出后「次日本地 08:00」（`OFFER_CONFIRM_HOUR/TZ_OFFSET` 可配）
- 过期采用**惰性判定**（列表/详情/统计等读入口扫描 `expire_due_offers()`），不依赖定时任务
- 同一汇总同时只允许一条「进行中」Offer（数据库**部分唯一索引**兜底，服务层先校验并返回 409）
- 候选人确认页复用 `/o/{token}`，与面试页 `/i/{token}` 同先例
- 统计口径：接受率 = accepted / (accepted+declined+expired)；入职率 = onboarded / accepted
- **关键缺口（尚未做）**：HTTP 回调审批（方式 B）待开发；本机/单机用飞书长连接消费者（`scripts/offer_card_action_consumer.py`）已跑通

## 7. 飞书通知（5 张卡片 + 1 个待办 + Offer 多类卡片）

| # | 触发时机 | 形态 | 幂等 |
| --- | --- | --- | --- |
| ① | 简历初筛完成 | 蓝色卡片（岗位/统计/推荐名单+面试链接） | 每次上传一次 |
| ② | 简历初筛完成 | 飞书待办 Task（岗位+人数+链接） | 每次上传一次 |
| ③ | AI 面试收尾且综合分 ≥ 阈值 | Markdown 文本（分数/建议/维度） | `session_reports.notified_at` |
| ④ | 汇总落库完成 | 橙色卡片（建议发 offer / 复核） | `recruitment_summaries.notified_at` |
| ⑤ | 汇总落库完成 | 飞书待办 Task（待复核人数） | 同 ④ |
| ⑥ | Offer 各状态流转 | 7 类卡片（提交审批给老板 / 审批通过给 HR / 驳回 / 接受给 HR∪店长 / 婉拒·过期·未报到给 HR） | 按 Offer 状态 |

- **收件人**：`FEISHU_HR_OPEN_IDS`（指定人）与 `FEISHU_HR_DEPARTMENT_IDS`（按部门**实时解析在职成员**，新人自动纳入、离职自动过滤）并集去重。
- **双传输**：`cli`（复用本机 `lark-cli`，适合本机联调）/ `api`（飞书 Open API 直连，**服务器部署必须用这个**）。
- **失败策略**：尽力而为 —— 推送/待办异常只记日志，绝不影响业务落库与接口返回。
- **录音转录**另走飞书妙记 `minutes search/transcript` 只读接口（bot 身份，需 `minutes:minutes.search:read` 与 `minutes:minutes.transcript:export` scope）。

配置（见 `.env.example`）：

```bash
FEISHU_NOTIFY_ENABLED=true            # 总开关
FEISHU_NOTIFY_THRESHOLD=65            # AI 面试达标推送阈值
FEISHU_NOTIFY_TRANSPORT=cli           # cli | api（服务器改 api）
FEISHU_HR_OPEN_IDS=ou_xxx,ou_yyy      # HR 收件人（逗号分隔）
FEISHU_HR_DEPARTMENT_IDS=od_xxx       # HR 收件部门（od_ 开头，实时解析在职成员）
# FEISHU_LARK_CLI_PATH=               # 留空自动探测
# FEISHU_APP_ID / FEISHU_APP_SECRET   # transport=api 时必填
```

> **部署到服务器 / 容器时**：必须把 `FEISHU_NOTIFY_TRANSPORT` 改为 `api` 并填 `FEISHU_APP_ID` / `FEISHU_APP_SECRET`——默认的 `cli` 依赖本机 `lark-cli` 与 macOS keychain，Linux 容器里不可用。详见 [`docs/飞书HR通知接入说明.md`](docs/飞书HR通知接入说明.md)。
>
> ⚠️ 飞书待办需要应用具备 `task:task:write` 权限（见 §13 部署前置）。

通道自检（不经过面试流程，直接验证 HR 收件人与登录态）：

```bash
cd backend
.venv/bin/python scripts/send_feishu_test.py --dry-run      # 只看文案与配置
.venv/bin/python scripts/send_feishu_test.py                # 真发一条样例通知
```

## 8. 技术栈

| 层 | 选型 | 说明 |
| --- | --- | --- |
| 编排框架 | **LangGraph** | 面试状态机、Checkpointer 断点恢复、interrupt 等待输入 |
| LLM 接入 | **langchain-openai**（OpenAI 兼容协议） | 一个接口切换 GPT / DeepSeek / Qwen / GLM / Claude |
| 结构化输出 | Pydantic + `with_structured_output` | 评分结果、报告 schema 校验（纯文本 JSON + 手动解析 + 降级兜底） |
| 简历解析 | pdfplumber / python-docx | PDF、Word、纯文本简历（MVP 不向量化） |
| 后端 | FastAPI + SSE | REST 管理接口 + 流式对话输出 |
| 前端 | **Vue 3 + Vite + Element Plus + Pinia + vue-router + ECharts** | 候选人面试页 / Offer 确认页 / HR 管理后台（**非 React/Next.js**） |
| 数据库 | PostgreSQL 16 | 岗位、模板、候选人、会话、评分、初筛、线下、汇总、Offer（LangGraph PostgresSaver 复用） |
| 文件存储 | 本地磁盘（`UPLOAD_DIR`） | 面试录音落盘 |
| 部署 | Docker Compose | postgres + api + web（nginx 反代） |
| 可观测（可选） | LangSmith | 全链路追踪、每个节点耗时与 token 统计 |

**国内环境推荐默认组合**：`LLM_MODEL=deepseek-chat`（DeepSeek，OpenAI 兼容）或 `qwen-plus`（通义），无需代理。

## 9. 数据模型（关键表）

```
job_postings          岗位（id, title, jd_text, requirements, skip_screening, is_active）
interview_templates   面试模板（id, job_id, questions JSONB[选择题+主观题], rubric JSONB,
                            duration_limit, max_follow_ups, culture_fit JSONB）
candidates            候选人（id, name, email, phone, source_channel）
resumes               简历（id, candidate_id, file_url, parsed_text）
resume_screenings     简历初筛（id, total_score, dim_scores, recommendation, status,
                            session_id, batch_id, folder_name）
interview_sessions    面试会话（id, token, template_id, candidate_id, status, thread_id,
                            consent_agreed_at, company_profile_snapshot JSONB）
answer_evaluations    逐题评分（id, session_id, question_id, scores JSONB, comment, follow_up_needed）
session_reports       面试报告（id, session_id, dim_scores JSONB, total_score, verdict, summary, notified_at）
offline_interviews    线下复试/到店直招（id, candidate_id, job_id, scheduled_at, location,
                            interviewer, interviewer_role, score, decision, source[online|walk_in],
                            audio_path/name/size, walk_in_code, minutes_token,
                            transcript_status/text, ai_score JSONB）
recruitment_summaries 招聘汇总（id, candidate_id, job_id, screening/ai/offline 分数,
                            final_score, stage, is_complete, detail JSONB, notified_at）
company_profiles      公司文化档案（全局单例：id, content, updated_by）
offers               Offer（id, summary_id, candidate_id, job_id, status[10态], token,
                            expires_at, final_score, store_name, ...）
```

迁移版本：`0001_initial` … `0010_offer_onboarding`（Alembic，部署时自动 `alembic upgrade head`）。

## 10. API 概要

**岗位 / 模板**
```
GET,POST     /admin/jobs
PUT,DELETE   /admin/jobs/{job_id}
GET,POST     /admin/templates
DELETE       /admin/templates/{template_id}
```

**简历初筛**
```
POST         /admin/screenings/upload      # 岗位文件夹批量上传（direct_invite / screen / notify 可选）
GET          /admin/screenings             # 榜单（按分降序）
GET,DELETE   /admin/screenings/{screening_id}
POST         /admin/screenings/invite      # 批量发起 AI 面试
POST         /admin/screenings/notify      # 手动推送榜单给 HR
```

**AI 面试**
```
GET,POST     /admin/sessions
GET          /admin/sessions/{session_id}/report
DELETE       /admin/sessions/{session_id}
GET,POST     /interview/{token}            # 候选人获取上下文 / 提交回答
POST         /interview/{token}/consent    # 同意《安全知情协议》
POST         /interview/{token}/answer
```

**线下复试 / 录音节点 / 汇总**
```
GET,POST     /admin/offline-interviews
POST         /admin/offline-interviews/walk-in                 # 到店直招建档 + 可选录音
PUT,DELETE   /admin/offline-interviews/{offline_id}
POST         /admin/offline-interviews/{offline_id}/score      # 录入人工/AI 分数
POST         /admin/offline-interviews/{offline_id}/audio      # 上传/替换整场录音
GET          /admin/offline-interviews/{offline_id}/audio      # 播放（?download=true 下载）
POST         /admin/offline-interviews/{offline_id}/bind-minutes   # 手动绑定妙记
POST         /admin/offline-interviews/{offline_id}/transcribe     # 触发转录
POST         /admin/offline-interviews/{offline_id}/adopt-ai-score # 采纳 AI 参考分
GET          /admin/summary                # 实时聚合
POST         /admin/summary/rebuild        # 落库（notify 可选）
POST         /admin/summary/notify         # 补推复核卡片
GET          /admin/summary/snapshots      # 已落库快照
GET          /api/scores/candidate/{candidate_id}   # 供外部系统读取
```

**公司文化档案**
```
GET          /admin/company-profile
PUT          /admin/company-profile
```

**Offer → 入职闭环**
```
GET,POST     /admin/offers
GET          /admin/offers/stats
GET,PATCH    /admin/offers/{offer_id}
POST         /admin/offers/{offer_id}/submit-approval   # 提交老板审批
POST         /admin/offers/{offer_id}/approve           # 审批通过（生成 token）
POST         /admin/offers/{offer_id}/reject            # 驳回
POST         /admin/offers/{offer_id}/withdraw          # 撤回
POST         /admin/offers/{offer_id}/onboard           # 办入职
GET,POST     /offer/{token}                             # 候选人确认页（接受/婉拒）
# POST       /admin/feishu/offer-callback               # HTTP 回调审批（待开发，方式 B）

GET          /health
```

> 完整字段与请求体见 [`docs/接口文档.md`](docs/接口文档.md)。

## 11. 目录结构

```
interview-agent/
├── backend/
│   ├── app/
│   │   ├── main.py                 # FastAPI 入口 + lifespan 启动妙记轮询任务
│   │   ├── config.py               # 配置（Pydantic Settings）
│   │   ├── graph/
│   │   │   ├── state.py            # InterviewState 定义
│   │   │   ├── builder.py          # 图构建 + checkpointer（连接池单例 + close 释放）
│   │   │   ├── nodes/              # load_context / ask_question / evaluate / final_report
│   │   │   └── route.py            # 条件边
│   │   ├── models/                 # Pydantic schemas（AnswerScore、Report、Offer 等）
│   │   ├── services/
│   │   │   ├── resume_parser.py    # PDF/DOCX 解析
│   │   │   ├── resume_screening.py # 简历初筛打分 + 榜单
│   │   │   ├── scoring.py          # 维度加权汇总 + 档位
│   │   │   ├── summary.py          # 三阶段聚合 + 落库
│   │   │   ├── company_profile.py  # 公司文化档案单例 + 快照 + 开场白
│   │   │   ├── audio_storage.py    # 录音落盘/读取
│   │   │   ├── feishu_minutes.py   # 妙记 search/transcript + 短码匹配
│   │   │   ├── minutes_poller.py   # 妙记轮询转录 + 整场评分调度
│   │   │   ├── whole_interview_scoring.py  # 整场面试 AI 参考分
│   │   │   ├── offer.py            # Offer 状态机（唯一事实源）/惰性过期/统计
│   │   │   ├── offer_notify.py     # Offer 7 类飞书卡片
│   │   │   ├── feishu_notify.py    # HR 通知（cli/api 双传输 + 部门解析）
│   │   │   └── cascade.py          # 流水线级联
│   │   ├── api/                    # admin / interview / offers / offline / screening / summary / scores
│   │   └── db/                     # SQLAlchemy models + migrations (Alembic)
│   ├── scripts/
│   │   ├── checkpointer_concurrency.py   # checkpointer 并发压测 + 连接中断自愈探针
│   │   ├── send_feishu_test.py           # 飞书 HR 通知通道自检
│   │   ├── smoke_recruitment_e2e.py      # 全链路端到端（静默推送）
│   │   ├── smoke_offer_e2e.py            # Offer 链路端到端（静默、自动清理）
│   │   └── offer_card_action_consumer.py # 飞书长连接审批消费者（本机/单机）
│   ├── tests/                      # 288 个 pytest 用例（基于 mock/fake LLM，不依赖外部服务）
│   ├── requirements.txt
│   └── Dockerfile
├── frontend/
│   ├── src/
│   │   ├── views/Interview.vue        # 候选人面试页 /i/:token
│   │   ├── views/OfferConfirm.vue     # 候选人 Offer 确认页 /o/:token
│   │   ├── views/admin/               # Dashboard/Jobs/Company/Templates/Screening/
│   │   │                              # Sessions/Offline/Reports/Summary/Offers
│   │   ├── components/                 # OfferCreate/Approve/Onboard 对话框等
│   │   ├── api/index.js  router/  stores/  utils/
│   └── vite.config.js                 # 含 /api /offer /admin /interview /health 反代
├── docs/                            # PRD / 全流程说明 / 方案设计 / 接口文档 / 飞书接入
├── docker-compose.yml               # postgres + api + web
└── README.md
```

## 12. 快速开始

### 环境变量

```bash
# .env
LLM_MODEL=deepseek-chat            # 或 gpt-4o / qwen-plus / glm-4
LLM_API_KEY=sk-xxx
LLM_BASE_URL=https://api.deepseek.com   # OpenAI 兼容地址，GPT 可省略
DATABASE_URL=postgresql://interview:interview@localhost:5432/interview

# LangGraph checkpointer 连接池大小（多线程并发面试各自检出独立连接）
CHECKPOINTER_POOL_MIN_SIZE=1
CHECKPOINTER_POOL_MAX_SIZE=20

# 飞书（可选，先跑通面试可不配）
FEISHU_NOTIFY_ENABLED=false
FEISHU_NOTIFY_TRANSPORT=cli
# PUBLIC_BASE_URL=http://localhost:3000   # 卡片/待办链接基址，部署必改
```

### 启动

```bash
cd interview-agent
cp .env.example .env              # 填入模型配置

# 方式一：Docker Compose（推荐）
docker compose up -d              # 启动 postgres + api + web

# 方式二：本地开发
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
alembic upgrade head              # 建表
uvicorn app.main:app --reload     # http://localhost:8000

cd ../frontend && npm i && npm run dev   # http://localhost:3000
```

### MVP 验证（不接前端，直接跑图）

```python
from graph.builder import build_interview_graph

graph = build_interview_graph()
config = {"configurable": {"thread_id": "session-001"}}

# 创建会话
graph.invoke({
    "candidate": {"name": "张三", "resume_text": "...", "job_desc": "..."},
    "questions": [{"id": "q1", "text": "请介绍你在高并发项目中的经验"}],
    "rubric": [...],
    "current_idx": 0,
}, config)

# 候选人答题（执行到下一个 interrupt）
graph.invoke({"messages": [("user", "我主导过日均千万级 API 网关改造……")]}, config)
```

### 回归验证（自动化测试 + 并发压测）

```bash
cd backend
pytest tests/ -q                     # 全量回归用例（mock/fake LLM，不依赖真实 LLM 与外部服务）

# checkpointer 并发压测 + 连接中断自愈探针（需可用的 PostgreSQL）
python scripts/checkpointer_concurrency.py --impl current --mode both --threads 30 --ops 20 --with-recovery
```

压测脚本说明（`backend/scripts/checkpointer_concurrency.py`）：

- `--impl current|single|pool`：分别跑生产实现 `builder._create_checkpointer()`、单长连接旧实现、连接池实现，便于修复前后同口径对比；
- `--mode saver|graph|both`：直接压 checkpointer 的 `put/get_tuple`，或经 `graph.invoke` → `interrupt` → `Command(resume=...)` 跑端到端面试流（fake LLM，`session_id` 留空跳过业务落库）；
- 每个线程使用独立 `thread_id`（uuid4）并校验读回数据的归属与内容，用于发现跨线程串话；结束后自动删除 `stress-*` 数据。

**实测结论**（30 线程 × 20 轮，2026-10-12）：

| 指标 | 单长连接（旧实现） | 连接池（当前实现） |
| --- | --- | --- |
| 线程成功 / 连接级异常 / 串话 | 30/30 · 0 · 0 | 30/30 · 0 · 0 |
| 总耗时（wall） | 0.353s | 0.369s |
| 连接被 DB 端强制中断后的自愈 | 探针 0/5 成功，永久失败（须重启进程） | 探针 3/5 成功，坏连接被丢弃并重建 |

> 耗时未随连接池下降的根因：`langgraph-checkpoint-postgres 2.0.25` 的 `PostgresSaver._cursor()` 对**每次操作**持有实例级 `threading.Lock`，单个 saver 实例的 DB 临界区恒为串行（与连接是单条还是池无关，对照实验已证实）。连接池解决的是「共享可变连接 + 连接失效不可恢复」的健壮性问题；生产耗时由 LLM 调用主导，该锁不构成瓶颈。

## 13. 部署前置（服务器上线四件事）

1. **`PUBLIC_BASE_URL` 改成公网地址**，如 `http://<服务器IP>:3000` 或 `https://hire.xxx.com`。否则卡片里是 `localhost` 链接，HR 打不开（系统会告警但链接仍是坏的）。
2. **`FEISHU_NOTIFY_TRANSPORT=api`** + `FEISHU_APP_ID` + `FEISHU_APP_SECRET`。默认 `cli` 依赖本机 lark-cli / node / macOS keychain，Linux 容器里全都没有。密钥建议放 `.env.local`（已被 `.gitignore`）。
3. **飞书应用申请 `task:task:write`（+ `task:task:writeonly`）权限**并发布，否则**待办创建失败**（卡片不受影响）；妙记转录另需 `minutes:minutes.search:read` / `minutes:minutes.transcript:export`。
4. **放行 3000 端口**（前端 nginx 反代 `/admin` `/interview` `/api` `/offer` `/health` 到 api 容器）；飞书应用**可用范围**需包含 HR 用户（否则报 230013）。

> `.env` 与 `.env.*` 均被 `.gitignore` 忽略，`git pull` **不会**覆盖服务器配置 —— 新增配置项必须手动补，否则走代码默认值。
> 部署时 api 容器会自动执行 `alembic upgrade head`，迁移无需手动跑。
> 注意：api 容器是 bind mount，`docker compose up -d` 不会重建未变更的容器 —— **`git pull` 后必须 `sudo docker compose restart api`**。

## 14. 扩展方向 / 已知限制

- **飞书待办权限未开通**：`task:task:write` 尚未申请，待办创建会失败（卡片正常）。
- **录音转录依赖店长用妙记**：写操作仅支持 user 身份；妙记无 webhook 只能轮询；bot 能否读店长个人妙记**需 POC 验证**（search/detail 接口支持 bot）。
- **公司文化档案部分能力未做**：CC-7 评分 prompt 注入、CC-9 模板文化题 UI、CC-10~13 尚未落地，当前仅完成「档案 → 快照 → 开场白」链路。
- **Offer 第⑤阶段 HTTP 回调审批（方式 B）未开发**：服务器无公网 HTTPS 时，审批可先在后台页面完成（卡片退化为通知）；本机用飞书长连接消费者已跑通。
- **初筛为同步逐份调用 LLM**：简历量大时上传接口会较慢，后续可改后台任务或并发。
- **多模态**：语音面试（ASR + TTS）、视频面试（表情/微表情分析，合规评估后接入）待做。
- **防作弊**：回答与简历/网络内容查重、答题时长异常检测待做。
- **多语言**：当前仅中文面试。
- **批量背调对比**：同岗位 N 个候选人横向排行榜与维度对比待做。
- **合规**：面试数据留存提醒、候选人知情同意书、AI 面试结果仅作参考的免责声明（已含 consent gate，其余待补）。

## 15. 文档导航

| 文档 | 内容 |
| --- | --- |
| [`docs/全流程说明.md`](docs/全流程说明.md) | 招聘四阶段 + 飞书触点 + 接口清单 + 部署前置（当前状态最权威） |
| [`docs/PRD.md`](docs/PRD.md) | MVP 后端核心链路 PRD |
| [`docs/PRD-Offer入职闭环.md`](docs/PRD-Offer入职闭环.md) | Offer → 入职闭环需求 |
| [`docs/设计方案-Offer入职闭环.md`](docs/设计方案-Offer入职闭环.md) | Offer 状态机任务分解 |
| [`docs/PRD-企业文化融入面试题.md`](docs/PRD-企业文化融入面试题.md) | 公司文化档案需求（CC-1~CC-16） |
| [`docs/飞书妙记录音节点方案设计.md`](docs/飞书妙记录音节点方案设计.md) | 到店直招录音节点设计 |
| [`docs/飞书HR通知接入说明.md`](docs/飞书HR通知接入说明.md) | 飞书通知接入与排障 |
| [`docs/接口文档.md`](docs/接口文档.md) | 完整接口字段 |
| [`docs/语音识别ASR成本评估.md`](docs/语音识别ASR成本评估.md) | ASR 成本评估 |
