#!/usr/bin/env bash
# 劳法通 · Cloudflare Pages 一键部署
#
# 前置：先执行 `npx wrangler login`（一次性，浏览器授权你的 Cloudflare 账号）。
# 登录后，本脚本全自动：建 D1（或复用）→ 回填 database_id → 建表 → 灌数据 → 发布。
# 幂等：可重复运行；数据用 INSERT OR IGNORE，不会重复插。
#
# 用法：bash scripts/deploy_cf.sh
set -euo pipefail
cd "$(dirname "$0")/.."

WR="npx wrangler"
DB_NAME="ldlawq"

echo "▶ 0/5 检查登录状态…"
if ! $WR whoami 2>/dev/null | grep -qi "account"; then
  echo "✗ 未登录 Cloudflare。先运行：npx wrangler login"
  exit 1
fi

echo "▶ 1/5 确保 D1 数据库存在…"
if $WR d1 list 2>/dev/null | grep -q "$DB_NAME"; then
  echo "  已存在 $DB_NAME，复用。"
else
  $WR d1 create "$DB_NAME"
fi

echo "▶ 2/5 回填 database_id 到 wrangler.toml…"
DB_ID=$($WR d1 list --json 2>/dev/null | python3 -c "import sys,json;
rows=json.load(sys.stdin)
print(next((r['uuid'] for r in rows if r['name']=='$DB_NAME'), ''))")
if [ -z "$DB_ID" ]; then echo "✗ 取不到 database_id"; exit 1; fi
python3 - "$DB_ID" <<'PY'
import re, sys, pathlib
db_id = sys.argv[1]
p = pathlib.Path("wrangler.toml")
t = p.read_text(encoding="utf-8")
t = re.sub(r'database_id = "[^"]*"', f'database_id = "{db_id}"', t)
p.write_text(t, encoding="utf-8")
print(f"  database_id = {db_id}")
PY

echo "▶ 3/5 重建本地知识库 + 导出 D1 种子 SQL…"
python3 src/build_knowledge.py >/dev/null
python3 scripts/export_d1_seed.py

echo "▶ 4/5 远程建表 + 灌数据…"
$WR d1 execute "$DB_NAME" --remote --file=migrations/0001_schema.sql
$WR d1 execute "$DB_NAME" --remote --file=migrations/0002_seed.sql

echo "▶ 5/5 发布到 Cloudflare Pages…"
$WR pages deploy web/ --project-name="$DB_NAME"

echo ""
echo "✓ 部署完成。最后一步（仅首次）：到 Cloudflare 控制台 → Pages → $DB_NAME → 设置 → 环境变量，"
echo "  添加 DEEPSEEK_API_KEY（值见本地 .env），保存后重新部署一次即可启用问答/文书/AI 审核。"
echo "  纯算钱/法库/案例/词条无需 key 即可用。"
