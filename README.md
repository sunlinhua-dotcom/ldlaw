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
python3 tests/test_calculators.py     # 计算器单元测试（14 个）
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
  zhnum.py                 中文数字归一化
  qa_demo.py               命令行问答演示
tests/                     单元测试（python3 tests/test_calculators.py）
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
| 法条 | 719 条 | **全部 verified = 0（未经官方源核验）** |
| 百科词条 | 2 条 | T1.7 目标 30 条 |
| 地区参数 | 22 项 | 全部占位值，输出带 ⚠ 标 |
| 案例 | 0 件 | 案例页签 UI 已就绪，T1.6 数据未启动 |
| 测试 | 14 全绿 | 仅覆盖计算器；管线/检索测试未建 |

界面六页签：问答 · 文书（AIGC 起草 + AI 律师审核）· 算钱 · 案例 · 词条 · 法库。
PC 三栏（书脊导航 + 内容 + 法条依据面板），移动端底部 Tab，375px 无横向滚动。

## ⚠ 数据状态（重要）

- 全部法条文本与地区参数当前为演示稿（verified = 0），上线前必须由运营从官方源
  （flk.npc.gov.cn、各地人社官网）逐字核对置 verified = 1，并经律师确认。
- 构建脚本输出未核验计数；**生产发布门槛 = 未核验数 0**（docs/PRD.md §10）。

## 下一步主线（详见 docs/EXECUTION.md 状态总览）

核验流水线 T1.4 → 统一测试入口 T0.8 → 案例库 T1.6 / 词条 30 条 T1.7 →
评测集与跑分 T2.6–T2.8（**没有评测就没有"精准"可言**）。
