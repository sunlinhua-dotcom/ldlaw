# LDLAWQ — 劳法通 · 劳动法智能百科

面向企业 HR 的劳动法百科 + 问答 Agent：固定法条库 + 确定性计算器 + 可信问答管线
（只依据库内法条作答，算不准就转律师）。律协渠道分发。

## 文档地图（先读这个）

| 文档 | 角色 | 什么时候读 / 改 |
|---|---|---|
| `README.md` | 入口：快速开始 + 当前状态 | 状态变化时同步（功能上线、数据扩容） |
| `CLAUDE.md` | 开发约定：结构、纪律、反屎山规则 | 每个开发会话自动加载；约定变更时改 |
| `docs/PRD.md` | 产品全貌 v0.1.1（需求、表结构、里程碑） | 做功能前查需求口径；基本稳定 |
| `docs/PLAN.md` | 改进计划 v1.0（战略、阶段、风险） | 排期与取舍时读；基本稳定 |
| `docs/EXECUTION.md` | 任务卡（含**状态总览**，唯一进度真相源） | 每次开工前看状态，收工时打勾 |

## 快速开始

```bash
python3 src/server.py                 # 启动 Web（自动建库；http://127.0.0.1:8400）

# —— 或分步 ——
python3 src/build_knowledge.py        # 重建 knowledge.db + 初始化 app.db
python3 tests/run_all.py              # 全套单元测试（30 个）
python3 src/run_eval.py              # 评测跑分（四硬指标，编造引用>0 即非 0 退出）
python3 src/qa_demo.py "上海员工 2023 年 6 月入职，月薪 15000 元，协商解除要付多少补偿？"
```

依赖：Python 3.10+ 标准库，运行时零第三方包。
**LLM**：DeepSeek（密钥放 `.env`，仅服务端），无 key / 断网自动降级规则引擎；
路由、计算、引用校验、数字溯源、拒答闸门全部在代码层（docs/PRD.md §7）。

## 目录结构

```
CLAUDE.md                  开发约定（每个 Claude 会话自动加载）
README.md                  本文件
docs/                      产品与计划文档（PRD / PLAN / EXECUTION）
schema/
  knowledge.sql            知识库 DDL（只读库，发布 = 整库重建替换）
  app.sql                  业务库 DDL（问答日志/转介工单，WAL 读写）
src/
  server.py                HTTP 服务 + API（纯标准库）
  pipeline.py              可信问答管线（路由/检索/RAG/校验/文书/审核）
  calculators.py           确定性计算器（N / 2N / 年假折算）
  llm.py                   DeepSeek 封装（chat_json，类型防御）
  build_knowledge.py       建库（含引用存在性校验，失败即建库失败）
  ingest_law.py            法规抓取 + 切条工具（产出 data/seed/laws/*.json）
  verify_articles.py       法条核验流水线（seed vs 官方快照逐字 diff → G1 置 verified）
  run_eval.py              评测跑分器（四硬指标，编造引用>0 即非 0 退出）
  websearch.py             博查联网搜索（仅运营/知识层用，问答管线不接入）
  zhnum.py                 中文数字归一化
  qa_demo.py               命令行问答演示
tests/                     单元测试（python3 tests/run_all.py，30 用例）
data/eval/eval_v1.jsonl    起步评测集 18 题（机器金标准）
data/
  raw/                     法规原文 txt + MISSING.md（待补抓清单）
  seed/                    种子数据（laws/*.json、词条、地区参数）——唯一数据入口
db/                        构建产物 knowledge.db / app.db（不入版本管理）
web/index.html             前端单文件 SPA（判例纸卷设计，六页签，双端自适应）
```

## 当前状态（2026-06-13 实测）

| 项 | 数量 | 说明 |
|---|---|---|
| 法规 | 15 部 | 缺：工会法、劳动争议司法解释（二）、上海市企业工资支付办法 |
| 法条 | 719 条 | **全部 verified = 0**；核验工具就绪，11 部逐字一致待 G1 批准 |
| 百科词条 | 30 条 | 全部 in_review 待 G2 律师审核（清单已生成）|
| 地区参数 | 22 项 | 全部占位值，输出带 ⚠ 标 |
| 案例 | 0 件 | 案例页签 UI 已就绪，T1.6 数据未启动 |
| 测试 | 30 全绿 | 计算器/管线/引用/联网搜索/核验（`python3 tests/run_all.py`） |
| 评测 | 4 指标达标 | eval_v1 18 题：编造 0% / 结论·拒答·引用完整 100%（起步集） |

界面六页签：问答 · 文书（AIGC 起草 + AI 律师审核）· 算钱 · 案例 · 词条 · 法库。
PC 三栏（书脊导航 + 内容 + 法条依据面板），移动端底部 Tab，375px 无横向滚动。
问答支持多轮：缺要素时给出可点选/快填的补全表单，同会话累积要素续算。
法库页以「六道防线」可信架构图开篇（纯 SVG/CSS、单文件、不引框架）：朱砂序号印
+ 闸门线性图标 + 描线脊柱，把路由 / 算钱 / 引用 / 数字 / 案号 / 拒答六道代码层把关
可视化呈现，双端验证无横向溢出。引用卡按来源层级（法律 / 行政法规 / 司法解释 /
地方 / 文件）标注 SVG 图标（层级取自 `/api/db/sources`，非启发式）；库存概览数字
入视 count-up（rAF + 安全兜底，绝不停在 0），待核验数朱砂高亮以示数据透明。
法库四张数据表在窄屏（≤767px）改为堆叠卡片：每条记录一卡、字段带标签、法条原文 /
法规名称等长文本整卡宽换行，不再左右拖（grid `minmax(0,1fr)` 绕开 flex 撑破）；
桌面端仍为常规表格。

## ⚠ 数据状态（重要）

- 全部法条文本与地区参数当前为演示稿（verified = 0），上线前必须由运营从官方源
  （flk.npc.gov.cn、各地人社官网）逐字核对置 verified = 1，并经律师确认。
- 构建脚本输出未核验计数；**生产发布门槛 = 未核验数 0**（docs/PRD.md §10）。

## 下一步主线（详见 docs/EXECUTION.md 状态总览）

P0 全清；T1.4 核验 / T2.7 评测 / T1.7 词条 30 条 / T2.4 多轮补全 已就绪。接下来需**人或真实数据**：
- **G1**：运营跑 `python3 src/verify_articles.py --approve <slug>`，把逐字一致的法规置 verified
- **G2**：律师过 `data/review/entries_review.md`（30 词条审核清单）
- **真实数据**（不可编造）：案例库 T1.6（官方案号+裁判要旨）、市级社平参数 T1.5、
  补抓 3 部法规（工会法 / 司法解释二 / 上海工资支付办法，见 `data/raw/MISSING.md`）
- **后续工程**：评测扩到 100 题 T2.6（律师出题 G3）→ 检索升级 T2.1（jieba，需先定运行时依赖口径）
