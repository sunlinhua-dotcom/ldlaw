"""案例入库工具 —— 官方公开案例 → 种子库（接入方案 A，仅建库期/运营脚本）。

用途（EXECUTION T1.6 / PRD F案例库）：
- `--discover`：用 websearch.py（博查 --official）发现**官方公开发布**的典型/指导案例源；
- `--validate`：校验 data/seed/cases.json 每条记录是否可安全入库；
- `--merge`：把一份候选案例 JSON 并入 data/seed/cases.json（去重、保留 verified 状态）。

合规边界（CLAUDE.md 架构不变量 #6）——硬红线：
- **绝不爬裁判文书网**（wenshu.court.gov.cn）。案例只许来自官方公开发布物
  （最高法指导性案例/典型案例、人社部及各地人社「劳动人事争议典型案例」、各省高院典型案例）。
- 本模块只在建库期/运营侧运行，**不得**被 server.py / pipeline.py 引用。
- 每条入库即 source_channel=official_release、脱敏、引用过存在性校验、verified=0，
  须经律师复审（G 闸：本脚本不置 verified=1，核验置位另走人工流程）。

案例记录字段（与 build_knowledge.py 读取一致）：
  title(必填) gist(必填) court region cause facts_summary result(employee_win/
  employer_win/partial) decided_date tags[] citations[{source,article_no,clause_no?}]
  source_channel(默认 official_release) license_note(放官方源 URL) verified(默认 0)

用法：
    python3 src/ingest_case.py --discover "劳动人事争议 典型案例" -n 8
    python3 src/ingest_case.py --validate                       # 校验 seed/cases.json
    python3 src/ingest_case.py --merge /tmp/harvest.json        # 并入 seed（去重）
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from zhnum import norm_clause
import websearch

ROOT = Path(__file__).resolve().parent.parent
KDB = ROOT / "db" / "knowledge.db"
SEED_CASES = ROOT / "data" / "seed" / "cases.json"

RESULT_ENUM = {"employee_win", "employer_win", "partial"}
CHANNEL_ENUM = {"official_release", "licensed_db", "partner_lawyer"}
# 官方案例发布渠道关键词（仅用于 discover 的查询拼装与提示，非白名单）
OFFICIAL_HINT = ("最高人民法院", "人力资源社会保障部", "人社部", "高级人民法院",
                 "人力资源和社会保障", "劳动人事争议仲裁")


def _db():
    if not KDB.exists():
        sys.exit(f"× 知识库不存在：{KDB}。先 python3 src/build_knowledge.py")
    con = sqlite3.connect(KDB)
    con.row_factory = sqlite3.Row
    return con


def _resolve(con, source: str, article_no: str, clause_no=None) -> bool:
    """引用是否落在库内（与 build_knowledge._resolve 同口径）。"""
    row = con.execute(
        """SELECT 1 FROM legal_article la JOIN legal_source ls ON ls.id = la.source_id
           WHERE ls.title = ? AND la.article_no = ? AND ifnull(la.clause_no,'') = ifnull(?, '')""",
        (source, article_no, norm_clause(clause_no))).fetchone()
    return row is not None


def discover(term: str, n: int) -> int:
    """搜索官方源典型/指导案例，打印候选（供人工/agent 取材，不自动入库）。"""
    try:
        hits = websearch.search(term, count=max(n * 2, 10), official_only=True)
    except Exception as e:  # noqa: BLE001
        sys.exit(f"× 联网搜索失败：{e}")
    if not hits:
        print("（无官方源命中；换主题词或去掉 --official 看全网）")
        return 0
    for i, h in enumerate(hits[:n], 1):
        print(f"{i}. {h['title']}　·　{h['site']}　·　{h['date']}")
        print(f"   {h['url']}")
        if h.get("summary"):
            print(f"   摘要：{h['summary'][:120]}")
    print(f"\n共 {min(n, len(hits))} 条官方源候选。"
          "请据官方原文结构化为案例记录，core 字段必填，引用仅限库内法规，verified=0 待律师复审。")
    return 0


def validate(path: Path, strict: bool = True) -> int:
    """逐条校验候选/种子案例，返回失败条数。硬伤：gist 缺失/result 越界/渠道非法/引用未落库/无官方源 URL。"""
    if not path.exists():
        sys.exit(f"× 文件不存在：{path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    cases = data["cases"] if isinstance(data, dict) else data
    con = _db()
    tags_ok = {r[0] for r in con.execute("SELECT name FROM dispute_tag")}
    regions_ok = {r[0] for r in con.execute("SELECT name FROM region")}

    hard, soft = 0, 0
    for i, c in enumerate(cases, 1):
        errs, warns = [], []
        title = c.get("title") or c.get("gist", "")[:18] or f"#{i}"
        if not (c.get("gist") or "").strip():
            errs.append("gist 为空")
        if c.get("result") not in RESULT_ENUM and c.get("result") is not None:
            errs.append(f"result 越界：{c.get('result')}")
        ch = c.get("source_channel", "official_release")
        if ch not in CHANNEL_ENUM:
            errs.append(f"source_channel 非法：{ch}")
        note = c.get("license_note", "") or ""
        url = next((tok for tok in note.replace("｜", " ").split() if tok.startswith("http")), "")
        if not url:
            errs.append("license_note 缺官方源 URL")
        elif not websearch.is_official(url):
            warns.append(f"源 URL 非 .gov.cn：{url}")
        if "wenshu.court.gov.cn" in note:
            errs.append("红线：疑似裁判文书网来源")
        if c.get("region") and c["region"] not in regions_ok and c["region"] != "CN":
            warns.append(f"region 未知（将落 全国）：{c['region']}")
        for t in c.get("tags", []):
            if t not in tags_ok:
                warns.append(f"标签将被丢弃（不在 DISPUTE_TAGS）：{t}")
        for ct in c.get("citations", []):
            if not _resolve(con, ct.get("source", ""), ct.get("article_no", ""), ct.get("clause_no")):
                errs.append(f"引用未落库：{ct.get('source')}{ct.get('article_no')}")
        if c.get("verified"):
            warns.append("verified=1（应由律师复审置位，入库前置 0）")
        if errs:
            hard += 1
            print(f"✗ [{i}] {title}：" + "；".join(errs))
        elif warns:
            soft += 1
            print(f"△ [{i}] {title}：" + "；".join(warns))
        else:
            print(f"✓ [{i}] {title}")
    print(f"\n汇总：{len(cases)} 条，硬伤 {hard}，提示 {soft}。"
          + ("可入库（重建生效）。" if hard == 0 else "存在硬伤，修正后再 build。"))
    return hard


def clean(path: Path) -> int:
    """清洗候选/种子案例：引用降到能落库的条级（库为条粒度，款号过细则去款），
    真缺的引用丢弃；按 (来源URL, 结果, 案情前 14 字) 去内容重复。原地写回。"""
    data = json.loads(path.read_text(encoding="utf-8"))
    cases = data["cases"] if isinstance(data, dict) else data
    con = _db()
    seen, kept = set(), []
    n_clause_dropped = n_cite_dropped = n_dup = 0
    for c in cases:
        url = next((t for t in (c.get("license_note", "") or "").replace("｜", " ").split()
                    if t.startswith("http")), "")
        key = (url, c.get("result"), (c.get("facts_summary", "") or "")[:14])
        if key in seen:
            n_dup += 1
            print(f"  去重：{(c.get('title') or '')[:30]}")
            continue
        seen.add(key)
        cleaned_cites = []
        for ct in c.get("citations", []):
            src, art, cl = ct.get("source", ""), ct.get("article_no", ""), ct.get("clause_no")
            if _resolve(con, src, art, cl):
                cleaned_cites.append(ct)
            elif cl and _resolve(con, src, art, None):
                n_clause_dropped += 1
                cleaned_cites.append({"source": src, "article_no": art})  # 降到条级
            else:
                n_cite_dropped += 1
                print(f"  丢弃库外引用：{(c.get('title') or '')[:24]} → {src}{art}")
        c["citations"] = cleaned_cites
        kept.append(c)
    path.write_text(json.dumps({"cases": kept}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n✓ 清洗完成：{len(cases)} → {len(kept)} 件（去重 {n_dup}）；"
          f"引用降条级 {n_clause_dropped}、丢弃库外 {n_cite_dropped}。")
    return 0


def merge(src: Path) -> int:
    """把候选 JSON 并入 seed/cases.json（按 title 去重，新覆盖旧；强制 verified=0）。"""
    incoming = json.loads(src.read_text(encoding="utf-8"))
    incoming = incoming["cases"] if isinstance(incoming, dict) else incoming
    existing = (json.loads(SEED_CASES.read_text(encoding="utf-8"))["cases"]
                if SEED_CASES.exists() else [])
    by_title = {c.get("title"): c for c in existing}
    for c in incoming:
        c.setdefault("source_channel", "official_release")
        c["verified"] = 0  # 律师复审前一律未核验
        by_title[c.get("title")] = c
    merged = list(by_title.values())
    SEED_CASES.parent.mkdir(parents=True, exist_ok=True)
    SEED_CASES.write_text(json.dumps({"cases": merged}, ensure_ascii=False, indent=2),
                          encoding="utf-8")
    print(f"✓ 并入 {len(incoming)} 条，seed/cases.json 现 {len(merged)} 条。"
          "下一步：python3 src/ingest_case.py --validate && python3 src/build_knowledge.py")
    return 0


def main() -> None:
    ap = argparse.ArgumentParser(description="官方案例发现/校验/入库（仅建库期）")
    ap.add_argument("--discover", metavar="TERM", help="搜索官方源典型/指导案例")
    ap.add_argument("--validate", action="store_true", help="校验 seed/cases.json（或 --file）")
    ap.add_argument("--clean", action="store_true", help="清洗 seed/cases.json：引用降条级/丢库外 + 去重")
    ap.add_argument("--merge", metavar="FILE", help="把候选 JSON 并入 seed/cases.json")
    ap.add_argument("--file", metavar="FILE", help="--validate 指定文件（默认 seed/cases.json）")
    ap.add_argument("-n", type=int, default=8, help="discover 返回条数")
    a = ap.parse_args()
    if a.discover:
        sys.exit(discover(a.discover, a.n))
    if a.clean:
        sys.exit(clean(Path(a.file) if a.file else SEED_CASES))
    if a.merge:
        sys.exit(merge(Path(a.merge)))
    if a.validate:
        sys.exit(1 if validate(Path(a.file) if a.file else SEED_CASES) else 0)
    ap.print_help()


if __name__ == "__main__":
    main()
