# 劳法通 · Cloudflare Pages 部署指南

与 kenleme 同架构：静态站（`web/`）+ Pages Functions（`functions/api/`）+ D1 数据库。

## 一次性设置

### 1. 安装 wrangler 并登录

```bash
npm install -g wrangler
wrangler login
```

### 2. 创建 D1 数据库

```bash
wrangler d1 create ldlawq
# 输出类似：database_id = "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
```

将 `database_id` 填入 `wrangler.toml`：

```toml
[[d1_databases]]
binding = "DB"
database_name = "ldlawq"
database_id = "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"  ← 填这里
```

### 3. 建表

```bash
wrangler d1 execute ldlawq --file=migrations/0001_schema.sql
```

### 4. 导入种子数据

先确保本地知识库已构建：

```bash
python3 src/build_knowledge.py   # 构建 db/knowledge.db
python3 scripts/export_d1_seed.py  # 导出到 migrations/0002_seed.sql
wrangler d1 execute ldlawq --file=migrations/0002_seed.sql
```

> 数据有更新时重复此步骤即可（INSERT OR IGNORE，不覆盖已有记录）。
> 若要完全重建：先删除 D1 中所有表，再重新执行 0001 + 0002。

### 5. 本地预览（带 D1）

```bash
wrangler pages dev web/ --d1=DB=ldlawq
# 打开 http://localhost:8788
```

### 6. 部署到 Cloudflare Pages

```bash
wrangler pages deploy web/
```

首次部署后，在 Cloudflare Pages 控制台配置环境变量：

| 变量名 | 必填 | 说明 |
|---|---|---|
| `DEEPSEEK_API_KEY` | ✓ | DeepSeek 密钥（建议加密 Secret） |
| `DEEPSEEK_MODEL` | 可选 | 默认 `deepseek-chat` |

## 后续更新

代码变更：
```bash
wrangler pages deploy web/
```

数据变更（种子 JSON 有变动后重建知识库）：
```bash
python3 src/build_knowledge.py
python3 scripts/export_d1_seed.py
wrangler d1 execute ldlawq --file=migrations/0002_seed.sql
wrangler pages deploy web/
```

## 说明

- **D1** 替代了本地的两个 SQLite 文件（`knowledge.db` + `app.db` 合并为一个 D1 库）。
- **Functions** 是 `functions/api/` 里的 JS 文件，与 kenleme 的 `functions/api/analyze.js` 完全同等地位。
- **`web/index.html`** 零改动发布，`location.origin` 自动指向线上域名，无 `localhost:8400` 硬编码问题。
- **DEEPSEEK_API_KEY** 只存服务端环境变量，前端 F12 看不到，与本地 `.env` 完全同等的保密级别。
