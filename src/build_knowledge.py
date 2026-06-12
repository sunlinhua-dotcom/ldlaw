"""构建 knowledge.db（整库重建）并初始化 app.db。

用法：python3 src/build_knowledge.py

发布纪律（与 PRD §7/§8 对应）：
- 法规来源：data/seed/laws/*.json（ingest_law.py 自官方页面切条产出）；
- 款号统一归一化为中文数字（T0.2，修复引用静默丢失）；
- 词条引用 + 计算器引用做存在性校验：找不到 → 构建失败；
- 案例引用做存在性校验：找不到 → 丢弃该条引用并计数告警（案例容错，法条零容忍）；
- 未核验（verified=0）条文/参数/案例在摘要中给出计数，生产发布门槛 = 0；
- FTS5 二元组索引（fts_article / fts_case）供检索（T2.1，M1 升级中文分词+向量）。
"""
import json
import re
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from zhnum import norm_clause

ROOT = Path(__file__).resolve().parent.parent
SCHEMA = ROOT / "schema"
SEED = ROOT / "data" / "seed"
DB_DIR = ROOT / "db"

REGIONS = [
    # (id, code, name, level, parent_id)
    (1, "CN", "全国", "country", None),
    (2, "310000", "上海", "province", 1),
    (3, "320000", "江苏", "province", 1),
    (4, "330000", "浙江", "province", 1),
    (5, "110000", "北京", "province", 1),
    (6, "120000", "天津", "province", 1),
    (7, "130000", "河北", "province", 1),
    (8, "440000", "广东", "province", 1),
    (9, "440100", "广州", "city", 8),
    (10, "440300", "深圳", "city", 8),
    (11, "320100", "南京", "city", 3),
    (12, "320200", "无锡", "city", 3),
    (13, "320400", "常州", "city", 3),
    (14, "320500", "苏州", "city", 3),
    (15, "330100", "杭州", "city", 4),
    (16, "330200", "宁波", "city", 4),
]

TOPICS = [
    "招聘入职", "劳动合同", "工时与加班", "休息休假", "工资与福利",
    "社保公积金", "规章制度", "调岗调薪", "解除与终止", "经济补偿与赔偿",
    "竞业限制与保密", "女职工与三期", "工伤", "劳动争议",
]

DISPUTE_TAGS = [
    "违法解除", "未签合同二倍工资", "调岗调薪", "加班费", "竞业限制",
    "三期女职工", "年休假", "经济补偿", "规章制度", "工伤", "劳动报酬",
    "劳动关系确认",
]


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _bigram_seg(text: str) -> str:
    """中文二元组分段（空格连接），写入 FTS5 列供 unicode61 切词。"""
    cjk = re.sub(r"[^一-鿿]", "", text)
    return " ".join(cjk[i:i + 2] for i in range(len(cjk) - 1))


def _resolve(con, source_title: str, article_no: str, clause_no=None):
    return con.execute(
        """SELECT la.id FROM legal_article la
           JOIN legal_source ls ON ls.id = la.source_id
           WHERE ls.title = ? AND la.article_no = ?
             AND ifnull(la.clause_no,'') = ifnull(?, '')""",
        (source_title, article_no, norm_clause(clause_no))).fetchone()


def _resolve_ref_str(con, ref: str):
    """'《法规名》第X条[第Y款]' → article_id 或 None。"""
    m = re.match(r"《(.+?)》(第.+?条)(?:(第.+?款))?$", ref.strip())
    if not m:
        return None
    clause = m.group(3)[1:-1] if m.group(3) else None  # 去掉"第/款"
    return _resolve(con, m.group(1), m.group(2), clause)


def load_law_files() -> list[dict]:
    laws_dir = SEED / "laws"
    out = []
    for f in sorted(laws_dir.glob("*.json")):
        if f.name.startswith("_"):
            continue
        out.append(json.loads(f.read_text(encoding="utf-8")))
    return out


def build_knowledge() -> None:
    DB_DIR.mkdir(exist_ok=True)
    db_path = DB_DIR / "knowledge.db"
    if db_path.exists():
        db_path.unlink()
    con = sqlite3.connect(db_path)
    con.executescript((SCHEMA / "knowledge.sql").read_text(encoding="utf-8"))

    con.executemany(
        "INSERT INTO region(id, code, name, level, parent_id) VALUES (?,?,?,?,?)", REGIONS)
    con.executemany("INSERT INTO topic(name, sort) VALUES (?,?)",
                    [(t, i) for i, t in enumerate(TOPICS)])
    con.executemany("INSERT INTO dispute_tag(name) VALUES (?)",
                    [(t,) for t in DISPUTE_TAGS])
    region_id = {name: rid for rid, _, name, _, _ in REGIONS}
    topic_id = {t: i + 1 for i, t in enumerate(TOPICS)}
    tag_id = {t: i + 1 for i, t in enumerate(DISPUTE_TAGS)}

    # --- 法规与法条（data/seed/laws/*.json）---
    unverified_articles = 0
    for s in load_law_files():
        cur = con.execute(
            """INSERT INTO legal_source(title, doc_no, issuer, level, region_id,
               publish_date, effective_date, status, source_url, coverage)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (s["title"], s.get("doc_no"), s.get("issuer"), s["level"],
             region_id.get(s.get("region", "CN"), 1),
             s.get("publish_date"), s.get("effective_date"),
             s.get("status", "active"), s.get("source_url"),
             s.get("coverage", "full")))
        sid = cur.lastrowid
        for a in s["articles"]:
            verified = 1 if a.get("verified") else 0
            unverified_articles += 0 if verified else 1
            cur2 = con.execute(
                """INSERT INTO legal_article(source_id, article_no, clause_no, text, verified)
                   VALUES (?,?,?,?,?)""",
                (sid, a["article_no"], norm_clause(a.get("clause_no")),
                 a["text"], verified))
            for t in a.get("topics", []):
                if t in topic_id:
                    con.execute(
                        "INSERT OR IGNORE INTO article_topic(article_id, topic_id) VALUES (?,?)",
                        (cur2.lastrowid, topic_id[t]))

    # --- 地区参数 ---
    params = json.loads((SEED / "region_params.json").read_text(encoding="utf-8"))["params"]
    unverified_params = 0
    for p in params:
        unverified_params += 0 if p.get("verified") else 1
        con.execute(
            """INSERT INTO region_param(region_id, param_key, value, period, verified)
               VALUES (?,?,?,?,?)""",
            (region_id[p["region"]], p["param_key"],
             json.dumps(p["value"], ensure_ascii=False),
             p["period"], 1 if p.get("verified") else 0))

    # --- 词条（引用存在性校验：找不到即构建失败）---
    entries = json.loads((SEED / "entries.json").read_text(encoding="utf-8"))["entries"]
    for e in entries:
        cur = con.execute(
            """INSERT INTO entry(title, slug, topic_id, body, status, basis_date)
               VALUES (?,?,?,?,?,?)""",
            (e["title"], e["slug"], topic_id[e["topic"]],
             json.dumps(e["body"], ensure_ascii=False), e["status"], e.get("basis_date")))
        eid = cur.lastrowid
        for r in e["regions"]:
            con.execute("INSERT INTO entry_region(entry_id, region_id) VALUES (?,?)",
                        (eid, 1 if r == "CN" else region_id[r]))
        for c in e["citations"]:
            row = _resolve(con, c["source"], c["article_no"], c.get("clause_no"))
            if row is None:
                con.close()
                db_path.unlink(missing_ok=True)
                sys.exit(f"[构建失败] 词条《{e['title']}》引用校验不通过："
                         f"{c['source']} {c['article_no']} 款{c.get('clause_no') or '-'} 不在库内")
            con.execute("INSERT INTO entry_citation(entry_id, article_id) VALUES (?,?)",
                        (eid, row[0]))

    # --- 计算器引用校验（T0.2：与词条同等待遇，失败即构建失败）---
    from calculators import ALL_CALC_CITATIONS
    for ref in ALL_CALC_CITATIONS:
        if _resolve_ref_str(con, ref) is None:
            con.close()
            db_path.unlink(missing_ok=True)
            sys.exit(f"[构建失败] 计算器引用校验不通过：{ref} 不在库内")

    # --- 案例（引用容错：解析不出丢弃并告警）---
    cases_file = SEED / "cases.json"
    n_cases, unverified_cases, dropped_cites = 0, 0, 0
    if cases_file.exists():
        cases = json.loads(cases_file.read_text(encoding="utf-8"))["cases"]
        for c in cases:
            n_cases += 1
            unverified_cases += 0 if c.get("verified") else 1
            cur = con.execute(
                """INSERT INTO case_record(case_no, court, region_id, trial_level, cause,
                   facts_summary, gist, result, decided_date, source_channel,
                   license_note, anonymized, verified, file_key)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,1,?,?)""",
                (c.get("case_no"), c.get("court"),
                 region_id.get(c.get("region", "CN"), 1), c.get("trial_level"),
                 c.get("cause"), c.get("facts_summary"), c["gist"], c.get("result"),
                 c.get("decided_date"), c.get("source_channel", "official_release"),
                 (c.get("title", "") + "｜" + (c.get("license_note") or "")).strip("｜"),
                 1 if c.get("verified") else 0, None))
            cid = cur.lastrowid
            for t in c.get("tags", []):
                if t in tag_id:
                    con.execute("INSERT OR IGNORE INTO case_tag(case_id, tag_id) VALUES (?,?)",
                                (cid, tag_id[t]))
            for ct in c.get("citations", []):
                row = _resolve(con, ct["source"], ct["article_no"], ct.get("clause_no"))
                if row is None:
                    dropped_cites += 1
                    print(f"  ⚠ 案例引用丢弃：{c.get('title','?')} → "
                          f"{ct['source']}{ct['article_no']}", file=sys.stderr)
                else:
                    con.execute(
                        "INSERT OR IGNORE INTO case_citation(case_id, article_id) VALUES (?,?)",
                        (cid, row[0]))

    # --- FTS5 二元组索引（T2.1；M1 换中文分词 + sqlite-vec 向量）---
    fts_ok = True
    try:
        con.execute("CREATE VIRTUAL TABLE fts_article USING fts5(seg)")
        con.executemany("INSERT INTO fts_article(rowid, seg) VALUES (?,?)",
                        [(r[0], _bigram_seg(r[1])) for r in
                         con.execute("SELECT id, text FROM legal_article").fetchall()])
        con.execute("CREATE VIRTUAL TABLE fts_case USING fts5(seg)")
        con.executemany("INSERT INTO fts_case(rowid, seg) VALUES (?,?)",
                        [(r[0], _bigram_seg((r[1] or "") + (r[2] or ""))) for r in
                         con.execute("SELECT id, gist, facts_summary FROM case_record").fetchall()])
    except sqlite3.OperationalError:
        fts_ok = False

    con.execute("INSERT INTO meta(key, value) VALUES ('built_at', ?)", (now_iso(),))
    con.execute("INSERT INTO meta(key, value) VALUES ('schema_version', '0.2')")
    con.commit()

    n_src = con.execute("SELECT count(*) FROM legal_source").fetchone()[0]
    n_art = con.execute("SELECT count(*) FROM legal_article").fetchone()[0]
    n_entry = con.execute("SELECT count(*) FROM entry").fetchone()[0]
    n_param = con.execute("SELECT count(*) FROM region_param").fetchone()[0]
    n_local = con.execute("SELECT count(*) FROM legal_source WHERE region_id != 1").fetchone()[0]
    con.close()

    print(f"[knowledge.db] 构建完成：法规 {n_src} 部（地方 {n_local} 部）/ 法条 {n_art} 条 / "
          f"词条 {n_entry} 条 / 参数 {n_param} 项 / 案例 {n_cases} 件")
    print(f"  引用校验：词条+计算器全部通过；案例引用丢弃 {dropped_cites} 条；"
          f"FTS5 索引：{'已建' if fts_ok else '本机 SQLite 不支持，已跳过（检索自动降级）'}")
    if unverified_articles or unverified_params or unverified_cases:
        verified_art = n_art - unverified_articles
        pct = (verified_art / n_art * 100) if n_art else 0
        print(f"  ⚠ 待核验：法条 {unverified_articles} / 参数 {unverified_params} / "
              f"案例 {unverified_cases}（演示可用；生产发布门槛 = 0，见 PRD §10）")
        print(f"  法条核验覆盖率：{verified_art}/{n_art} = {pct:.1f}%"
              f"（明细 python3 src/verify_articles.py --status）")


def build_app() -> None:
    db_path = DB_DIR / "app.db"
    con = sqlite3.connect(db_path, timeout=5)
    con.execute("PRAGMA journal_mode=WAL")
    con.executescript((SCHEMA / "app.sql").read_text(encoding="utf-8"))
    # 幂等迁移：旧库补列
    try:
        con.execute("ALTER TABLE qa_session ADD COLUMN facts TEXT")
    except sqlite3.OperationalError:
        pass
    con.commit()
    con.close()
    print("[app.db] 就绪（WAL 模式，幂等初始化）")


if __name__ == "__main__":
    build_knowledge()
    build_app()
