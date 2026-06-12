# LDLAWQ 执行手册（Opus 4.8 任务卡）v1.1 — 2026-06-11（状态同步 2026-06-13）

> 本文档把 [PLAN.md](PLAN.md) 拆解为可由 Claude Opus 4.8 独立执行的任务卡。
> 每张卡自包含：前置依赖、具体动作、产出文件、**机器可验证的验收命令**。
> 执行方式：每个任务开一个 Claude Code 会话，使用 §1.4 的启动提示词模板。
> **本文档是唯一进度真相源**：每卡标题带状态标记（✅ 完成 / 🔶 部分 / ⬜ 未做），收工时更新。

---

## 0. 状态总览（2026-06-13 对照代码与数据库实测）

| 阶段 | 状态 | 备注 |
|---|---|---|
| P0 工程止血 | T0.1–T0.8 ✅ | 全清 |
| P1 知识层 | T1.1 / T1.4 ✅；T1.2 / T1.3 / T1.5 🔶；T1.6 / T1.7 / T1.8 ⬜ | 法规 15 部 719 条；核验工具就绪、待 G1 人工批准置 verified；案例 0、词条 2 |
| P2 检索精度 | T2.7 ✅（起步集）；T2.1 / T2.4 🔶；T2.6 🔶（18 题机器金标准，律师 100 题待 G3）；其余 ⬜ | 评测闸门已立，四指标达标；露出 2 道检索召回失分题 |
| P3 界面双端 | T3.1 ✅；T3.3 🔶（UI 就绪、数据 0）；T3.2 ⬜；T3.4 / T3.5 属 M1 | 375px 横向溢出已修（2026-06-13） |
| P4 合规商务 | 全部 ⬜ | PLAN §6：与 P1 并行启动，不能等 |

**计划外已实现**（属 PRD 范围、超出本手册原排期）：文书 AIGC 起草 + AI 律师审核
（6 类文书，引用走同一校验链）；医疗期防线（病假解除硬规则闸门）；案例页签
提前到 M0（数据待 T1.6）；联网搜索底座 `src/websearch.py`（博查，仅知识层，
2026-06-13，已用它找到上海工资支付办法官方线索 → 见 MISSING.md）。

---

## 1. 全局约定（每个任务都必须遵守）

### 1.1 技术决策（已定，执行时不再讨论）

| 决策点 | 结论 |
|---|---|
| 依赖策略 | 放弃"零第三方"。新建 `requirements.txt`，白名单：`jieba`（分词）、`sqlite-vec`（向量）、`sentence-transformers`（嵌入，仅构建期）。**server.py 运行时保持纯标准库**——嵌入在构建期算好存库，线上零模型依赖 |
| 嵌入模型 | `BAAI/bge-m3`，本地构建期计算；机器跑不动时降级为 SiliconFlow API（key 放 .env `EMBEDDING_API_KEY`） |
| 中文分词 | jieba 预分词 → 空格分隔写入 FTS5 列（不依赖自定义 tokenizer 编译） |
| LLM | deepseek-chat 为主；评测判分用 deepseek-reasoner |
| 数据库 | 维持双库 SQLite（knowledge.db 只读整库重建 / app.db WAL）；schema 改动只允许加表加列 |
| 官方法条来源 | flk.npc.gov.cn（国家法律法规数据库）优先；地方规定用各地人社/政府官网。**禁止裁判文书网爬取** |
| 前端 | 维持单文件 `web/index.html` 响应式，不引入框架；遵守 djhh 中文排版规则（keep-all / 数字 nowrap / 千分位） |
| 联网搜索 | 博查 Bocha Web Search（`src/websearch.py`，key 在 .env `BOCHA_API_KEY`）。**仅运营/知识层使用（方案 A）**：找官方源（T1.2/T1.3）、核验抓取（T1.4）、法规监测（F9）。问答管线接入（方案 B）未授权，须先评审 |

### 1.2 工程纪律

- 每个任务 = 一个 git 分支或至少一个独立 commit，提交信息格式：`[T1.2] 全国性法规入库：劳动合同法 98 条`
- 改代码必跑：`python3 tests/run_all.py`（T0.8 建立）；动知识层必跑：`python3 src/build_knowledge.py`
- P2.7 评测上线后追加纪律：动 prompt / 检索 / 知识库 → 必跑 `python3 src/run_eval.py`，分数回退不允许提交
- 法条/参数/案例数据一律先进 `data/seed/`（JSON，git 可审），从不直接写 .db

### 1.3 人工闸口（仅这 4 处需要人，其余全部 Opus 执行）

| 闸口 | 人工动作 | AI 准备到什么程度 |
|---|---|---|
| G1 法条核验批准 | 抽查 diff 报告后运行 `--approve` | AI 已抓官方源、逐字 diff、生成核验报告，差异为零的批量待批 |
| G2 词条律师审核 | 在审核清单上勾选通过/驳回 | AI 已起草词条全文 + 引用 + 风险提示，状态 in_review |
| G3 评测题终审 | 律师过目 100 题金标准 | AI 已出题 + 金标准答案 + 金标准引用 + 难度分级 |
| G4 律协书面意见 | 纯人工（商务） | AI 起草沟通函与合规问题清单 |

### 1.4 任务启动提示词模板（复制即用）

```
打开 /Volumes/ProjectsAPFS/LDLAWQ，先读 CLAUDE.md 与 docs/EXECUTION.md 的
§1 全局约定，然后执行任务卡 <任务ID>。严格按卡内"动作"清单执行，完成后
运行卡内"验收"命令并贴出结果。验收不过不许收工。完成后按 §1.2 格式提交 git，
并更新 docs/EXECUTION.md 中该卡的状态标记与 README「当前状态」。
不要做卡外的事；发现卡内描述与代码现状冲突时，以现状为准并在
提交信息中注明偏差。
```

---

## 2. P0 工程止血（1 个会话可全部完成，预计半天）

### T0.1 git 初始化与密钥加固 ✅
- **前置**：无
- **动作**：`git init`；确认 `.gitignore` 覆盖 `.env db/ __pycache__/`；`chmod 600 .env`；首次提交全部现有文件
- **验收**：`git log --oneline | wc -l` ≥ 1；`git status --porcelain` 为空；`stat -f "%Lp" .env` = 600；`git ls-files | grep -c "^\.env$"` = 0

### T0.2 修引用静默丢失（款号归一化） ✅
- **前置**：T0.1
- **背景**：[calculators.py:132](../src/calculators.py) 引用"第五条第三款"（中文），库内 clause_no='3'（阿拉伯），[pipeline.py](../src/pipeline.py) `resolve_citations` 匹配失败后静默丢弃——实测年假 4 条引用只剩 3 条
- **动作**：
  1. `build_knowledge.py` 入库时把 clause_no 统一归一化为中文数字（'3'→'三'）
  2. `resolve_citations` 解析失败的 ref 记入返回结构的 `unresolved` 字段并打 stderr 告警，不再静默
  3. 新增构建期校验：`calculators.py` 中所有硬编码 citations 在建库后逐条 resolve，失败即 `sys.exit`（与词条引用同等待遇）
- **验收**：`python3 src/build_knowledge.py` 通过；新增测试 `tests/test_citations.py`：年假计算 4 条引用全部解析（含第五条第三款）；故意改坏一条计算器引用 → 构建失败

### T0.3 RAG 校验链保留款号 ✅
- **前置**：T0.2
- **动作**：`pipeline.py` `rag_answer` 中 valid_refs 正则改为保留款号段，重建字符串含"第X款"
- **验收**：`tests/test_citations.py` 增加用例：含款引用经 RAG 校验链往返后款号不丢

### T0.4 LLM 输出类型防御 ✅
- **前置**：T0.1
- **动作**：
  1. `llm.chat_json` 校验返回值为 dict，否则抛 `ValueError`
  2. `extract_facts`：monthly_wage / cumulative_years / taken_days 做 `float()` 类型清洗（含全角逗号、"万"单位换算），清洗失败该字段置 None（走 clarify 路由，不再 500）
  3. `rag_answer` 中 `out.get(...)` 移入 try 块；citations 字段非 list 时按空处理
- **验收**：新增 `tests/test_pipeline.py`：mock LLM 返回数组/字符串数值/垃圾 JSON 三种情况，全部走 clarify 或拒答，无异常逃逸

### T0.5 资源与日志完整性 ✅
- **前置**：T0.1
- **动作**：
  1. `answer_structured` 包 try/finally：kc 必关、`_log` 必执行
  2. `_log` 写真实 region_id（region 名→id 查表）、命中词条时写 hit_entry_id
  3. `/api/escalate` 透传 session_id 写入 referral
  4. app.db 连接统一 `sqlite3.connect(ADB, timeout=5)`；`_log` 失败计数器（meta 表或 stderr）
- **验收**：`tests/test_pipeline.py`：问答一次后查 app.db，qa_session.region_id 非 NULL；entry_hit 路由 hit_entry_id 非 NULL

### T0.6 年假计算口径修正（法律正确性） ✅
- **前置**：T0.1
- **动作**：
  1. headline 结论改为"额外应补 200% = X 元"为主，300% 总额并列展示
  2. 折算天数基数改为"当年度**在本单位**已过日历天数"：hire_date 在当年时从 hire_date 起算
  3. hire_date < 2008-01-01 的补偿/赔偿计算：强制 route=refuse + escalate=true，结论说明分段计算需律师核算
- **验收**：`tests/test_calculators.py` 新增 3 个用例对应上述 3 点，全过；既有 14 测试不回退

### T0.7 FTS 死代码标注 ✅
- **前置**：T0.1
- **动作**：`build_knowledge.py` FTS 段加注释"T2.1 启用，当前 retrieve() 未使用"
- **验收**：注释存在即可（一行改动，随其他任务提交）

### T0.8 统一测试入口 ✅
- **前置**：T0.2–T0.6
- **动作**：新建 `tests/run_all.py`，发现并运行 tests/ 下全部测试
- **验收**：`python3 tests/run_all.py` 退出码 0，用例数 ≥ 20

---

## 3. P1 知识层扩容（W1–W4，重心）

### T1.1 法条切条工具 ✅
- **前置**：T0.8
- **动作**：新建 `src/ingest_law.py`：
  - 输入：法规全文 txt（`data/raw/<法规名>.txt`）+ 元数据（题名/文号/层级/地区/生效日期/source_url）
  - 自动切条：按"第X条"正则分条，款按换行/"（一）"模式分款；款号统一中文数字
  - 输出：`data/seed/laws/<slug>.json`（与现 legal_sources.json 同 schema，单法规单文件）
  - 自检：条号连续性检查（缺第N条则告警）、空文本检查
- **同时改**：`build_knowledge.py` 改为扫描 `data/seed/laws/*.json` + 兼容旧 legal_sources.json
- **验收**：用《劳动合同法》全文（98 条）跑切条，输出恰好 98 条且条号连续；建库通过；新增 `tests/test_ingest.py`

### T1.2 全国性法规全量入库（~400–600 条） 🔶
> **状态注（06-13）**：15 部 719 条已入库；缺工会法、劳动争议司法解释（二）——已列入 data/raw/MISSING.md。
- **前置**：T1.1
- **动作**：逐部抓取官方文本（WebFetch flk.npc.gov.cn；抓取失败的法规列入"待人工提供文本"清单，放 `data/raw/MISSING.md`）→ `ingest_law.py` 切条入库，verified=0：
  - 法律：劳动法 / 劳动合同法 / 劳动争议调解仲裁法 / 社会保险法 / 工会法（涉劳动条款）
  - 行政法规：劳动合同法实施条例 / 职工带薪年休假条例 / 工伤保险条例 / 女职工劳动保护特别规定
  - 司法解释：劳动争议司法解释（一）、（二）（2025-09-01 施行）
  - 部门规章：带薪年休假实施办法 / 工资支付暂行规定 / 劳社部发〔2008〕3 号
- **验收**：`build_knowledge.py` 输出法规 ≥ 14 部、法条 ≥ 400 条；`tests/test_ingest.py` 抽查 10 个知名条文（如劳动合同法第八十二条）文本非空且条号正确
- **注**：网页结构抓不全时退化为"人工贴文本到 data/raw/，AI 切条"——闸口仍最小化

### T1.3 沪苏地方法规入库 🔶
> **状态注（06-13）**：江苏 2 部已入库；上海市企业工资支付办法官方源 404 未入（见 MISSING.md），两地高院/人社口径文件未启动。
- **前置**：T1.1
- **动作**：同 T1.2 流程，目标：上海市企业工资支付办法、江苏省工资支付条例、江苏省劳动合同条例、两地高院/人社审理口径文件（regional_id 打标 上海/江苏）
- **验收**：建库后 `SELECT count(*) FROM legal_source WHERE region_id IN (2,3)` ≥ 4 部；retrieve() 带地区过滤能召回地方条文（写进 tests/test_ingest.py）

### T1.4 核验流水线（→ 人工闸口 G1） ✅
> **状态注（06-13）**：`src/verify_articles.py` 上线（--check 离线/--fetch 联网/--approve/--status）。离线核验已抓出并修复 2 处 seed 脏数据（劳动合同法 98 条、调解仲裁法 54 条尾部页脚噪音）。11 部法规逐字一致=待批，G1 由人跑 --approve 置 verified。build 摘要显示覆盖率%。偏离原卡：verified 状态写回 seed JSON（非直接改 DB，因 DB 整库重建）；无 verified_by/at 列改存 seed 字段。
- **前置**：T1.2
- **动作**：新建 `src/verify_articles.py`：
  - `--fetch`：按 source_url 重新抓官方文本，与库内逐条 diff，生成 `data/verify_report.md`（零差异条文列入"待批"，有差异的列出 diff）
  - `--approve <source_id>`：人工审过后批量置 verified=1（写 verified_by/verified_at）
  - build_knowledge 摘要中 verified 覆盖率显示为百分比
- **验收**：对劳动合同法跑 `--fetch` 产出报告；`--approve` 后该法规 verified=1 率 100%；篡改库内一条文本再跑 `--fetch` → diff 报告捕获该条

### T1.5 市级地区参数 🔶
> **状态注（06-13）**：22 项参数已入库但全部 verified=0 占位；市级行与前端省→市二级选择器未做。
- **前置**：T0.8
- **动作**：
  1. region 表补市级行：江苏 13 市、浙江 11 市（M0 先苏州/南京/杭州/宁波 + 沪京津深广全量）
  2. 从各地人社官网抓取真实社平工资（2024/2025 口径）、最低工资 → `data/seed/region_params.json`，verified=0、记 source_url
  3. `fetch_param` 改为"市级优先、省级回退"链路；封顶计算注明所用口径层级
  4. 前端地区选择器升级为省→市二级
- **验收**：参数 ≥ 20 项且全部非"演示占位"；`tests/test_pipeline.py`：苏州问题命中苏州社平、无苏州参数时回退江苏并在 warnings 注明
- **注**：抓取数字仍 verified=0 展示 ⚠，G1 流程批准后转正

### T1.6 案例库 v1（100 件） ⬜
- **前置**：T1.2
- **动作**：
  1. 新建 `src/ingest_case.py`：输入 `data/raw/cases/*.md`（单案例单文件：案号/法院/地区/争议焦点/案情摘要/裁判要旨/结果/关联法条），输出 `data/seed/cases.json`，入库 case_record + case_citation（**关联法条过存在性校验，失败即拒**）+ dispute_tag
  2. 抓取整理官方公开案例 ≥ 100 件：最高法指导性案例（劳动部分）、最高法+人社部联合发布的劳动争议典型案例（历批次）、沪苏法院年度劳动争议典型案例；source_channel='official_release'
  3. 覆盖度自检：violation_dismissal / double_pay / transfer / overtime / non_compete / maternity / annual_leave 七类标签每类 ≥ 8 件
- **验收**：建库输出案例 ≥ 100；七类标签覆盖达标；`tests/test_ingest.py`：案例引用不存在的法条 → 构建失败
- **注**：官方公开量不足 100 时如实降到可得量，缺口写入 MISSING.md（律师脱敏渠道补，挂 G4 商务线）

### T1.7 词条 TOP 30（→ 人工闸口 G2） ⬜
- **前置**：T1.2、T1.3
- **动作**：
  1. 按高频问题清单起草 30 条词条（试用期/未签合同/调岗/加班费/病假医疗期/三期/竞业/规章制度/仲裁时效……），每条含 conclusion / how_to / pitfalls / citations / regions / basis_date，引用全部可 resolve
  2. status 一律 in_review；生成 `data/review/entries_review.md` 审核清单（一条一段：全文 + 引用原文 + 勾选框）
- **验收**：建库词条 = 32（30 新 + 2 旧）且引用校验全过；审核清单生成；`match_entry` 移除硬编码 slug 特判，改为标题+关键词匹配（30 条规模下验证 TOP10 高频问法命中正确，写进 tests/test_pipeline.py）

### T1.8 嵌入预计算管线 ⬜
- **前置**：T1.2、T1.6；建议与 T2.2 同会话执行
- **动作**：`build_knowledge.py` 增加构建步骤：bge-m3 计算 legal_article.text、case_record.gist、entry 标题+结论的向量 → 存 `article_vec` / `case_vec` / `entry_vec` 表（sqlite-vec 格式）；无 sentence-transformers 环境时走 EMBEDDING_API_KEY，再没有则跳过并告警
- **验收**：建库后 `SELECT count(*) FROM article_vec` = 法条数；向量维度 = 1024

---

## 4. P2 检索与回答精度（W2–W6）

### T2.1 FTS5 + jieba 分词检索 🔶
> **状态注（06-13）**：已实现 FTS5 **二元组**召回 + 重叠精排 + 地区过滤（偏离卡内 jieba 方案，召回可用但查准待评测验证）；jieba 分词升级与本卡验收用例仍待做。
- **前置**：T1.2
- **动作**：建库时 jieba 分词后空格连接写入 `fts_article(text_seg)`（案例、词条同理）；`retrieve()` 第一路改为 FTS5 MATCH（查询同样 jieba 分词），保留地区过滤链
- **验收**：`tests/test_retrieval.py`（新建）："未签劳动合同二倍工资"召回劳动合同法第八十二条 TOP3；"经济补偿怎么算"召回第四十七条 TOP3

### T2.2 向量检索 + 混合融合 ⬜
- **前置**：T1.8、T2.1
- **动作**：`retrieve()` 第二路：问题向量 × sqlite-vec KNN（法条/案例双索引）；RRF 融合两路得分；输出统一候选 8–12 条（法条为主、案例 2–3 件）
- **验收**：`tests/test_retrieval.py` 增加语义改写用例（"员工不来上班几天能开除"召回旷工/解除相关条文）；FTS 路关闭时向量路单独可用（降级测试）

### T2.3 检索阈值标定 ⬜
- **前置**：T2.2、T2.7（用评测集标定）
- **动作**：新建 `src/calibrate.py`：在评测集上扫描置信度阈值（替换现 score<2 拍脑袋值），目标=拒答恰当率最大化；阈值写入 meta 表，pipeline 读取
- **验收**：calibrate 输出阈值-指标曲线表；run_eval 拒答恰当率 ≥ 80%

### T2.4 多轮对话（要素累积） 🔶
> **状态注（06-13）**：后端 session 要素累积已实现（/api/ask 收发 session_id）；前端 clarify 仍是纯文本列表，表单 chip 未做（与 T3.2 合并执行）。
- **前置**：T0.5
- **动作**：
  1. `/api/ask` 接受并返回 session_id；同 session 内 facts 合并（新值覆盖旧值，clarify 后只补缺）
  2. facts 累积存 qa_session（新列 facts_json，schema 加列）
  3. web 端：clarify 项渲染为可点选/快填的表单 chip（地区下拉、日期、金额输入），提交即续问
- **验收**：`tests/test_pipeline.py`：第一问缺月薪 → clarify；第二问只说"月薪一万五" → 同 session 完成计算。前端用 preview 工具走通同一流程并截图

### T2.5 案例进 RAG + 案例引用校验 🔶
> **状态注（06-13）**：retrieve_cases 与回答中案例区块已就绪；因案例数据 = 0（T1.6 未做）未实际生效，案号校验与地区适用性声明未实现。
- **前置**：T2.2
- **动作**：
  1. RAG 上下文 = 法条原文 + 相似案例（案号+裁判要旨），prompt 要求区分"法定规则"与"裁判倾向"两层作答，裁判倾向必须挂案号
  2. 校验器扩展：案例引用（案号）必须在提供的案例集合内，否则丢弃；正文出现"案"字样但无有效案号引用 → 整答拒绝
  3. 答案涉及存在地方规定的主题时，强制附"本答案适用地区：X"声明
- **验收**：`tests/test_pipeline.py`：mock LLM 编造案号 → 拒答；正常案例引用 → 透出且可展开

### T2.6 评测集 100 题（→ 人工闸口 G3） 🔶
> **状态注（06-13）**：起步集 18 题已随 T2.7 落地（机器金标准）。扩到 100 题、律师出题与终审（G3）、reasoner 语义判分仍待。
- **前置**：T1.2、T1.3、T1.6
- **动作**：
  1. 起草 100 题：覆盖 9 大主题 × 计算/概念/地方差异/应拒答四类；每题含金标准结论、金标准引用（必须可 resolve）、地区、难度 → `data/eval/eval_v1.jsonl` + 入 eval_item 表
  2. 其中 ≥ 15 题为"应拒答题"（个案争议/超纲/2008 前分段）
  3. 生成 `data/review/eval_review.md` 供律师终审
- **验收**：100 题金标准引用 100% 可 resolve；JSONL schema 校验脚本通过

### T2.7 评测跑分器 ✅
> **状态注（06-13）**：`src/run_eval.py` + 起步集 `data/eval/eval_v1.jsonl`（18 题，金标准机器可判：计算器金额/应拒答路由/法条存在性，非 LLM 自说自话）。四指标全达 M0 门槛：编造引用 0%✅ / 结论 88.9%✅ / 拒答 88.9%✅ / 引用完整 83.3%✅。编造>0 退出码非 0。露出 2 道真实失分（未签合同二倍工资、补偿封顶口径被误拒）→ 检索召回缺口，留 T2.1/T1.7 修。律师出题 100 题（T2.6）+ reasoner 语义判分仍待 G3。
- **前置**：T2.6
- **动作**：新建 `src/run_eval.py`：逐题过 pipeline，输出四指标——编造引用率（机器判：引用不在库内即编造）、引用完整率（金标准引用命中率）、拒答恰当率、结论正确率（deepseek-reasoner 当 judge 对照金标准，prompt 固定存 repo）；结果写 eval_run 表 + `data/eval/report_<日期>.md`
- **验收**：全量跑通出报告；硬指标达标判定自动化：**编造引用率 = 0 不达标则退出码非 0**；同时跑两次结论正确率波动 < 3pp（judge 稳定性）

### T2.8 评测纪律钩子 ⬜
- **前置**：T2.7
- **动作**：`tests/run_all.py` 增加 `--with-eval` 模式；新建 `scripts/preship.sh` = 测试 + 建库 + 评测，README 写明"动 prompt/检索/知识层必跑"
- **验收**：preship.sh 一键跑通，任何硬指标不达标退出码非 0

---

## 5. P3 界面双端（W4–W7）

### T3.1 PC 宽屏双栏布局 ✅
- **前置**：T2.5（依据面板要展示案例）
- **动作**：≥1100px 时左栏对话流 + 右栏"依据面板"（当前回答的法条原文与案例要旨常驻、可固定）；<1100px 维持现单栏。djhh 规则全覆盖
- **验收**：preview 1280px 与 375px 各截图一张；右栏法条可展开原文；无横向滚动条

### T3.2 移动端 clarify 表单 chip ⬜
- **前置**：T2.4（已含前端部分，此卡为补完打磨）
- **动作**：clarify 渲染地区下拉/日期选择/金额键盘 chip；点选后自动续问；输入框支持语音占位（仅 UI 预留）
- **验收**：preview 375px 走通"缺要素→点 chip 补全→出计算结果"全流程截图

### T3.3 案例页签 🔶
> **状态注（06-13）**：页签 UI + /api/cases 已上线，筛选已改为 change 即筛；数据 0 件显示空态，待 T1.6 灌入。
- **前置**：T1.6
- **动作**：新增"案例库"页签：争议类型 × 地区筛选，列表展示案号/法院/焦点/结果，详情展开裁判要旨 + 关联法条（复用 citeHtml）；server 加 `/api/cases` 只读接口
- **验收**：preview 中筛选"违法解除 × 上海"返回正确子集；移动端可用

### T3.4 文书模板页签（M1） ⬜
- **前置**：M0 后启动
- **动作**：template/template_version 数据填充（解除协议/合同到期通知/规章制度签收……每类先 2–3 份，AI 起草 + 风险提示，状态待审核走 G2）；前端分类下载页；下载记录写 template_download
- **验收**：下载一份模板文件成功且 template_download 有记录

### T3.5 微信 H5 适配（M1） ⬜
- **前置**：T3.1–T3.3
- **动作**：微信内置浏览器兼容性处理（vh 问题/字体/分享 meta）；部署到公网（方案另定：备案域名 + HTTPS）
- **验收**：微信开发者工具模拟器走通主流程

---

## 6. P4 合规与商务（AI 起草 + 人工推进）

| 卡 | 动作 | 产出 | 闸口 |
|---|---|---|---|
| T4.1 | 起草生成式 AI 备案评估报告（服务定位/算法机制/数据来源/安全措施） | `docs/compliance/filing_assessment.md` | 人工提交 |
| T4.2 | PIPL 数据清单 + 留存策略：qa_message 留存 180 天自动脱敏脚本 `src/retention.py` | 文档 + 脚本 + cron 说明 | 法务确认留存期 |
| T4.3 | consent 真实化：转介前弹窗勾选"同意将咨询摘要提供给合作律师"，consent_at 记真实勾选时间，未勾选不建工单 | 代码改动 | 无 |
| T4.4 | 起草致律协函：转介分成执业合规问题清单 | `docs/compliance/bar_association_letter.md` | 纯人工（G4） |

---

## 7. 执行顺序与并行建议

```
串行主线：T0.1 → T0.2 → T0.3 ─┐
          T0.4 / T0.5 / T0.6 ─┴→ T0.8 ──→ T1.1 ──→ T1.2 ──┬→ T1.4(G1)
                                                            ├→ T1.3
                                                            ├→ T1.6 ──→ T2.5
                                                            ├→ T1.7(G2)
                                                            └→ T1.8 ──→ T2.2 ─→ T2.3
可并行：T1.5（参数）、T2.1（FTS）与 T1.6（案例）互不依赖
评测线：T2.6(G3) → T2.7 → T2.8 → T2.3（标定回填）
界面线：T2.4 → T3.2；T2.5 → T3.1；T1.6 → T3.3
合规线：T4.1–T4.4 随时可做，建议 W2 启动
```

M0 验收（7 月底）= P0 全清 + T1.1–T1.8 + T2.1–T2.7 + T3.1–T3.3 完成，
评测四硬指标达标（编造引用率 = 0 / 结论正确率 ≥ 85% / 拒答恰当率 ≥ 80% / 引用完整率 ≥ 75%）。
