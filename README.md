# LDLAWQ — 劳动法智能百科（M0 工程骨架）

面向企业 HR 的劳动法百科 + 问答 Agent。产品全貌见 [PRD.md](PRD.md)。

## 快速开始

```bash
# 1. 启动 Web 演示（自动建库；浏览器打开 http://127.0.0.1:8400）
python3 src/server.py

# —— 或分步 ——
python3 src/build_knowledge.py        # 构建 knowledge.db（整库重建）+ 初始化 app.db
python3 tests/test_calculators.py    # 计算器单元测试
python3 src/qa_demo.py "上海员工 2023 年 6 月入职，月薪 15000 元，协商解除要付多少补偿？"
python3 src/qa_demo.py "入职两个月还没签劳动合同有什么风险？" 上海   # RAG 路径
python3 src/qa_demo.py "员工要求公司报销宠物医疗费怎么办？"          # 拒答转律师
```

依赖：仅 Python 3.10+ 标准库（sqlite3 内置），零第三方包。

**LLM**：DeepSeek，密钥放项目根目录 `.env`（`DEEPSEEK_API_KEY=…`），只在服务端使用、
不下发前端；无 key / 断网时自动降级为规则引擎。LLM 仅承担要素抽取与 RAG 生成，
路由、计算、引用存在性校验、数字溯源校验、拒答闸门全部在代码层（PRD §7）。

**Web 界面**：四个页签——智能问答（结构化回答 + 法条原文折叠 + 转律师）、计算器
（经济补偿 N / 2N、年假折算）、百科词条、知识库（法规 / 法条 / 参数实时浏览，即
「看得见的数据库」）。移动端自适应已验证（375px 视口），中文排版按 djhh 规则
（keep-all / 数字 nowrap / 千分位）。

## 目录结构

```
PRD.md                     产品需求文档 v0.1.1
schema/knowledge.sql       知识库 DDL（只读库，发布 = 整库重建替换）
schema/app.sql             业务库 DDL（WAL 读写库）
data/seed/                 种子数据（法规/法条、地区参数、词条）
src/build_knowledge.py     构建脚本（含词条引用存在性校验，校验失败即构建失败）
src/calculators.py         确定性计算器（经济补偿 N / 赔偿金 2N / 年假折算）
src/qa_demo.py             可信问答管线最小演示（要素确认→路由→引用→拒答→双库日志）
tests/test_calculators.py  计算器单元测试
db/                        构建产物（knowledge.db / app.db，不入版本管理）
```

## ⚠ 数据状态（重要）

- **全部法条文本与地区参数当前为演示稿（verified = 0）**，上线前必须由运营从官方源
  （国家法律法规数据库 flk.npc.gov.cn、各地人社官网）逐字核对后置 verified = 1，并经律师确认。
- 构建脚本会输出未核验计数；**生产发布门槛 = 0**（见 PRD §10）。
- 地区参数（社平工资等）为占位数值，问答输出中会显式打 ⚠ 标。

## M0 进度（按 PRD §11）

- [x] LLM 接入（DeepSeek）：要素抽取 + RAG 生成，带规则引擎降级
- [x] 生成后校验器：引用存在性 + 引用落在检索集合内 + 数字溯源（PRD §7 防线 3/4）
- [x] Web 演示界面（问答 / 计算器 / 词条 / 知识库浏览），移动端自适应
- [ ] sqlite-vec 向量检索 + FTS5 中文分词 tokenizer（当前检索为二元组重叠打分）
- [ ] 词条扩到 TOP 20 × 沪苏；法条官方核验流程跑通（verified 全部置 1）
- [ ] 评测集 100 题（律师出题）+ 评测跑分脚本
- [ ] 备案合规评估（生成式 AI 服务 / PIPL）启动
