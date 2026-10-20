# PRD：候选人维度匹配度与综合素质（BI 看板增强）

> 版本：v1.0 ｜ 2026-10-20 ｜ 状态：待评审
> 目标读者：BI、前端、后端、HR 业务方
> 关联文档：`docs/设计方案-看板面试问答接入.md`、`docs/PRD-Offer入职闭环.md`、`bi/README.md`

---

## 1. 背景与需求

### 1.1 一句话需求

在 BI 数据看板中，新增一个**独立面板**，把候选人从「4 个环节总分」升级为「**统一维度的匹配度画像**」，并在画像末尾给出**综合素质**（加权综合分 + 录用建议 + 整体评语）。

### 1.2 用户场景

| 角色 | 想解决的问题 | 使用动作 |
|---|---|---|
| HR / 招聘负责人 | 想知道候选人「强在哪、弱在哪」，而不只是一个总分 | 打开「候选人维度匹配」面板 → 看维度条 → 点开雷达 |
| 店长 / 用人部门 | 判断候选人相对同岗位其他人处于什么水位 | 弹层雷达叠加「同岗位均值」基准线 |
| 管理层 / 老板 | 快速判断这个人「整体行不行」 | 看面板末列「综合素质」分 + 结论 + 总评 |

### 1.3 验收口径（P0）

1. 新增面板可列出候选人在**统一维度**（岗位匹配 / 沟通表达 / 工作态度 / 稳定性）上的匹配度；
2. 每个维度匹配度为 0~100 的百分制数值，鼠标可读、缺失可区分（显示 `—` 而非 0）；
3. 弹层以**雷达图**展示候选人各维度匹配度，并**叠加同岗位均值基准**；
4. 面板**最后**给出**综合素质**：加权综合分 + 录用建议（强烈推荐/推荐/待定/不推荐）+ 整体评语；
5. 弹层内可下钻看「各环节对该维度的贡献」，来源标注明晰（简历初筛 / AI 面试 / 线下复试）；
6. 缺数据时给出可理解空态，不得把「无数据」显示成「0 分」。

---

## 2. 现状盘点

### 2.1 已有数据（维度分其实已全部落库）

| 环节 | 表 | 维度字段 | 维度口径 |
|---|---|---|---|
| 简历初筛 | `resume_screenings` | `dim_scores` JSONB | `skill_match`(0.30) / `experience`(0.30) / `stability`(0.20) / `attitude`(0.20) |
| AI 面试 | `session_reports` | `dim_scores` JSONB | 模板级 `rubric`（key 不固定，见 §4.1 映射） |
| 线下复试 | `offline_interviews` | `dim_scores` JSONB（人工优先，缺省回填 `ai_dim_scores`） | 模板 `rubric` 或到店直招 `DEFAULT_RUBRIC`：`attitude`(0.30) / `communication`(0.25) / `job_match`(0.25) / `stability`(0.20) |
| 最终汇总 | `recruitment_summaries` | — | `final_score` / `final_verdict` / `summary` / `detail` |

`dim_scores` 统一结构：`[{"key","name","score","max_score","weight","weighted_score"(部分有)}]`。

### 2.2 缺口（为什么现在看不到）

1. 看板「候选人汇总明细」只展示 4 个**环节总分**（初筛 / AI / 线下 / 最终），**没有维度分解**（`bi/static/index.html` 明细表表头 10 列）；
2. 各环节维度 key **不统一**：初筛用 `skill_match/experience`，AI 用模板自定义 key，线下用 `job_match/communication`——不做归并无法直接横向比较；
3. 没有任何「跨环节、按维度」的聚合查询，也没有岗位维度均值基准。

### 2.3 结论：改动落在哪一层

维度原始分数**已在库中**，无需改评分链路。本次改动**仅涉及 BI 侧**：

- `bi/app.py`：新增维度聚合查询函数 + 并入 `/api/dashboard` 载荷；
- `bi/static/index.html`：新增面板 + 弹层雷达图；
- 新增 `bi/dim_config.json`：维度归并映射与权重的**唯一配置入口**（对齐 `kpi_config.json` 的可配置做法）。

> 后端（`interview-agent/backend`）本次**不改**：不新增字段、不新增迁移、不动评分/汇总业务逻辑。

---

## 3. 核心设计

### 3.1 统一跨环节维度（canonical dimensions）

定义 4 个标准维度，作为面板与雷达图的坐标轴：

| key | 名称 | 含义 |
|---|---|---|
| `job_match` | 岗位匹配 | 技能、经验与岗位要求的契合度 |
| `communication` | 沟通表达 | 能否听懂问题、把意思说清楚 |
| `attitude` | 工作态度 | 积极性、配合度、服务意识、责任心 |
| `stability` | 稳定性 | 任职时长、长期任职意愿 |

**各环节 key → 标准维度映射**（配置于 `bi/dim_config.json`，可随新模板扩展）：

```json
{
  "canonical": [
    {"key": "job_match", "name": "岗位匹配"},
    {"key": "communication", "name": "沟通表达"},
    {"key": "attitude", "name": "工作态度"},
    {"key": "stability", "name": "稳定性"},
    {"key": "__other__", "name": "其他"}
  ],
  "stage_key_map": {
    "screening": {"skill_match": "job_match", "experience": "job_match", "stability": "stability", "attitude": "attitude"},
    "ai":        {"job_fit": "job_match", "tech_depth": "job_match", "experience": "job_match",
                  "communication": "communication", "attitude": "attitude",
                  "culture_fit": "attitude", "stability": "stability"},
    "offline":   {"job_match": "job_match", "communication": "communication",
                  "attitude": "attitude", "service": "attitude", "stability": "stability"}
  },
  "stage_weight": {"screening": 0.20, "ai": 0.40, "offline": 0.40}
}
```

**兜底规则（顺序执行）**：

1. 先按 `stage_key_map[stage][key]` 命中；
2. 未命中则按维度 `name` 关键字归并（技能匹配/经验相关/岗位匹配/技术深度/项目经验→`job_match`；沟通表达→`communication`；工作态度/素质与态度/服务意识/文化契合→`attitude`；稳定性→`stability`）；
3. 仍未命中 → 归入 `__other__`，UI 灰显并在弹层列出明细，**不静默丢弃**（便于发现新模板引入的新维度）。

### 3.2 维度匹配度计算

对每位候选人的每个标准维度：

```
归一化分 norm_i = score / max_score × 100        // max_score 缺省按 10
维度匹配度 = Σ(norm_i × w_stage_i) / Σ(w_stage_i) // 仅计入该维度"有分"的环节
```

- `w_stage` 取 `dim_config.stage_weight`（0.20 / 0.40 / 0.40），与 `recruitment_summaries.final_score` 同口径，保证面板内自洽；
- 同一环节若出现多条记录（多轮 AI 面试 / 多场线下复试），取**最新一条**（按 `created_at`）；
- 某维度在**任何环节都无数据** → 该维度返回 `null`，前端显示 `—`（**不是 0**）；
- 线下复试维度取 `offline_interviews.dim_scores`（人工优先），其为空时回落到 `ai_dim_scores`（AI 参考分）。

### 3.3 综合素质（复用加权分 + 综合总评，不新增评分）

末尾「综合素质」三件套：

1. **加权综合分**：直接复用 `recruitment_summaries.final_score`（已按 0.20/0.40/0.40 加权并重新归一化）；
2. **录用建议**：复用 `recruitment_summaries.final_verdict`（阈值 80/65/50）；
3. **整体评语**：确定性拼接，不调用 LLM——
   - 若 `recruitment_summaries.summary` 存在，直接复用其数值口径文案；
   - 追加各环节一句定性评语（存在才拼）：初筛 `resume_screenings.summary`、AI `session_reports.summary`、线下 `offline_interviews.comment`（人工）或 `ai_comment`；
   - 追加各维度最强/最弱一句：`表现最佳：{维度}（{分}）；需重点关注：{维度}（{分}）`（对齐 `scoring._build_summary` 口径）。

**到店直招（walk_in，无初筛 / 无 AI）**：不参与 0.20/0.40/0.40 加权，综合素质=线下复试分，评语首句注明「到店直招，仅线下复试」。

### 3.4 同岗位均值基准

对同岗位、同时间窗/口径下的候选人，按标准维度求均值（仅统计该维度有分的候选人），作为弹层雷达的第二条 series（岗位均值），使候选人位置可判读。样本数 < 3 时在图上标注「样本不足，仅供参考」。

---

## 4. 后端改造（BI 侧）

### 4.1 新增查询函数

`bi/app.py` 新增两个查询：

**（a）维度明细 `_q_candidate_dims(conn, p, limit)`**

按候选人聚合三张表的维度分（示意 SQL，实际按 `q->>'key'` 归并，配置在应用层做）：

```sql
-- 三来源：resume_screenings / session_reports(join session) / offline_interviews
-- 每候选人每个 stage 取最新一条，展开 dim_scores 数组
SELECT co.id::text AS candidate_id, co.name AS candidate_name, j.title AS job_title,
       'screening' AS stage, d->>'key' AS dim_key, (d->>'score')::float AS score,
       coalesce((d->>'max_score')::float, 10) AS max_score, rs.created_at AS at
  FROM resume_screenings rs
  JOIN cohort co ON co.id = rs.candidate_id
  JOIN job_postings j ON j.id = rs.job_id, jsonb_array_elements(rs.dim_scores) d
 WHERE jsonb_typeof(rs.dim_scores) = 'array'
 -- AI / offline 同理
```

再在 Python 侧按 `stage_key_map` / 别名归并、按 `stage_weight` 加权，产出：

```json
{"candidate_id":"…","candidate_name":"吴婷","job_title":"店员","job_id":"…",
 "dims":{"job_match":82.5,"communication":70.0,"attitude":88.0,"stability":null,"__other__":null},
 "composite":79.2,"verdict":"推荐","evidence":{"job_match":{"screening":78,"ai":85,"offline":82}}}
```

**（b）岗位维度均值 `_q_job_dim_baseline(conn, p)`**

同口径按 `job_id` 聚合各标准维度均值：

```json
{"job_id":"…","job_title":"店员","sample":12,
 "dims":{"job_match":74.1,"communication":71.3,"attitude":80.6,"stability":68.9}}
```

> 说明：归并/加权逻辑建议抽成纯函数 `_normalize_dims()` 并对齐 `kpi_config.json` 的读取方式，便于单测。

### 4.2 并入 dashboard 载荷

`build_dashboard` 追加两个键：

```python
candidate_dims = _q_candidate_dims(conn, params, limit)
job_dim_baseline = _q_job_dim_baseline(conn, params)
```

- 顶部 `recent`/`recent_walkin` 已带 `candidate_id`，维度面板按 `candidate_id` 关联即可；
- 载荷增量估算：每人 5 个数值 + 3 个文字字段，按 25 人计 ≈ 数 KB，可接受；
- 为控制体积，**长定量只放维度数值 + 综合分/建议 + evidence（各环节数值）**，`evidence` 不含长文本；
- 弹层若需「各环节一句评语」，走既有 `/api/dashboard` 已含的字段或后续按需接口，避免首屏臃肿。

### 4.3 缓存与鉴权

- 复用现有 60s 聚合缓存与 SSE 指纹刷新机制，无需新增缓存；
- 维度含候选人姓名等敏感字段，沿用既有 `BI_ACCESS_TOKEN` 网关，不额外放宽。

---

## 5. 前端交互设计（`bi/static/index.html`）

### 5.1 新增面板

新增「候选人维度匹配」面板，置于运营明细视图（`data-view="ops"`），与「面试问答」「候选人汇总明细」并列；`span-12` 整行，避免挤压。

列表列：

| 候选人 | 岗位 | 岗位匹配 | 沟通表达 | 工作态度 | 稳定性 | 综合素质 | 结论 |
|---|---|---|---|---|---|---|---|

- 每个维度单元格用**迷你维度条**（`--fs` 同款配色，宽度 = 匹配度%）；缺失显示 `—`；
- 综合素质列为**加粗百分数 + verdict 标签**（复用现有 `VERDICT_CLASS` 配色）；
- 行支持点击 → 打开弹层；复用现有搜索/排序控件风格。

### 5.2 弹层（雷达图 + 综合素质）

复用现有 `.qa-modal` 结构新增一个 `#dimModal`：

```
┌──────────────────────────────────────────────┐
│ 维度匹配 · 吴婷                          [×] │
│ 店员 · 综合 79.2 分（推荐）· 岗位均值对比    │
├──────────────────────────────────────────────┤
│  [雷达图] 候选人 vs 岗位均值（4 轴）         │
│  ────────────────────────────────            │
│  综合素质  79.2 分 · 推荐                     │
│  总评：简历初筛 78 分；AI面试 85 分；线下…    │
│  表现最佳：工作态度（88）；需重点：沟通表达…  │
│  ────────────────────────────────            │
│  各环节贡献（折叠）：                        │
│   岗位匹配  初筛78 · AI85 · 线下82            │
│   沟通表达  初筛— · AI70 · 线下70             │
└──────────────────────────────────────────────┘
```

- 雷达图用已引入的 `echarts`（`/static/echarts.min.js`），`radar` 两条 series（候选人实线蓝、岗位均值虚线灰琥珀）；
- 窄屏（<900px）：雷达与文字上下堆叠，维度明细转列表；
- 配色沿用现有 CSS 变量（`--blue` / `--amber` / `--purple` / `--green`）。

### 5.3 空态与错误态（必须区分）

| 状态 | 判定 | 文案 |
|---|---|---|
| 无任何维度数据 | 三环节 dims 全空 | 该候选人暂无维度数据（尚未初筛/面试） |
| 仅部分环节 | 部分维度为 null | 单元格显示 `—`，弹层对应轴不绘制并注明「未考察」 |
| 岗位均值样本不足 | `sample < 3` | 图上标注「同岗位样本不足，均值仅供参考」 |
| 到店直招 | `stage == walk_in` | 注明「到店直招 · 仅线下复试」，不显示初筛/AI 环节贡献 |
| 数据加载失败 | 接口异常 | 与现有面板一致的故障态样式（`setStamp('err')`） |

---

### 5.4 口径与待确认（实现补充）

面板底部固定一个「口径与待确认」提示块，把**仍需人工确认 / 补充**的地方显式写出来（有则显示、无则隐藏）：

1. **未归入标准维度的原始维度**：列出维度名，提示需在 `bi/dim_config.json` 补映射后才计入匹配度；
2. **同岗位样本不足 3 人**：列出岗位与样本数，声明均值基准仅供参考；
3. **存在「未考察维度」**：说明表格显示 `—` 系该环节未评到该维度，并非 0 分。

弹层内以同样口径给出对应提示（样本不足 / 未考察维度 / 未归入维度）。导出文档一并带上这些提示，保证离职流转的画像自带口径说明。

## 6. 边界规则汇总

1. `max_score` 缺失一律按 10 计，避免除零；
2. 同环节多轮取最新一条；跨轮聚合只做最新，不做跨轮平均（避免"面得越多分越高/越低"）；
3. 归并后落 `__other__` 的维度必须在弹层可见，便于发现新模板维度；
4. 不做历史回填：历史会话若 `dim_scores` 为空即按"无数据"处理（与问答原文的既有取舍一致）；
5. 权重唯一来源为 `bi/dim_config.json`，与 `kpi_config.json` 同风格，改配置不改代码。

---

## 7. 任务拆分（工作量相对量级：S / M / L）

| 编号 | 任务 | 产出 | 量级 |
|---|---|---|---|
| T01 | 新增 `bi/dim_config.json` + 读取与校验 | 配置入口 | S |
| T02 | `_normalize_dims()` 纯函数（归并 + 加权 + 兜底） | 可单测函数 | M |
| T03 | `_q_candidate_dims()` 查询 + evidence 聚合 | 维度明细 | M |
| T04 | `_q_job_dim_baseline()` 岗位均值 | 基准线 | S |
| T05 | 并入 `build_dashboard` 载荷 + 单测 | 接口数据 | S |
| T06 | 前端：新增「候选人维度匹配」面板 + 迷你维度条 | 列表 | M |
| T07 | 前端：弹层雷达图（候选人 vs 岗位均值）+ 综合素质块 | 详情 | M |
| T08 | 空态/错误态/窄屏适配 + 手工回归 | 验收 | S |

> 节奏建议：T01→T05 一次（数据先就绪），T06→T08 一次（交互）。无后端改动，无数据库迁移。

---

## 8. 风险与取舍

| 风险 | 影响 | 应对 |
|---|---|---|
| 各环节维度 key 不统一 | 归并错误导致画像失真 | 映射集中配置 + 别名兜底 + `__other__` 可见 + 单测覆盖 |
| AI 模板自定义维度多 | 部分维度落 `其他` | 配置可扩展；未识别的显式呈现，便于运营补充映射 |
| 维度归一化口径误解 | 被误读为「绝对能力分」 | 面板 hint 注明「跨环节加权·百分制·仅统计已考察环节」 |
| 岗位均值样本少 | 基准线失真 | 样本 < 3 显式标注 |
| 载荷变大 | 首屏变慢 | 只传数值 + 短字段，长文本按需加载 |
| 历史数据 `dim_scores` 为空 | 老候选人无画像 | 明确空态文案，不做回填 |

---

## 9. 后续可扩展（本次不做）

1. 维度筛选与「同岗位维度排行」；
2. 维度解释下钻到「逐题/回答亮点」（与 `qa_extract` 联动）；
3. 维度趋势：同一候选人不同时间的维度变化（需引入快照留存）。

> 「导出单候选人维度匹配画像 PDF/Word」原列 P1，**本次已实现**：弹层内「导出 Word / 导出 PDF」按钮，
> 由前端零依赖生成（Word 走 HTML `.doc`、PDF 走打印另存），文档内含浅色雷达图、维度明细表、
> 综合素质与口径提示；不引入 reportlab / python-docx，保持 BI 的 3 依赖约束。
