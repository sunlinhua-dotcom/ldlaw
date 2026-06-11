"""LDLAWQ Web 服务（Python 标准库实现，零第三方依赖）。

用法：python3 src/server.py [端口，默认 8400]
浏览器打开 http://127.0.0.1:8400

API：
  POST /api/ask       {question, region}        → 结构化回答（六道防线管线）
  POST /api/calc      {type, ...}               → 计算器
  POST /api/escalate  {question, region}        → 生成转律师工单（演示）
  GET  /api/entries · /api/entries/<slug>       → 词条
  GET  /api/db/summary|sources|articles|params|regions → 知识库浏览（只读）
"""
from __future__ import annotations

import json
import sqlite3
import sys
from datetime import date, datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent))
import llm
import pipeline
from calculators import (annual_leave_payout, exit_prorated_unused_days,
                         severance, statutory_annual_days, unlawful_damages)

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "web"
KDB = ROOT / "db" / "knowledge.db"
ADB = ROOT / "db" / "app.db"


def ensure_db() -> None:
    if not KDB.exists() or not ADB.exists():
        import build_knowledge
        build_knowledge.build_knowledge()
        build_knowledge.build_app()


def kconn() -> sqlite3.Connection:
    con = sqlite3.connect(f"file:{KDB}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    return con


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------- API 实现 ----------

def api_db_summary() -> dict:
    kc = kconn()
    g = lambda sql: kc.execute(sql).fetchone()[0]
    out = {
        "sources": g("SELECT count(*) FROM legal_source"),
        "articles": g("SELECT count(*) FROM legal_article"),
        "articles_unverified": g("SELECT count(*) FROM legal_article WHERE verified=0"),
        "entries": g("SELECT count(*) FROM entry"),
        "params": g("SELECT count(*) FROM region_param"),
        "regions": g("SELECT count(*) FROM region WHERE level != 'country'"),
        "templates": g("SELECT count(*) FROM template"),
        "cases": g("SELECT count(*) FROM case_record"),
        "local_sources": g("SELECT count(*) FROM legal_source WHERE region_id != 1"),
        "built_at": (kc.execute("SELECT value FROM meta WHERE key='built_at'").fetchone()
                     or ["-"])[0],
        "llm": llm.model_name() if llm.available() else None,
        "db_files": {"knowledge": str(KDB), "app": str(ADB)},
    }
    ac = sqlite3.connect(ADB)
    out["qa_logged"] = ac.execute("SELECT count(*) FROM qa_message").fetchone()[0]
    out["referrals"] = ac.execute("SELECT count(*) FROM referral").fetchone()[0]
    ac.close()
    kc.close()
    return out


def api_db_sources() -> list:
    kc = kconn()
    rows = kc.execute(
        """SELECT ls.id, ls.title, ls.doc_no, ls.issuer, ls.level, r.name AS region,
                  ls.effective_date, ls.status, ls.source_url,
                  (SELECT count(*) FROM legal_article a WHERE a.source_id = ls.id) AS articles
           FROM legal_source ls JOIN region r ON r.id = ls.region_id
           ORDER BY ls.id""").fetchall()
    kc.close()
    return [dict(r) for r in rows]


def api_db_articles() -> list:
    kc = kconn()
    rows = kc.execute(
        """SELECT la.id, ls.title AS source, la.article_no, la.clause_no,
                  la.text, la.verified
           FROM legal_article la JOIN legal_source ls ON ls.id = la.source_id
           ORDER BY la.id""").fetchall()
    kc.close()
    return [dict(r) for r in rows]


def api_db_params() -> list:
    kc = kconn()
    rows = kc.execute(
        """SELECT rp.id, r.name AS region, rp.param_key, rp.value, rp.period, rp.verified
           FROM region_param rp JOIN region r ON r.id = rp.region_id
           ORDER BY rp.id""").fetchall()
    kc.close()
    out = []
    for r in rows:
        d = dict(r)
        d["value"] = json.loads(d["value"])
        out.append(d)
    return out


def api_db_regions() -> list:
    kc = kconn()
    rows = kc.execute(
        """SELECT r.id, r.code, r.name, r.level, p.name AS parent
           FROM region r LEFT JOIN region p ON p.id = r.parent_id
           ORDER BY r.id""").fetchall()
    kc.close()
    return [dict(r) for r in rows]


def api_entries() -> list:
    kc = kconn()
    rows = kc.execute(
        """SELECT e.slug, e.title, e.status, e.basis_date, t.name AS topic
           FROM entry e LEFT JOIN topic t ON t.id = e.topic_id
           WHERE e.status != 'archived' ORDER BY e.id""").fetchall()
    kc.close()
    return [dict(r) for r in rows]


def api_entry_detail(slug: str) -> dict | None:
    kc = sqlite3.connect(f"file:{KDB}?mode=ro", uri=True)
    e = pipeline.entry_by_slug(kc, slug)
    kc.close()
    return e


def api_calc(body: dict) -> dict:
    ctype = body.get("type")
    if ctype in ("severance", "unlawful"):
        hire = date.fromisoformat(body["hire_date"])
        term = date.fromisoformat(body.get("term_date") or date.today().isoformat())
        wage = float(body["monthly_wage"])
        kc = kconn()
        p = pipeline.fetch_param(kc, body.get("region", ""), "social_avg_wage_monthly")
        social = p["value"]["amount"] if p else None
        note = ""
        extra_warn = []
        if p:
            if not p["verified"]:
                note = f"（⚠ 社平为近似值待核验，口径：{p['region_used']} {p['period']}）"
            if p.get("fallback"):
                extra_warn.append(f"未配置市级社平，封顶按 {p['region_used']} 省级口径，"
                                  f"法定口径为设区市级，结果可能偏差")
        if ctype == "unlawful":
            calc = unlawful_damages(hire, term, wage, social)
        else:
            calc = severance(hire, term, wage, social, note)
        if hire < date(2008, 1, 1):
            extra_warn.append("入职早于 2008-01-01，依法需分段计算（本结果未分段），"
                              "请转律师核算后再使用")
        cites, _ = pipeline.resolve_citations(kc, calc.citations)
        kc.close()
        return {"amount": calc.amount, "steps": calc.steps,
                "citations": cites, "warnings": calc.warnings + extra_warn}
    if ctype == "annual":
        wage = float(body["monthly_wage"])
        years = float(body["cumulative_years"])
        taken = float(body.get("taken_days") or 0)
        term = date.fromisoformat(body.get("term_date") or date.today().isoformat())
        hire = date.fromisoformat(body["hire_date"]) if body.get("hire_date") else None
        annual = statutory_annual_days(years)
        year_start = date(term.year, 1, 1)
        if hire and hire.year == term.year and hire > year_start:
            passed = (term - hire).days + 1
            base_note = f"自当年入职日 {hire.isoformat()} 起算"
        else:
            passed = (term - year_start).days + 1
            base_note = "按全年在职折算"
        unused = exit_prorated_unused_days(passed, annual, taken)
        calc = annual_leave_payout(wage, unused)
        calc.steps.insert(0, f"累计工龄 {years:g} 年 → 全年应休 {annual} 天；"
                             f"{base_note}，截至 {term.isoformat()} 已过 {passed} 天，"
                             f"已休 {taken:g} 天 → 应付未休 {unused} 天")
        kc = kconn()
        cites, _ = pipeline.resolve_citations(kc, calc.citations)
        kc.close()
        return {"amount": calc.amount, "steps": calc.steps,
                "citations": cites, "warnings": calc.warnings, "unused_days": unused}
    raise ValueError(f"未知计算器类型：{ctype}")


def api_cases(query: dict) -> list:
    """案例库浏览（T3.3）：?tag=违法解除&region=上海 过滤。"""
    kc = kconn()
    sql = """SELECT DISTINCT c.id, c.case_no, c.court, c.gist, c.facts_summary,
                    c.result, c.license_note, c.verified, r.name AS region,
                    c.cause, c.trial_level
             FROM case_record c JOIN region r ON r.id = c.region_id"""
    args, wheres = [], []
    if query.get("tag"):
        sql += " JOIN case_tag ct ON ct.case_id = c.id JOIN dispute_tag dt ON dt.id = ct.tag_id"
        wheres.append("dt.name = ?")
        args.append(query["tag"])
    if query.get("region"):
        wheres.append("r.name IN (?, '全国')")
        args.append(query["region"])
    if wheres:
        sql += " WHERE " + " AND ".join(wheres)
    sql += " ORDER BY c.id"
    rows = kc.execute(sql, args).fetchall()
    out = []
    for row in rows:
        d = dict(zip(("id", "case_no", "court", "gist", "facts_summary", "result",
                      "license_note", "verified", "region", "cause", "trial_level"), row))
        title, _, note = (d.pop("license_note") or "").partition("｜")
        d["title"], d["source_note"] = title or "（未命名案例）", note
        d["tags"] = [t[0] for t in kc.execute(
            """SELECT dt.name FROM case_tag ct JOIN dispute_tag dt ON dt.id = ct.tag_id
               WHERE ct.case_id = ?""", (d["id"],)).fetchall()]
        d["citations"] = [{"source": c[0], "article": c[1], "clause": c[2],
                           "text": c[3], "verified": bool(c[4])}
                          for c in kc.execute(
                """SELECT ls.title, la.article_no, la.clause_no, la.text, la.verified
                   FROM case_citation cc
                   JOIN legal_article la ON la.id = cc.article_id
                   JOIN legal_source ls ON ls.id = la.source_id
                   WHERE cc.case_id = ?""", (d["id"],)).fetchall()]
        out.append(d)
    kc.close()
    return out


def api_escalate(body: dict) -> dict:
    ac = sqlite3.connect(ADB)
    brief = (body.get("question") or "")[:200]
    cur = ac.execute(
        """INSERT INTO referral(question_brief, consent_at, status, created_at)
           VALUES (?,?,?,?)""",
        (f"[{body.get('region', '-')}] {brief}", now_iso(), "pending", now_iso()))
    ac.commit()
    rid = cur.lastrowid
    ac.close()
    return {"referral_id": rid, "status": "pending",
            "message": "已生成咨询摘要并创建转介工单（演示），待匹配律师接单"}


# ---------- HTTP 处理 ----------

class Handler(BaseHTTPRequestHandler):
    server_version = "LDLAWQ/0.1"

    def log_message(self, fmt, *args):
        sys.stderr.write("[http] %s\n" % (fmt % args))

    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def _json(self, obj, code=200):
        data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self._cors()
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _html(self, path: Path):
        if not path.exists():
            self._json({"error": "not found"}, 404)
            return
        data = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.end_headers()

    def do_GET(self):
        p = urlparse(self.path).path
        try:
            if p in ("/", "/index.html"):
                return self._html(WEB / "index.html")
            if p == "/api/db/summary":
                return self._json(api_db_summary())
            if p == "/api/db/sources":
                return self._json(api_db_sources())
            if p == "/api/db/articles":
                return self._json(api_db_articles())
            if p == "/api/db/params":
                return self._json(api_db_params())
            if p == "/api/db/regions":
                return self._json(api_db_regions())
            if p == "/api/entries":
                return self._json(api_entries())
            if p == "/api/cases":
                from urllib.parse import parse_qs
                qs = parse_qs(urlparse(self.path).query)
                query = {k: v[0] for k, v in qs.items()}
                return self._json(api_cases(query))
            if p == "/api/doc-types":
                return self._json(pipeline.doc_type_list())
            if p.startswith("/api/entries/"):
                e = api_entry_detail(p.rsplit("/", 1)[1])
                return self._json(e if e else {"error": "not found"}, 200 if e else 404)
            return self._json({"error": "not found"}, 404)
        except Exception as exc:  # noqa: BLE001
            return self._json({"error": str(exc)}, 500)

    def do_POST(self):
        p = urlparse(self.path).path
        try:
            length = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(length) or b"{}")
            if p == "/api/ask":
                q = (body.get("question") or "").strip()
                if not q:
                    return self._json({"error": "question 不能为空"}, 400)
                res = pipeline.answer_structured(q, default_region=body.get("region"),
                                                 session_id=body.get("session_id"))
                return self._json(res)
            if p == "/api/calc":
                return self._json(api_calc(body))
            if p == "/api/draft":
                return self._json(pipeline.draft_document(
                    body.get("type", ""), body.get("fields") or {}, body.get("region")))
            if p == "/api/review":
                return self._json(pipeline.review_document(
                    body.get("type", ""), body.get("document", ""),
                    body.get("fields") or {}, body.get("region")))
            if p == "/api/escalate":
                return self._json(api_escalate(body))
            return self._json({"error": "not found"}, 404)
        except (KeyError, ValueError) as exc:
            return self._json({"error": f"参数错误：{exc}"}, 400)
        except Exception as exc:  # noqa: BLE001
            return self._json({"error": str(exc)}, 500)


def main() -> None:
    llm.load_env()
    ensure_db()
    import os
    port = int(sys.argv[1]) if len(sys.argv) > 1 else int(os.environ.get("PORT", 8400))
    # 绑 0.0.0.0 供局域网设备（手机/同事电脑）访问演示；公网部署需加鉴权
    srv = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    mode = f"DeepSeek（{llm.model_name()}）已接入" if llm.available() else "未配置 LLM，规则引擎模式"
    print(f"LDLAWQ demo 已启动：http://127.0.0.1:{port}   [{mode}]")
    try:
        import socket
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        print(f"  局域网访问：http://{s.getsockname()[0]}:{port}")
        s.close()
    except OSError:
        pass
    srv.serve_forever()


if __name__ == "__main__":
    main()
