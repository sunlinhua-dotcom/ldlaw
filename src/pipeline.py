"""可信问答管线（PRD §7 六道防线，LLM 接入版）。

LLM（DeepSeek）只做两件事：
1. 要素抽取与意图识别（结构化 JSON 输出）；
2. 开放问题的 RAG 生成——只许依据检索到的条文作答。

路由决策、金额计算、引用校验、数字溯源、拒答闸门全部在代码层。
LLM 不可用（断网 / 无 key / 超时）时自动降级为规则引擎，系统不瘫。
"""
from __future__ import annotations

import json
import re
import sqlite3
import sys
from datetime import date, datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from calculators import (CalcResult, annual_leave_payout, exit_prorated_unused_days,
                         severance, statutory_annual_days, unlawful_damages)
import llm

ROOT = Path(__file__).resolve().parent.parent
KDB = ROOT / "db" / "knowledge.db"
ADB = ROOT / "db" / "app.db"

DISCLAIMER = "内容仅供参考，不构成法律意见。"
REFUSE_CONCLUSION = ("这个问题超出当前知识库可靠回答的范围（依据不足或属于个案争议）。"
                     "为避免给出不准确的答案，建议转交合作律师处理。")
REGIONS = ["上海", "江苏", "浙江", "北京", "天津", "河北", "广东", "广州", "深圳"]


def kconn() -> sqlite3.Connection:
    return sqlite3.connect(f"file:{KDB}?mode=ro", uri=True)


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ============ 要素抽取 ============

EXTRACT_SYS = """你是企业劳动法咨询系统的要素抽取器。从用户问题中抽取结构化要素，只输出 JSON。
字段：
- intent: "severance"（经济补偿/协商解除/裁员补偿的金额测算）| "unlawful_damages"（违法解除赔偿金/2N 测算）| "annual_leave"（年假天数或未休年假折算测算）| "concept"（规则/概念解释类提问，不要求算钱）| "other"
- region: 用工所在地，只能取 ["上海","江苏","浙江","北京","天津","河北","广东","广州","深圳"] 之一，否则 null
- monthly_wage: 月薪数字（元），没有则 null
- hire_date: "YYYY-MM-DD"，只说到年月则取当月 1 日，没有则 null
- term_date: "YYYY-MM-DD"，没有则 null
- cumulative_years: 累计工龄（年，数字），没有则 null
- taken_days: 今年已休年假天数，没有则 null
规则：不确定一律 null，禁止编造或推测。只输出 JSON。"""


def _regex_facts(text: str) -> dict:
    facts: dict = {}
    for name in REGIONS:
        if name in text:
            facts["region"] = name
            break
    m = re.search(r"月薪[约为是]?\s*([\d,，]+)", text) or re.search(r"([\d,，]{4,})\s*元", text)
    if m:
        facts["monthly_wage"] = float(m.group(1).replace(",", "").replace("，", ""))
    dates = re.findall(r"(\d{4})\s*年\s*(\d{1,2})\s*月(?:\s*(\d{1,2})\s*日)?", text)
    if dates:
        y, mo, d = dates[0]
        facts["hire_date"] = f"{y}-{int(mo):02d}-{int(d) if d else 1:02d}"
        if len(dates) > 1:
            y2, m2, d2 = dates[1]
            facts["term_date"] = f"{y2}-{int(m2):02d}-{int(d2) if d2 else 1:02d}"
    m = re.search(r"(?:工龄|工作了?)\s*(\d+)\s*年", text)
    if m:
        facts["cumulative_years"] = float(m.group(1))
    if re.search(r"(违法解除|赔偿金|2\s*N)", text, re.I):
        facts["intent"] = "unlawful_damages"
    elif re.search(r"(协商解除|经济补偿|补偿|裁员)", text):
        facts["intent"] = "severance"
    elif re.search(r"(年假|年休假)", text):
        facts["intent"] = "annual_leave"
    else:
        facts["intent"] = "other"
    return facts


def extract_facts(question: str) -> tuple[dict, bool]:
    """返回 (facts, llm_used)。LLM 失败时回落正则。"""
    if llm.available():
        try:
            raw = llm.chat_json(
                [{"role": "system", "content": EXTRACT_SYS},
                 {"role": "user", "content": question}],
                max_tokens=300)
            facts = {k: v for k, v in raw.items() if v is not None}
            if facts.get("region") not in REGIONS:
                facts.pop("region", None)
            if facts.get("intent") not in ("severance", "unlawful_damages",
                                           "annual_leave", "concept", "other"):
                facts["intent"] = "other"
            return facts, True
        except Exception:
            pass
    return _regex_facts(question), False


def _parse_date(s: str | None) -> date | None:
    if not s:
        return None
    try:
        return date.fromisoformat(str(s))
    except ValueError:
        return None


# ============ 引用解析（库内取原文，取不到即丢弃） ============

def resolve_citations(kc: sqlite3.Connection, refs: list[str]) -> list[dict]:
    out, seen = [], set()
    for ref in refs:
        m = re.match(r"《(.+?)》(第.+?条)(?:第(.+?)款)?$", ref.strip())
        if not m or ref in seen:
            continue
        seen.add(ref)
        title, art, clause = m.group(1), m.group(2), m.group(3)
        row = kc.execute(
            """SELECT ls.title, la.article_no, la.clause_no, la.text, la.verified
               FROM legal_article la JOIN legal_source ls ON ls.id = la.source_id
               WHERE ls.title = ? AND la.article_no = ?
                 AND (? IS NULL OR la.clause_no = ?)""",
            (title, art, clause, clause)).fetchone()
        if row:
            out.append({"source": row[0], "article": row[1], "clause": row[2],
                        "text": row[3], "verified": bool(row[4])})
    return out


def fetch_param(kc: sqlite3.Connection, region_name: str, key: str):
    row = kc.execute(
        """SELECT rp.value, rp.period, rp.verified FROM region_param rp
           JOIN region r ON r.id = rp.region_id
           WHERE r.name = ? AND rp.param_key = ?
           ORDER BY rp.period DESC LIMIT 1""", (region_name, key)).fetchone()
    if not row:
        return None
    return {"value": json.loads(row[0]), "period": row[1], "verified": bool(row[2])}


# ============ 检索（地区过滤 + 二元组重叠打分） ============

def _bigrams(s: str) -> list[str]:
    cjk = re.sub(r"[^一-鿿]", "", s)
    return [cjk[i:i + 2] for i in range(len(cjk) - 1)]


def retrieve(kc: sqlite3.Connection, question: str, region_name: str | None,
             k: int = 4) -> list[dict]:
    chain = [1]
    if region_name:
        row = kc.execute("SELECT id, parent_id FROM region WHERE name = ?",
                         (region_name,)).fetchone()
        if row:
            chain.append(row[0])
            if row[1] and row[1] != 1:
                chain.append(row[1])
    qmarks = ",".join("?" * len(chain))
    rows = kc.execute(
        f"""SELECT ls.title, la.article_no, la.clause_no, la.text, la.verified
            FROM legal_article la JOIN legal_source ls ON ls.id = la.source_id
            WHERE ls.region_id IN ({qmarks}) AND la.status = 'active'""",
        chain).fetchall()
    grams = set(_bigrams(question))
    scored = []
    for title, art, clause, text, verified in rows:
        score = sum(1 for g in grams if g in text)
        if score > 0:
            scored.append({"source": title, "article": art, "clause": clause,
                           "text": text, "verified": bool(verified), "score": score})
    scored.sort(key=lambda x: -x["score"])
    return scored[:k]


# ============ RAG 生成 + 双重校验 ============

RAG_SYS = """你是面向企业 HR 的劳动法助手。严格遵守：
1. 只能依据下面用户提供的【条文】回答，禁止使用条文之外的任何知识、经验或记忆；
2. 只输出 JSON：{"refuse": false, "conclusion": "一句话结论", "analysis": "简要分析（150 字内）", "citations": ["《法规名》第X条", ...]}
3. citations 只能引用提供的条文，写法必须与提供的《法规名》第X条完全一致；
4. 条文不足以可靠回答时，输出 {"refuse": true}，不要勉强作答；
5. 不得出现条文中没有的数字或金额。"""

_CJK_DIG = {"零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4,
            "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}


def _cjk_to_int(s: str) -> int:
    total, num = 0, 0
    for ch in s:
        if ch in _CJK_DIG:
            num = _CJK_DIG[ch]
        elif ch == "十":
            total += (num or 1) * 10
            num = 0
        elif ch == "百":
            total += (num or 1) * 100
            num = 0
        elif ch == "千":
            total += (num or 1) * 1000
            num = 0
    return total + num


def _allowed_numbers(texts: list[str]) -> set[str]:
    allowed: set[str] = set()
    for t in texts:
        for m in re.findall(r"\d+(?:\.\d+)?", t):
            allowed.add(m)
        for m in re.findall(r"[零一二两三四五六七八九十百千]+", t):
            v = _cjk_to_int(m)
            if v:
                allowed.add(str(v))
    return allowed


def rag_answer(kc: sqlite3.Connection, question: str,
               region_name: str | None) -> dict | None:
    """成功返回 {conclusion, analysis, citations(resolved)}；任何一道校验不过返回 None。"""
    ctx = retrieve(kc, question, region_name)
    if not ctx or ctx[0]["score"] < 2:
        return None  # 防线 5：检索置信度不足，不送生成
    ctx_block = "\n\n".join(
        f"【条文 {i + 1}】《{c['source']}》{c['article']}\n{c['text']}"
        for i, c in enumerate(ctx))
    try:
        out = llm.chat_json(
            [{"role": "system", "content": RAG_SYS},
             {"role": "user", "content": f"问题：{question}\n\n可用条文：\n{ctx_block}"}],
            max_tokens=600)
    except Exception:
        return None
    if out.get("refuse") or not out.get("conclusion"):
        return None
    # 防线 3a：引用必须落在提供的条文集合内
    provided = {(c["source"], c["article"]) for c in ctx}
    valid_refs = []
    for ref in out.get("citations", []):
        m = re.match(r"《(.+?)》(第.+?条)", str(ref).strip())
        if m and (m.group(1), m.group(2)) in provided:
            valid_refs.append(f"《{m.group(1)}》{m.group(2)}")
    if not valid_refs:
        return None
    # 防线 3b：数字溯源——回答里的数字必须来自条文或问题本身
    answer_text = (out["conclusion"] + out.get("analysis", "")).replace(",", "")
    allowed = _allowed_numbers([c["text"] for c in ctx] + [question])
    for num in re.findall(r"\d+(?:\.\d+)?", answer_text):
        if num not in allowed:
            return None
    resolved = resolve_citations(kc, valid_refs)
    if not resolved:
        return None
    return {"conclusion": out["conclusion"], "analysis": out.get("analysis", ""),
            "citations": resolved}


# ============ 词条 ============

def entry_by_slug(kc: sqlite3.Connection, slug: str) -> dict | None:
    row = kc.execute("SELECT id, title, body, status, basis_date FROM entry WHERE slug = ?",
                     (slug,)).fetchone()
    if not row:
        return None
    eid, title, body_json, status, basis = row
    cites = kc.execute(
        """SELECT ls.title, la.article_no, la.clause_no, la.text, la.verified
           FROM entry_citation ec
           JOIN legal_article la ON la.id = ec.article_id
           JOIN legal_source ls ON ls.id = la.source_id
           WHERE ec.entry_id = ?""", (eid,)).fetchall()
    return {"id": eid, "title": title, "body": json.loads(body_json),
            "status": status, "basis_date": basis,
            "citations": [{"source": r[0], "article": r[1], "clause": r[2],
                           "text": r[3], "verified": bool(r[4])} for r in cites]}


def match_entry(kc: sqlite3.Connection, question: str) -> str | None:
    if re.search(r"N\s*\+\s*1", question, re.I):
        return "consensual-termination-n-plus-one"
    if re.search(r"(没休完|未休|休不完).{0,8}(年假|年休假)|（?年假）?.{0,6}折算", question) \
            and re.search(r"年假|年休假", question):
        return "annual-leave-payout-on-exit"
    best, best_score = None, 0
    for slug, title in kc.execute("SELECT slug, title FROM entry WHERE status != 'archived'"):
        score = sum(1 for g in set(_bigrams(question)) if g in title)
        if score > best_score:
            best, best_score = slug, score
    return best if best_score >= 3 else None


# ============ 主路由 ============

def answer_structured(question: str, default_region: str | None = None) -> dict:
    kc = kconn()
    facts, llm_used = extract_facts(question)
    if "region" not in facts and default_region in REGIONS:
        facts["region"] = default_region
        facts["region_defaulted"] = True
    region = facts.get("region")
    res: dict = {"route": "refuse", "llm_used": llm_used, "conclusion": REFUSE_CONCLUSION,
                 "steps": [], "amount": None, "analysis": None, "citations": [],
                 "region": region, "warnings": [], "clarify": [], "entry": None,
                 "escalate": False}
    intent = facts.get("intent", "other")

    def finish() -> dict:
        if facts.get("region_defaulted") and res["route"] in ("calculator", "rag"):
            res["warnings"].append(f"地区取自页面默认设置（{region}），请确认实际用工所在地")
        _log(question, facts, res)
        kc.close()
        return res

    # —— 计算类 ——
    if intent in ("severance", "unlawful_damages"):
        need = []
        if not region:
            need.append("用工所在城市（各地社平封顶口径不同）")
        if not facts.get("monthly_wage"):
            need.append("离职前 12 个月平均应发月工资")
        if not _parse_date(facts.get("hire_date")):
            need.append("入职年月")
        if need:
            res.update(route="clarify", clarify=need,
                       conclusion="为了算得准，请先补充以下要素：")
            return finish()
        hire = _parse_date(facts["hire_date"])
        term = _parse_date(facts.get("term_date")) or date.today()
        p = fetch_param(kc, region, "social_avg_wage_monthly")
        social = p["value"]["amount"] if p else None
        note = "" if (p and p["verified"]) else ("（⚠ 社平为演示占位值，待核验）" if p else "")
        if intent == "unlawful_damages":
            calc = unlawful_damages(hire, term, float(facts["monthly_wage"]), social)
            label = "违法解除赔偿金 2N"
        else:
            calc = severance(hire, term, float(facts["monthly_wage"]), social, note)
            label = "经济补偿 N"
        res.update(route="calculator", amount=calc.amount, steps=calc.steps,
                   warnings=calc.warnings + res["warnings"],
                   citations=resolve_citations(kc, calc.citations),
                   conclusion=f"按现有要素测算，应支付 {calc.amount:,.2f} 元（{label}）。")
        res["calculator_key"] = calc.key
        return finish()

    if intent == "annual_leave":
        need = []
        if not facts.get("monthly_wage"):
            need.append("月工资")
        if facts.get("cumulative_years") is None:
            need.append("累计工龄（含此前单位年限）")
        if need:
            res.update(route="clarify", clarify=need,
                       conclusion="为了算得准，请先补充以下要素：")
            return finish()
        years = float(facts["cumulative_years"])
        taken = float(facts.get("taken_days") or 0)
        term = _parse_date(facts.get("term_date")) or date.today()
        annual = statutory_annual_days(years)
        passed = (term - date(term.year, 1, 1)).days + 1
        unused = exit_prorated_unused_days(passed, annual, taken)
        calc = annual_leave_payout(float(facts["monthly_wage"]), unused)
        calc.steps.insert(0, f"累计工龄 {years:g} 年 → 全年应休 {annual} 天；"
                             f"按离职日折算已过 {passed} 天，已休 {taken:g} 天 → 应付未休 {unused} 天")
        if not facts.get("taken_days"):
            calc.warnings.append("今年已休天数按 0 计，如已休过年假请补充后重算")
        res.update(route="calculator", amount=calc.amount, steps=calc.steps,
                   warnings=calc.warnings + res["warnings"],
                   citations=resolve_citations(kc, calc.citations),
                   conclusion=f"离职折算未休年假 {unused} 天，应付 {calc.amount:,.2f} 元"
                              f"（300% 口径，其中含正常工资部分）。")
        res["calculator_key"] = calc.key
        return finish()

    # —— 概念 / 开放问题：词条优先，RAG 兜底 ——
    slug = match_entry(kc, question)
    if slug:
        e = entry_by_slug(kc, slug)
        if e:
            warnings = list(e["body"].get("pitfalls", []))
            if e["status"] != "published":
                warnings.insert(0, f"本词条状态为「{e['status']}」，尚未完成律师审核（演示数据）")
            res.update(route="entry_hit", conclusion=e["body"]["conclusion"],
                       citations=e["citations"], warnings=warnings,
                       entry={"title": e["title"], "slug": slug,
                              "how_to": e["body"].get("how_to", [])})
            return finish()

    rag = rag_answer(kc, question, region) if llm.available() else None
    if rag:
        res.update(route="rag", conclusion=rag["conclusion"], analysis=rag["analysis"],
                   citations=rag["citations"],
                   warnings=["本回答由检索 + 生成产生，已通过引用存在性与数字溯源校验；"
                             "重要决策前建议人工复核或转律师确认"])
        return finish()

    res.update(route="refuse", escalate=True)
    return finish()


def _log(question: str, facts: dict, res: dict) -> None:
    try:
        ac = sqlite3.connect(ADB)
        cur = ac.execute("INSERT INTO qa_session(region_id, created_at) VALUES (NULL, ?)",
                         (now_iso(),))
        sid = cur.lastrowid
        ac.execute("INSERT INTO qa_message(session_id, role, content, created_at) "
                   "VALUES (?,?,?,?)", (sid, "user", question, now_iso()))
        ac.execute(
            """INSERT INTO qa_message(session_id, role, content, facts, route,
               calculator_key, citations, confidence, escalated, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (sid, "assistant", res["conclusion"],
             json.dumps({k: str(v) for k, v in facts.items()}, ensure_ascii=False),
             res["route"], res.get("calculator_key"),
             json.dumps([f"《{c['source']}》{c['article']}" for c in res["citations"]],
                        ensure_ascii=False),
             1.0 if res["route"] in ("entry_hit", "calculator") else
             (0.7 if res["route"] == "rag" else 0.0),
             1 if res["escalate"] else 0, now_iso()))
        ac.commit()
        ac.close()
    except Exception:
        pass  # 日志失败不影响回答


# ============ CLI 文本渲染 ============

def format_text(res: dict) -> str:
    lines = [f"[route={res['route']}  llm={'on' if res['llm_used'] else 'off'}]",
             f"【结论】{res['conclusion']}"]
    for c in res["clarify"]:
        lines.append(f"  · {c}")
    if res["steps"]:
        lines.append("【计算】")
        lines += [f"  · {s}" for s in res["steps"]]
    if res["analysis"]:
        lines.append(f"【分析】{res['analysis']}")
    if res["citations"]:
        lines.append("【依据】")
        for c in res["citations"]:
            flag = "" if c["verified"] else "（⚠ 待官方源核验）"
            lines.append(f"  · 《{c['source']}》{c['article']}{flag}")
    if res["warnings"]:
        lines.append("【风险提示】")
        lines += [f"  · {w}" for w in res["warnings"]]
    lines.append(f"—— {DISCLAIMER}")
    return "\n".join(lines)
