# 面试智能体（AI Interview Agent）

基于 **LangGraph + LangChain** 的多轮面试智能体：候选人通过与 AI Agent 对话完成面试，Agent 依据岗位 JD 与候选人简历生成/追问问题，并按照预设评分维度自动完成逐题评分与最终面试报告，后台可查看每个候选人的评分结果。

## 1. 为什么选 LangGraph（而非单独用 LangChain）

| 需求特征 | 实现方式 |
| --- | --- |
| 多轮有状态对话（问题流转、追问、进度） | LangGraph 状态图 + Checkpointer |
| 面试中断后恢复（候选人中途退出再进入） | Checkpointer 按 thread_id 恢复状态 |
| 条件分支（追问 / 下一题 / 结束） | 条件边 `route` |
| 等待候选人输入 | `interrupt` / 人工介入节点 |
| 逐题评分、维度汇总 | 节点内结构化输出（Pydantic） |
| 可观测、可调试（每个节点的输入输出与耗时） | LangSmith / LangGraph 自带追踪 |

结论：**LangGraph 负责编排（状态机），LangChain 负责生态组件（模型封装、Prompt 模板、结构化输出、文档加载）**，两者配合使用，不是二选一。

## 2. 整体架构

```mermaid
flowchart LR
    subgraph Client[前端]
        A[候选人面试页] 
        B[HR 管理后台]
    end
    subgraph Server[后端]
        C[FastAPI]
        D[LangGraph 面试图]
        E[简历解析]
    end
    subgraph Store[存储]
        F[(PostgreSQL)]
        G[(Redis 可选)]
    end
    subgraph Model[模型层]
        H[LLM 提供商<br/>GPT / DeepSeek / Qwen / GLM / Claude]
    end

    A -->|SSE 流式对话| C
    B -->|CRUD + 评分报表| C
    C --> D
    E --> D
    D <-->|Checkpointer| F
    C --> F
    C --> G
    D -->|REST 兼容接口| H
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
    questions: List[dict]            # 预设问题集
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

def build_interview_graph():
    g = StateGraph(InterviewState)

    g.add_node("load_context", load_context)   # 加载 JD + 简历 + 问题集
    g.add_node("ask_question", ask_question)   # 出题（含追问生成）
    g.add_node("evaluate", evaluate_answer)    # 按维度评分
    g.add_node("final_report", final_report)   # 汇总报告

    g.add_edge(START, "load_context")
    g.add_edge("load_context", "ask_question")
    g.add_edge("ask_question", "evaluate")     # 实际通过 interrupt 暂停等待回答
    g.add_conditional_edges(
        "evaluate",
        route,                                 # follow_up -> ask_question
        {"ask_question": "ask_question",       # next_question -> ask_question
         "final_report": "final_report",
         "end": END}
    )
    g.add_edge("final_report", END)

    return g.compile(checkpointer=PostgresSaver.from_conn_string(DB_URL))
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
4. **汇总报告**：维度得分 = 各题维度得分均值 × 权重 → 综合分（百分制）→ 等级建议（强烈推荐 / 推荐 / 待定 / 不推荐）+ 总体评价与录用风险提示。
5. **后台展示**：HR 后台按候选人查看每题得分明细、维度雷达图、综合分与建议。

## 5. 技术栈

| 层 | 选型 | 说明 |
| --- | --- | --- |
| 编排框架 | **LangGraph** | 面试状态机、Checkpointer 断点恢复、interrupt 等待输入 |
| LLM 接入 | **langchain-openai**（OpenAI 兼容协议） | 一个接口切换 GPT / DeepSeek / Qwen / GLM / Claude |
| 结构化输出 | Pydantic + `with_structured_output` | 评分结果、报告 schema 校验 |
| 简历解析 | pdfplumber / python-docx | PDF、Word、纯文本简历（MVP 可不做向量化） |
| 后端 | FastAPI + SSE | REST 管理接口 + 流式对话输出 |
| 前端 | React + Next.js + antd/Tailwind | 候选人面试页 + HR 管理后台 |
| 数据库 | PostgreSQL | 岗位、模板、候选人、会话、评分（LangGraph PostgresSaver 复用） |
| 缓存（可选） | Redis | 会话级缓存、限流 |
| 检索（进阶） | Chroma/Qdrant 向量库 | 简历-岗位匹配度、追问素材检索 |
| 部署 | Docker Compose | api + web + postgres |
| 可观测（可选） | LangSmith | 全链路追踪、每个节点耗时与 token 统计 |

**国内环境推荐默认组合**：`LLM_MODEL=deepseek-chat`（DeepSeek，OpenAI 兼容）或 `qwen-plus`（通义），无需代理。

## 6. 数据模型（关键表）

```
job_postings          岗位（id, title, jd_text, requirements）
interview_templates   面试模板（id, job_id, questions JSONB, rubric JSONB, duration_limit）
candidates            候选人（id, name, email, phone）
resumes               简历（id, candidate_id, file_url, parsed_text, embedding 可选）
interview_sessions    面试会话（id, token, template_id, candidate_id, status, thread_id, started_at）
answer_evaluations    逐题评分（id, session_id, question_id, scores JSONB, comment, ts）
session_reports       面试报告（id, session_id, dim_scores JSONB, total_score, verdict, summary）
```

## 7. API 概要

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| POST | `/admin/templates` | 创建面试模板（预设问题 + 评分维度） |
| POST | `/admin/sessions` | 创建面试会话（上传简历 + 选择岗位），返回候选人答题 token |
| POST | `/admin/sessions/{id}/evaluate` | 手动触发/重算评分 |
| GET | `/admin/sessions/{id}/report` | 获取评分报告 |
| GET | `/admin/sessions?job_id=&status=` | 后台列表、批量导出 |
| GET | `/interview/{token}` | 候选人获取面试上下文（问题、开场白） |
| POST | `/interview/{token}/answer` | 提交回答，返回下一题/追问 |
| GET | `/interview/{token}/stream` | SSE 流式（提问流式输出） |

## 8. 目录结构

```
interview-agent/
├── backend/
│   ├── app/
│   │   ├── main.py                 # FastAPI 入口
│   │   ├── graph/
│   │   │   ├── state.py            # InterviewState 定义
│   │   │   ├── builder.py          # 图构建 + checkpointer
│   │   │   ├── nodes/
│   │   │   │   ├── load_context.py
│   │   │   │   ├── ask_question.py
│   │   │   │   ├── evaluate.py     # 结构化评分
│   │   │   │   └── final_report.py
│   │   │   └── route.py            # 条件边
│   │   ├── models/                 # Pydantic schemas（AnswerScore、Report 等）
│   │   ├── services/
│   │   │   ├── resume_parser.py    # PDF/DOCX 解析
│   │   │   └── scoring.py          # 维度加权汇总
│   │   ├── api/                    # admin / interview 路由
│   │   └── db/                     # SQLAlchemy models + migrations
│   ├── requirements.txt
│   └── Dockerfile
├── frontend/
│   ├── pages/
│   │   ├── interview/[token].tsx   # 候选人面试页
│   │   └── admin/                  # HR 后台（模板/会话/报告/雷达图）
│   └── ...
├── docker-compose.yml
└── README.md
```

## 9. 快速开始

### 环境变量

```bash
# .env
LLM_MODEL=deepseek-chat            # 或 gpt-4o / qwen-plus / glm-4
LLM_API_KEY=sk-xxx
LLM_BASE_URL=https://api.deepseek.com   # OpenAI 兼容地址，GPT 可省略
DATABASE_URL=postgresql://interview:interview@localhost:5432/interview
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

## 10. 扩展方向

- **多模态**：语音面试（ASR + TTS）、视频面试（表情/微表情分析，合规评估后接入）
- **简历-岗位匹配预筛**：向量检索 + 匹配度打分，作为开场或追问素材
- **防作弊**：回答与简历/网络内容查重、答题时长异常检测
- **多语言**：候选语言自适应（中文/英文面试）
- **批量背调对比**：同岗位 N 个候选人横向排行榜与维度对比
- **合规**：面试数据留存提醒、候选人知情同意书、AI 面试结果仅作参考的免责声明