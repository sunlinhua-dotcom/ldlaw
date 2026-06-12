# LDLAWQ 开发约定

劳动法 HR 问答 Agent（劳法通）。可信架构：只依据库内法条作答、涉钱永远走代码、
算不准转律师。本文件是所有开发会话的约定入口，**与代码冲突时以代码为准并回改本文件**。

## 文档地图与文档纪律

```
README.md           入口 + 当前状态        ← 功能/数据有变化就同步「当前状态」表
AGENTS.md           本文件：约定与纪律（CLAUDE.md 的自动镜像）
docs/PRD.md         产品需求（稳定）
docs/PLAN.md        改进计划（稳定）
docs/EXECUTION.md   任务卡 + 状态总览      ← 唯一进度真相源：开工看、收工打勾
data/raw/MISSING.md 法规缺口清单（ingest_law.py 维护）
```

- **禁止新增顶层 .md**。新文档一律进 `docs/`（合规文档放 `docs/compliance/`）。
- 过程性草稿、会话笔记**不入库**；有长期价值的结论合并进上述五份文档之一。
- 进度只记在 EXECUTION.md 的状态标注里，不在多处重复维护。
- 代码注释引用任务卡必须用 EXECUTION.md 里真实存在的卡号（历史教训：
  医疗期防线曾误标 T2.6，与评测集卡号冲突）。

## 常用命令

```bash
python3 src/server.py                  # 启动 Web（自动建库，端口 8400）
python3 src/build_knowledge.py         # 重建知识库（动 data/seed/ 后必跑）
python3 tests/test_calculators.py      # 测试（改代码后必跑；run_all.py 建成后换它）
python3 src/qa_demo.py "<问题>" [地区]  # 命令行问答
python3 src/ingest_law.py              # 法规抓取切条（失败项写 data/raw/MISSING.md）
python3 src/websearch.py "<法规名>" --official   # 博查联网搜索（找官方源；key 在 .env）
```

## 架构不变量（改之前先停下来想）

1. **运行时纯 Python 标准库**。第三方包白名单（jieba / sqlite-vec /
   sentence-transformers）只允许出现在**构建期**脚本，server.py 永不引入。
2. **双库**：knowledge.db 只读、发布 = 整库重建替换；app.db 读写（WAL）。
   schema 改动只允许加表加列（schema/*.sql 同步改）。
3. **数据只从 `data/seed/` JSON 进库**，从不手改 .db；法条/案例引用入库时
   过存在性校验，解析不出即建库失败。
4. **涉钱计算永远在 calculators.py（确定性代码）**，LLM 只做要素抽取与 RAG 生成；
   六道防线（路由/引用存在性/引用范围/数字溯源/拒答闸门/医疗期等硬规则）全在代码层。
5. **前端维持 `web/index.html` 单文件**，不引框架；中文排版遵守 djhh
   （keep-all / 数字 nowrap / 千分位；注意：keep-all 下长句在 flex 项内
   必须配 `min-width:0; max-width:100%`，否则窄屏横向溢出）。
6. **禁止爬裁判文书网**（合规红线）；法条官方源 = flk.npc.gov.cn 优先。
   联网搜索（websearch.py / 博查）只许运营、知识层脚本调用，
   **server.py / pipeline.py 禁止 import**——问答侧接入（方案 B）须另行评审开卡。
7. LLM 密钥只在服务端 `.env`（权限 600），永不下发前端、永不入 git。

## 反屎山规则（代码放哪、何时拆）

- **新功能域 = 新模块文件**，不要再往 pipeline.py 里堆。即将到来的：
  检索升级 → `src/retrieval.py`；评测 → `src/run_eval.py`；案例入库 →
  `src/ingest_case.py`；核验 → `src/verify_articles.py`；留存脱敏 → `src/retention.py`。
- **pipeline.py 当前 ~1100 行，已到上限**。下次对它做任何 ≥50 行的新增前，先按
  既有分区注释拆出独立模块（要素抽取 / 引用解析 / 检索 / RAG / 文书起草 /
  AI 审核），pipeline.py 保留 `answer_structured` 等门面接口，server.py 的
  import 不变。拆分必须单独成一个 commit，且测试全绿才许合入。
- **web/index.html 按现有 `/* ===== 分区 ===== */` 注释组织** CSS 与 JS；
  新页签 = 新分区，禁止跨区互相引用内部变量；超过 ~2000 行时再议构建拆分。
- 同一份常量只定义一处（教训：地区列表曾在 HTML select 和 JS 数组里重复硬编码，
  2026-06-13 已统一改为从 `/api/db/regions` 构建——别再退回去）。
- 删代码优于注释代码；确需保留的死代码必须挂任务卡号注释说明何时启用。

## 工程纪律

- 提交格式：`[T1.2] 全国性法规入库：劳动合同法 98 条`（卡号对应 EXECUTION.md）。
- 改代码必跑测试；动 `data/seed/` 或建库逻辑必跑 `build_knowledge.py`（看未核验计数）；
  动前端必须 375px + 1280px 双视口验证（无横向滚动）。
- 评测体系（T2.7）上线后：动 prompt / 检索 / 知识层必跑 `run_eval.py`，
  **编造引用率 > 0 或分数回退，不允许提交**。
- 收工三件事：EXECUTION.md 打勾 → README「当前状态」同步 → git commit。
