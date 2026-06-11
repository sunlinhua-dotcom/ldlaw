"""构建 knowledge.db（整库重建）并初始化 app.db。

用法：python3 src/build_knowledge.py

发布纪律（与 PRD §7/§8 对应）：
- 词条引用做存在性校验：(法规标题, 条号, 款号) 在 legal_article 中找不到 → 构建失败；
- 未核验（verified=0）的条文与参数会在构建摘要中给出告警计数，生产发布要求为 0。
"""
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

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
]

TOPICS = [
    "招聘入职", "劳动合同", "工时与加班", "休息休假", "工资与福利",
    "社保公积金", "规章制度", "调岗调薪", "解除与终止", "经济补偿与赔偿",
    "竞业限制与保密", "女职工与三期", "工伤", "劳动争议",
]


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def build_knowledge() -> None:
    DB_DIR.mkdir(exist_ok=True)
    db_path = DB_DIR / "knowledge.db"
    if db_path.exists():
        db_path.unlink()
    con = sqlite3.connect(db_path)
    con.executescript((SCHEMA / "knowledge.sql").read_text(encoding="utf-8"))

    con.executemany(
        "INSERT INTO region(id, code, name, level, parent_id) VALUES (?,?,?,?,?)", REGIONS
    )
    con.executemany(
        "INSERT INTO topic(name, sort) VALUES (?,?)",
        [(t, i) for i, t in enumerate(TOPICS)],
    )
    region_id = {name: rid for rid, _, name, _, _ in REGIONS}
    topic_id = {t: i + 1 for i, t in enumerate(TOPICS)}

    # --- 法规与法条 ---
    sources = json.loads((SEED / "legal_sources.json").read_text(encoding="utf-8"))["sources"]
    unverified_articles = 0
    for s in sources:
        cur = con.execute(
            """INSERT INTO legal_source(title, doc_no, issuer, level, region_id,
               publish_date, effective_date, status, source_url)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (s["title"], s.get("doc_no"), s.get("issuer"), s["level"],
             region_id[s["region"]] if s["region"] != "CN" else 1,
             s.get("publish_date"), s.get("effective_date"),
             s.get("status", "active"), s.get("source_url")),
        )
        sid = cur.lastrowid
        for a in s["articles"]:
            verified = 1 if a.get("verified") else 0
            unverified_articles += 0 if verified else 1
            cur2 = con.execute(
                """INSERT INTO legal_article(source_id, article_no, clause_no, text, verified)
                   VALUES (?,?,?,?,?)""",
                (sid, a["article_no"], a.get("clause_no"), a["text"], verified),
            )
            for t in a.get("topics", []):
                con.execute(
                    "INSERT INTO article_topic(article_id, topic_id) VALUES (?,?)",
                    (cur2.lastrowid, topic_id[t]),
                )

    # --- 地区参数 ---
    params = json.loads((SEED / "region_params.json").read_text(encoding="utf-8"))["params"]
    unverified_params = 0
    for p in params:
        unverified_params += 0 if p.get("verified") else 1
        con.execute(
            """INSERT INTO region_param(region_id, param_key, value, period, verified)
               VALUES (?,?,?,?,?)""",
            (region_id[p["region"]], p["param_key"], json.dumps(p["value"], ensure_ascii=False),
             p["period"], 1 if p.get("verified") else 0),
        )

    # --- 词条（引用存在性校验：找不到即构建失败）---
    entries = json.loads((SEED / "entries.json").read_text(encoding="utf-8"))["entries"]
    for e in entries:
        cur = con.execute(
            """INSERT INTO entry(title, slug, topic_id, body, status, basis_date)
               VALUES (?,?,?,?,?,?)""",
            (e["title"], e["slug"], topic_id[e["topic"]],
             json.dumps(e["body"], ensure_ascii=False), e["status"], e.get("basis_date")),
        )
        eid = cur.lastrowid
        for r in e["regions"]:
            con.execute("INSERT INTO entry_region(entry_id, region_id) VALUES (?,?)",
                        (eid, 1 if r == "CN" else region_id[r]))
        for c in e["citations"]:
            row = con.execute(
                """SELECT la.id FROM legal_article la
                   JOIN legal_source ls ON ls.id = la.source_id
                   WHERE ls.title = ? AND la.article_no = ?
                     AND ifnull(la.clause_no,'') = ifnull(?, '')""",
                (c["source"], c["article_no"], c.get("clause_no")),
            ).fetchone()
            if row is None:
                con.close()
                db_path.unlink(missing_ok=True)
                sys.exit(
                    f"[构建失败] 词条《{e['title']}》引用校验不通过："
                    f"{c['source']} {c['article_no']} 款{c.get('clause_no') or '-'} 不在库内"
                )
            con.execute("INSERT INTO entry_citation(entry_id, article_id) VALUES (?,?)",
                        (eid, row[0]))

    # --- FTS5 全文索引（M0 用内置 tokenizer；正式版换中文分词，见 README）---
    fts_ok = True
    try:
        con.execute(
            "CREATE VIRTUAL TABLE fts_article USING fts5(text, content='legal_article', content_rowid='id')"
        )
        con.execute("INSERT INTO fts_article(rowid, text) SELECT id, text FROM legal_article")
    except sqlite3.OperationalError:
        fts_ok = False

    con.execute("INSERT INTO meta(key, value) VALUES ('built_at', ?)", (now_iso(),))
    con.execute("INSERT INTO meta(key, value) VALUES ('schema_version', '0.1')")
    con.commit()

    n_src = con.execute("SELECT count(*) FROM legal_source").fetchone()[0]
    n_art = con.execute("SELECT count(*) FROM legal_article").fetchone()[0]
    n_entry = con.execute("SELECT count(*) FROM entry").fetchone()[0]
    n_param = con.execute("SELECT count(*) FROM region_param").fetchone()[0]
    con.close()

    print(f"[knowledge.db] 构建完成：法规 {n_src} 部 / 法条 {n_art} 条 / 词条 {n_entry} 条 / 地区参数 {n_param} 项")
    print(f"  引用校验：全部通过；FTS5 索引：{'已建' if fts_ok else '本机 SQLite 不支持，已跳过'}")
    if unverified_articles or unverified_params:
        print(f"  ⚠ 告警：未核验法条 {unverified_articles} 条、未核验参数 {unverified_params} 项"
              f"（演示可用；生产发布门槛 = 0）")


def build_app() -> None:
    db_path = DB_DIR / "app.db"
    con = sqlite3.connect(db_path)
    con.execute("PRAGMA journal_mode=WAL")
    con.executescript((SCHEMA / "app.sql").read_text(encoding="utf-8"))
    con.commit()
    con.close()
    print(f"[app.db] 就绪（WAL 模式，幂等初始化）")


if __name__ == "__main__":
    build_knowledge()
    build_app()
