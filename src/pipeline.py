"""可信问答管线（PRD §7 六道防线，LLM 接入版）。

LLM（DeepSeek）只做两件事：
1. 要素抽取与意图识别（结构化 JSON 输出）；
2. 开放问题的 RAG 生成——只许依据检索到的条文与案例作答。

路由决策、金额计算、引用校验、数字溯源、案号校验、拒答闸门全部在代码层。
LLM 不可用（断网 / 无 key / 超时）时自动降级为规则引擎，系统不瘫。

多轮对话（T2.4）：同 session 内要素累积合并，clarify 后只补缺口即可继续。
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
from zhnum import cjk_to_int, norm_clause
import llm

ROOT = Path(__file__).resolve().parent.parent
KDB = ROOT / "db" / "knowledge.db"
ADB = ROOT / "db" / "app.db"

DISCLAIMER = "内容仅供参考，不构成法律意见。"
REFUSE_CONCLUSION = ("这个问题超出当前知识库可靠回答的范围（依据不足或属于个案争议）。"
                     "为避免给出不准确的答案，建议转交合作律师处理。")
PRE2008_CONCLUSION = ("员工入职早于 2008-01-01（劳动合同法施行日），经济补偿需分段计算，"
                      "各地口径差异大（上海等地有特殊规则）。为避免算错，建议转交合作律师核算。")
REGIONS = ["上海", "江苏", "浙江", "北京", "天津", "河北", "广东",
           "广州", "深圳", "南京", "无锡", "常州", "苏州", "杭州", "宁波"]

_LOG_FAILURES = 0  # 日志落库失败计数（T0.5）
LOG_ENABLED = True  # 评测跑分（run_eval.py）置 False，避免评测流量污染 app.db


def kconn() -> sqlite3.Connection:
    return sqlite3.connect(f"file:{KDB}?mode=ro", uri=True)


def aconn() -> sqlite3.Connection:
    return sqlite3.connect(ADB, timeout=5)


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ============ 要素抽取 ============

EXTRACT_SYS = f"""你是企业劳动法咨询系统的要素抽取器。从用户问题中抽取结构化要素，只输出 JSON。
字段：
- intent: "severance"（经济补偿/协商解除/裁员补偿的金额测算）| "unlawful_damages"（违法解除赔偿金/2N 测算）| "annual_leave"（年假天数或未休年假折算测算）| "concept"（规则/概念解释类提问，不要求算钱）| "other"
- region: 用工所在地，只能取 {json.dumps(REGIONS, ensure_ascii=False)} 之一，否则 null
- monthly_wage: 月薪数字（元），没有则 null
- hire_date: "YYYY-MM-DD"，只说到年月则取当月 1 日，没有则 null
- term_date: "YYYY-MM-DD"，没有则 null
- cumulative_years: 累计工龄（年，数字），没有则 null
- taken_days: 今年已休年假天数，没有则 null
规则：不确定一律 null，禁止编造或推测。只输出 JSON。"""

_NUM_FIELDS = ("monthly_wage", "cumulative_years", "taken_days")


def _to_float(v) -> float | None:
    """LLM/用户输入的数值清洗（T0.4）：'15,000'、'1.5万'、'一万五' → 15000.0。"""
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip().replace(",", "").replace("，", "").replace("元", "").replace("￥", "")
    if not s:
        return None
    m = re.fullmatch(r"(\d+(?:\.\d+)?)\s*万", s)
    if m:
        return float(m.group(1)) * 10000
    try:
        return float(s)
    except ValueError:
        pass
    # 中文数字：'一万五' / '八千五' 口语尾数补位
    m = re.fullmatch(r"([零一二两三四五六七八九十百千]+)万([零一二两三四五六七八九])?", s)
    if m:
        base = cjk_to_int(m.group(1)) * 10000
        if m.group(2):
            base += cjk_to_int(m.group(2)) * 1000
        return float(base)
    n = cjk_to_int(s)
    return float(n) if n else None


def _regex_facts(text: str) -> dict:
    facts: dict = {}
    for name in REGIONS:
        if name in text:
            facts["region"] = name
            break
    m = re.search(r"月薪[约为是]?\s*([\d,，]+)", text) or re.search(r"([\d,，]{4,})\s*元", text)
    if m:
        facts["monthly_wage"] = _to_float(m.group(1))
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
    """返回 (facts, llm_used)。LLM 失败/类型异常时回落正则（T0.4）。"""
    if llm.available():
        try:
            raw = llm.chat_json(
                [{"role": "system", "content": EXTRACT_SYS},
                 {"role": "user", "content": question}],
                max_tokens=300)
            facts = {k: v for k, v in raw.items() if v is not None}
            for f in _NUM_FIELDS:
                if f in facts:
                    cleaned = _to_float(facts[f])
                    if cleaned is None:
                        facts.pop(f)
                    else:
                        facts[f] = cleaned
            if facts.get("region") not in REGIONS:
                facts.pop("region", None)
            if facts.get("intent") not in ("severance", "unlawful_damages",
                                           "annual_leave", "concept", "other"):
                facts["intent"] = "other"
            return facts, True
        except Exception:
            pass
    return _regex_facts(question), False


def merge_facts(stored: dict, new: dict) -> dict:
    """多轮要素合并：新值覆盖旧值；intent 为 other 时沿用历史 intent。"""
    merged = dict(stored)
    for k, v in new.items():
        if v is None:
            continue
        if k == "intent" and v == "other" and stored.get("intent") not in (None, "other"):
            continue
        merged[k] = v
    return merged


def _parse_date(s: str | None) -> date | None:
    if not s:
        return None
    try:
        return date.fromisoformat(str(s))
    except ValueError:
        return None


# ============ 引用解析（库内取原文；解析不出 → 告警，不再静默丢弃 T0.2）============

def resolve_citations(kc: sqlite3.Connection, refs: list[str]) -> tuple[list[dict], list[str]]:
    """返回 (resolved, unresolved)。"""
    out, unresolved, seen = [], [], set()
    for ref in refs:
        ref = str(ref).strip()
        if ref in seen:
            continue
        seen.add(ref)
        m = re.match(r"《(.+?)》(第.+?条)(?:第(.+?)款)?$", ref)
        if not m:
            unresolved.append(ref)
            continue
        title, art = m.group(1), m.group(2)
        clause = norm_clause(m.group(3))
        row = kc.execute(
            """SELECT ls.title, la.article_no, la.clause_no, la.text, la.verified,
                      r.name AS region
               FROM legal_article la
               JOIN legal_source ls ON ls.id = la.source_id
               JOIN region r ON r.id = ls.region_id
               WHERE ls.title = ? AND la.article_no = ?
                 AND (? IS NULL OR la.clause_no = ?)""",
            (title, art, clause, clause)).fetchone()
        if row:
            out.append({"source": row[0], "article": row[1], "clause": row[2],
                        "text": row[3], "verified": bool(row[4]), "region": row[5]})
        else:
            unresolved.append(ref)
    if unresolved:
        print(f"[citations] 未解析引用：{unresolved}", file=sys.stderr)
    return out, unresolved


def fetch_param(kc: sqlite3.Connection, region_name: str, key: str) -> dict | None:
    """市级优先、省级回退（T1.5）。返回含 region_used 的参数 dict。"""
    name = region_name
    for _ in range(3):  # 市 → 省 → 全国（最多三级）
        row = kc.execute(
            """SELECT rp.value, rp.period, rp.verified, r.name FROM region_param rp
               JOIN region r ON r.id = rp.region_id
               WHERE r.name = ? AND rp.param_key = ?
               ORDER BY rp.period DESC LIMIT 1""", (name, key)).fetchone()
        if row:
            return {"value": json.loads(row[0]), "period": row[1],
                    "verified": bool(row[2]), "region_used": row[3],
                    "fallback": row[3] != region_name}
        parent = kc.execute(
            """SELECT p.name FROM region r JOIN region p ON p.id = r.parent_id
               WHERE r.name = ?""", (name,)).fetchone()
        if not parent or parent[0] == "全国":
            return None
        name = parent[0]
    return None


# ============ 检索（FTS5 二元组召回 + 重叠精排 + 地区过滤；T2.1）============

def _bigrams(s: str) -> list[str]:
    cjk = re.sub(r"[^一-鿿]", "", s)
    return [cjk[i:i + 2] for i in range(len(cjk) - 1)]


def _region_chain(kc: sqlite3.Connection, region_name: str | None) -> list[int]:
    chain = [1]
    if region_name:
        row = kc.execute("SELECT id, parent_id FROM region WHERE name = ?",
                         (region_name,)).fetchone()
        if row:
            chain.append(row[0])
            if row[1] and row[1] != 1:
                chain.append(row[1])
    return chain


def _fts_candidates(kc, table: str, question: str, limit: int = 50) -> list[int] | None:
    grams = list(dict.fromkeys(_bigrams(question)))[:24]
    if not grams:
        return None
    query = " OR ".join(f'"{g}"' for g in grams)
    try:
        rows = kc.execute(
            f"SELECT rowid FROM {table} WHERE seg MATCH ? ORDER BY rank LIMIT ?",
            (query, limit)).fetchall()
        return [r[0] for r in rows]
    except sqlite3.OperationalError:
        return None  # FTS 不可用 → 调用方降级全表


def retrieve(kc: sqlite3.Connection, question: str, region_name: str | None,
             k: int = 4) -> list[dict]:
    chain = _region_chain(kc, region_name)
    qmarks = ",".join("?" * len(chain))
    ids = _fts_candidates(kc, "fts_article", question)
    where_ids = f"AND la.id IN ({','.join(map(str, ids))})" if ids else ""
    rows = kc.execute(
        f"""SELECT ls.title, la.article_no, la.clause_no, la.text, la.verified,
                   r.name AS region
            FROM legal_article la
            JOIN legal_source ls ON ls.id = la.source_id
            JOIN region r ON r.id = ls.region_id
            WHERE ls.region_id IN ({qmarks}) AND la.status = 'active' {where_ids}""",
        chain).fetchall()
    grams = set(_bigrams(question))
    scored = []
    for title, art, clause, text, verified, region in rows:
        score = sum(1 for g in grams if g in text)
        if score > 0:
            scored.append({"source": title, "article": art, "clause": clause,
                           "text": text, "verified": bool(verified),
                           "region": region, "score": score})
    scored.sort(key=lambda x: -x["score"])
    return scored[:k]


def retrieve_cases(kc: sqlite3.Connection, question: str, region_name: str | None,
                   k: int = 2) -> list[dict]:
    """相似案例检索（T2.5）：gist+案情二元组匹配，地区链过滤。"""
    chain = _region_chain(kc, region_name)
    qmarks = ",".join("?" * len(chain))
    ids = _fts_candidates(kc, "fts_case", question, limit=30)
    where_ids = f"AND c.id IN ({','.join(map(str, ids))})" if ids else ""
    try:
        rows = kc.execute(
            f"""SELECT c.id, c.case_no, c.court, c.gist, c.facts_summary, c.result,
                       c.license_note, c.verified, r.name
                FROM case_record c JOIN region r ON r.id = c.region_id
                WHERE c.region_id IN ({qmarks}) {where_ids}""", chain).fetchall()
    except sqlite3.OperationalError:
        return []
    grams = set(_bigrams(question))
    scored = []
    for cid, case_no, court, gist, facts, result, note, verified, region in rows:
        text = (gist or "") + (facts or "")
        score = sum(1 for g in grams if g in text)
        if score >= 2:
            title, _, source_note = (note or "").partition("｜")
            scored.append({"id": cid, "case_no": case_no, "court": court,
                           "title": title or "（未命名案例）", "gist": gist,
                           "facts_summary": facts, "result": result,
                           "source_note": source_note, "verified": bool(verified),
                           "region": region, "score": score})
    scored.sort(key=lambda x: -x["score"])
    return scored[:k]


# ============ RAG 生成 + 多重校验 ============

RAG_SYS = """你是面向企业 HR 的劳动法助手。严格遵守：
1. 只能依据下面提供的【条文】和【案例】回答，禁止使用其外的任何知识、经验或记忆；
2. 只输出 JSON：{"refuse": false, "conclusion": "一句话结论", "analysis": "简要分析（180 字内，先讲法定规则，如引用案例再讲裁判倾向）", "citations": ["《法规名》第X条", ...], "case_refs": [1]}
3. citations 只能引用提供的条文，写法必须与提供的《法规名》第X条完全一致；case_refs 为引用的案例编号数组（如 [1,2]），没引用就 []；
4. 案例只能用于说明裁判倾向，不得当作法律规定本身；提及案例时用"参考案例N"，禁止编造案号；
5. 条文不足以可靠回答时，输出 {"refuse": true}，不要勉强作答；
6. 不得出现条文、案例或问题中没有的数字或金额。"""


def _allowed_numbers(texts: list[str]) -> set[str]:
    allowed: set[str] = set()
    for t in texts:
        for m in re.findall(r"\d+(?:\.\d+)?", t):
            allowed.add(m)
        for m in re.findall(r"[零一二两三四五六七八九十百千]+", t):
            v = cjk_to_int(m)
            if v:
                allowed.add(str(v))
    return allowed


def rag_answer(kc: sqlite3.Connection, question: str,
               region_name: str | None) -> dict | None:
    """成功返回 {conclusion, analysis, citations(resolved), cases}；任一道校验不过返回 None。"""
    ctx = retrieve(kc, question, region_name)
    if not ctx or ctx[0]["score"] < 2:
        return None  # 防线 5：检索置信度不足，不送生成
    cases = retrieve_cases(kc, question, region_name)
    ctx_block = "\n\n".join(
        f"【条文 {i + 1}】《{c['source']}》{c['article']}"
        f"{'第' + c['clause'] + '款' if c['clause'] else ''}\n{c['text']}"
        for i, c in enumerate(ctx))
    if cases:
        ctx_block += "\n\n" + "\n\n".join(
            f"【案例 {i + 1}】{c['title']}（{c['source_note'] or '官方发布'}）\n"
            f"裁判要旨：{c['gist']}"
            for i, c in enumerate(cases))
    try:
        out = llm.chat_json(
            [{"role": "system", "content": RAG_SYS},
             {"role": "user", "content": f"问题：{question}\n\n可用材料：\n{ctx_block}"}],
            max_tokens=700)
        if not isinstance(out, dict):
            return None  # T0.4：类型防御
        if out.get("refuse") or not out.get("conclusion"):
            return None
        raw_cites = out.get("citations") or []
        if not isinstance(raw_cites, list):
            return None
        # 防线 3a：引用必须落在提供的条文集合内（保留款号 T0.3）
        provided = {(c["source"], c["article"]) for c in ctx}
        valid_refs = []
        for ref in raw_cites:
            m = re.match(r"《(.+?)》(第.+?条)(第.+?款)?", str(ref).strip())
            if m and (m.group(1), m.group(2)) in provided:
                valid_refs.append(f"《{m.group(1)}》{m.group(2)}{m.group(3) or ''}")
        if not valid_refs:
            return None
        answer_text = (str(out["conclusion"]) + str(out.get("analysis", ""))).replace(",", "")
        # 防线 3b：数字溯源——回答里的数字必须来自条文/案例/问题
        allowed = _allowed_numbers([c["text"] for c in ctx]
                                   + [c["gist"] or "" for c in cases] + [question])
        for num in re.findall(r"\d+(?:\.\d+)?", answer_text):
            if num not in allowed:
                return None
        # 防线 3c：案号校验——答案中出现案号样式必须在提供的案例里（T2.5）
        provided_case_nos = {c["case_no"] for c in cases if c["case_no"]}
        for m in re.findall(r"[（(]\d{4}[）)][^，。；\s]{2,20}?号", answer_text):
            if m not in provided_case_nos:
                return None
        case_refs = out.get("case_refs") or []
        used_cases = [cases[i - 1] for i in case_refs
                      if isinstance(i, int) and 1 <= i <= len(cases)]
    except Exception:
        return None
    resolved, _ = resolve_citations(kc, valid_refs)
    if not resolved:
        return None
    return {"conclusion": out["conclusion"], "analysis": out.get("analysis", ""),
            "citations": resolved, "cases": used_cases}


# ============ 词条 ============

def entry_by_slug(kc: sqlite3.Connection, slug: str) -> dict | None:
    row = kc.execute("SELECT id, title, body, status, basis_date FROM entry WHERE slug = ?",
                     (slug,)).fetchone()
    if not row:
        return None
    eid, title, body_json, status, basis = row
    cites = kc.execute(
        """SELECT ls.title, la.article_no, la.clause_no, la.text, la.verified, r.name
           FROM entry_citation ec
           JOIN legal_article la ON la.id = ec.article_id
           JOIN legal_source ls ON ls.id = la.source_id
           JOIN region r ON r.id = ls.region_id
           WHERE ec.entry_id = ?""", (eid,)).fetchall()
    return {"id": eid, "title": title, "body": json.loads(body_json),
            "status": status, "basis_date": basis,
            "citations": [{"source": r[0], "article": r[1], "clause": r[2],
                           "text": r[3], "verified": bool(r[4]), "region": r[5]}
                          for r in cites]}


def match_entry(kc: sqlite3.Connection, question: str) -> str | None:
    if re.search(r"N\s*\+\s*1", question, re.I):
        return "consensual-termination-n-plus-one"
    best, best_score = None, 0
    grams = set(_bigrams(question))
    for slug, title, body in kc.execute(
            "SELECT slug, title, body FROM entry WHERE status != 'archived'"):
        kw = title + "".join(json.loads(body).get("keywords", []))
        score = sum(1 for g in grams if g in kw)
        if score > best_score:
            best, best_score = slug, score
    return best if best_score >= 3 else None


# ============ 会话（多轮要素累积 T2.4）============

def _session_facts(session_id: int | None) -> dict:
    if not session_id:
        return {}
    try:
        ac = aconn()
        row = ac.execute("SELECT facts FROM qa_session WHERE id = ?",
                         (session_id,)).fetchone()
        ac.close()
        return json.loads(row[0]) if row and row[0] else {}
    except Exception:
        return {}


def _ensure_session(session_id: int | None, region_id: int | None) -> int | None:
    try:
        ac = aconn()
        if session_id and ac.execute("SELECT 1 FROM qa_session WHERE id = ?",
                                     (session_id,)).fetchone():
            ac.close()
            return session_id
        cur = ac.execute("INSERT INTO qa_session(region_id, created_at) VALUES (?,?)",
                         (region_id, now_iso()))
        sid = cur.lastrowid
        ac.commit()
        ac.close()
        return sid
    except Exception:
        return None


# ============ 医疗期防线（病假/医疗期解除的硬规则闸门；计划外新增，无对应任务卡）============

MEDICAL_RE = re.compile(r"(医疗期|病假|患病|生病|住院|动手术|做了?手术|手术后?|癌|肿瘤|"
                        r"脑积水|尿毒症|重病|绝症|非因工负伤|精神病|化疗|透析)")
FIRE_RE = re.compile(r"(开除|辞退|解雇|炒(?:掉|了)|单方解除|劝退|fire)", re.I)


def medical_period_months(total_years: float, unit_years: float) -> int:
    """劳部发〔1994〕479号第三条：按总工龄与本单位工龄确定医疗期（月）。"""
    if total_years < 10:
        return 3 if unit_years < 5 else 6
    if unit_years < 5:
        return 6
    if unit_years < 10:
        return 9
    if unit_years < 15:
        return 12
    if unit_years < 20:
        return 18
    return 24


def _medical_guard(kc, question: str, facts: dict, hire: date, term: date,
                   res: dict, intent: str) -> None:
    """病假/患病语境下的解除测算：强制补足医疗期依据、给出合规路径、转律师。

    医疗语境跨轮持久化（facts['medical_context']）：多轮补要素时第二轮问题
    往往只有数字没有病情描述，不能只看当前文本。
    """
    if not (facts.get("medical_context") or MEDICAL_RE.search(question)):
        return
    unit_years = (term - hire).days / 365.25
    total = facts.get("cumulative_years")
    assumed = total is None
    mp = medical_period_months(unit_years if assumed else float(total), unit_years)
    extra_refs = ["《中华人民共和国劳动合同法》第四十二条",
                  "《中华人民共和国劳动合同法》第四十条",
                  "《企业职工患病或非因工负伤医疗期规定》第三条",
                  "《企业职工患病或非因工负伤医疗期规定》第四条"]
    cites, _ = resolve_citations(kc, extra_refs)
    seen = {(c["source"], c["article"], c.get("clause")) for c in res["citations"]}
    res["citations"] += [c for c in cites
                         if (c["source"], c["article"], c.get("clause")) not in seen]
    base = "按总工龄≈本单位工龄估算" if assumed else f"按总工龄 {total:g} 年"
    warns = [
        f"患病/病假语境：{base}、本单位约 {unit_years:.1f} 年 → 法定医疗期 {mp} 个月"
        f"（劳部发〔1994〕479号第三条；病休按第四条规定的周期内累计计算）",
        "医疗期内不得依《劳动合同法》第四十条、第四十一条单方解除（第四十二条）；"
        "此时强行解除属违法解除，按第八十七条支付 2N 赔偿金",
        "合规路径：① 协商一致解除（第三十六条，N，金额可谈）；"
        "② 医疗期满后不能从事原工作也不能从事另行安排工作的，依第四十条第一项解除"
        "（N + 1 个月代通知金；需先履行劳动能力鉴定 / 调岗安排等前置程序）",
    ]
    if res.get("region") == "上海":
        warns.append("上海口径：医疗期满解除的，另需支付不低于 6 个月工资的医疗补助费"
                     "（重病 / 绝症还需上浮；该地方依据库内暂缺，请律师核验）")
    if intent == "severance" and FIRE_RE.search(question) and res.get("amount"):
        warns.append(f"注意：当前金额为协商解除口径 N；若在医疗期内被认定单方违法解除，"
                     f"风险敞口为 2N ≈ {res['amount'] * 2:,.2f} 元")
    res["warnings"] += warns
    res["escalate"] = True


# ============ 文书起草（AIGC，引用走同一套校验）============

def _F(key: str, label: str, type_: str = "text", ph: str = "", req: bool = False) -> dict:
    return {"key": key, "label": label, "type": type_, "ph": ph, "req": req}


DOC_TYPES: dict[str, dict] = {
    "mutual_termination": {
        "title": "协商解除劳动合同协议书",
        "desc": "双方协商一致解除，约定补偿与交接",
        "query": "协商一致解除劳动合同 经济补偿 工作交接",
        "guide": ("依据《劳动合同法》第三十六条协商一致解除。写明：解除日期、经济补偿金额与"
                  "支付时间、工资结算、社保公积金停缴结转、工作交接、保密义务延续、"
                  "双方再无其他劳动争议条款。"),
        "fields": [
            _F("company", "公司名称", req=True),
            _F("employee", "员工姓名", req=True),
            _F("position", "岗位"),
            _F("hire_date", "入职日期", "date"),
            _F("term_date", "协商离职日期", "date", req=True),
            _F("compensation", "经济补偿金额（元）", "number"),
            _F("note", "其他约定 / 情况说明", "textarea", "如：年假已休完、有竞业限制约定…"),
        ],
    },
    "dismissal_notice": {
        "title": "解除劳动合同通知书",
        "desc": "单方解除（过失性 / 无过失性）",
        "query": "用人单位 解除劳动合同 通知 工会 提前三十日 严重违反规章制度",
        "guide": ("单方解除务必写明事实与法律依据（对应《劳动合同法》第三十九 / 四十条的具体"
                  "情形）、解除日期、工资与经济补偿结算、工作交接、离职证明开具。"
                  "提示用人单位应事先将解除理由通知工会。"),
        "risk": "单方解除是劳动争议高发区：解除事由与程序瑕疵都可能被认定违法解除（2N）。"
                "发出前必须由律师确认事由充分、证据固定、程序完备",
        "fields": [
            _F("company", "公司名称", req=True),
            _F("employee", "员工姓名", req=True),
            _F("position", "岗位"),
            _F("basis", "解除事由与依据", "textarea",
               "如：连续旷工 5 个工作日，违反员工手册第X条…", True),
            _F("term_date", "解除日期", "date", req=True),
            _F("note", "交接与结算安排", "textarea"),
        ],
    },
    "warning_letter": {
        "title": "违纪警告处分通知书",
        "desc": "违纪行为的书面警告与整改要求",
        "query": "严重违反 规章制度 劳动纪律 处分",
        "guide": ("写明：违纪事实（时间地点行为）、违反的制度条款名称与编号、处分决定、"
                  "整改要求与期限、再犯后果、员工申辩权利与签收栏。"),
        "fields": [
            _F("company", "公司名称", req=True),
            _F("employee", "员工姓名", req=True),
            _F("department", "部门"),
            _F("fact", "违纪事实", "textarea", "时间、地点、具体行为、证据…", True),
            _F("rule", "违反的制度条款", "text", "如：《员工手册》第 5.2 条"),
            _F("demand", "整改要求", "textarea"),
        ],
    },
    "return_to_work": {
        "title": "催告返岗通知书",
        "desc": "旷工 / 失联员工的限期返岗催告",
        "query": "旷工 严重违反规章制度 解除劳动合同",
        "guide": ("写明：旷工起始日期与天数、催告返岗期限、需提交的说明材料、"
                  "逾期不返岗将按制度认定为旷工并可能解除劳动合同的后果、送达方式。"),
        "fields": [
            _F("company", "公司名称", req=True),
            _F("employee", "员工姓名", req=True),
            _F("absent_from", "旷工起始日期", "date", req=True),
            _F("deadline", "限期返岗日期", "date", req=True),
            _F("note", "补充说明", "textarea", "联系方式、已尝试的联系记录…"),
        ],
    },
    "transfer_notice": {
        "title": "调岗通知书",
        "desc": "工作岗位调整的书面通知",
        "query": "变更劳动合同 协商一致 调整工作岗位",
        "guide": ("调岗原则上需协商一致（《劳动合同法》第三十五条）。写明：原岗位、新岗位、"
                  "调整理由（合理性依据）、生效日期、薪酬是否变化、异议反馈渠道与期限。"),
        "risk": "单方调岗的合理性（经营必要、薪酬不降、不具侮辱性、不增加显著通勤负担）"
                "是争议焦点，建议先与员工协商并保留记录",
        "fields": [
            _F("company", "公司名称", req=True),
            _F("employee", "员工姓名", req=True),
            _F("old_position", "原岗位", req=True),
            _F("new_position", "新岗位", req=True),
            _F("reason", "调岗理由", "textarea", "组织架构调整 / 岗位撤销 / 身体原因…", True),
            _F("effective", "生效日期", "date"),
            _F("salary", "薪酬变化说明", "text", "如：薪酬标准不变"),
        ],
    },
    "probation_fail": {
        "title": "试用期不符合录用条件通知书",
        "desc": "试用期解除（第三十九条第一项）",
        "query": "试用期 不符合录用条件 解除劳动合同",
        "guide": ("务必写明：录用条件是什么（已书面告知）、考核过程与结果、"
                  "不符合录用条件的具体事实、解除日期与结算交接。"
                  "解除必须在试用期届满前作出并送达。"),
        "risk": "「录用条件」未事先书面明示、考核证据不足、超过试用期才通知，"
                "都会导致解除被认定违法。试用期解除同样需要事由与证据",
        "fields": [
            _F("company", "公司名称", req=True),
            _F("employee", "员工姓名", req=True),
            _F("position", "岗位", req=True),
            _F("hire_date", "入职日期", "date"),
            _F("probation_end", "试用期截止日期", "date", req=True),
            _F("fact", "考核情况与不符合录用条件的事实", "textarea", "", True),
        ],
    },
}

DRAFT_SYS = """你是企业 HR 劳动法文书起草助手。根据文书类型、事实要素和提供的【条文】起草规范文书。严格遵守：
1. 只输出 JSON：{"document": "文书全文", "citations": ["《法规名》第X条", ...]}
2. 文书格式：第一行为标题；正文条理分明（需要时用"一、二、三"分条）；结尾落款留公司名称与日期；
3. 事实要素缺失处用【待填写：说明】占位，禁止编造任何事实、日期、金额或证据；
4. 文中如引用法律条文，必须出自提供的【条文】，写法与《法规名》第X条完全一致；citations 列出全部引用；
5. 语言正式克制，不使用威胁性、侮辱性表述；
6. 若该类文书有法律风险前提（如单方解除需事由充分），在文末以"操作提示："另起一段列出 2–3 条要点。"""


def doc_type_list() -> list[dict]:
    return [{"key": k, "title": v["title"], "desc": v["desc"], "fields": v["fields"]}
            for k, v in DOC_TYPES.items()]


def draft_document(doc_type: str, fields: dict, region_name: str | None) -> dict:
    dt = DOC_TYPES.get(doc_type)
    if not dt:
        raise ValueError(f"未知文书类型：{doc_type}")
    if not llm.available():
        raise RuntimeError("未配置 DeepSeek API，文书起草不可用（问答与计算器不受影响）")
    kc = kconn()
    try:
        note = " ".join(str(fields.get(k) or "") for k in ("note", "basis", "fact", "reason"))
        ctx = retrieve(kc, dt["query"] + " " + note, region_name, k=5)
        ctx_block = "\n\n".join(
            f"【条文 {i + 1}】《{c['source']}》{c['article']}"
            f"{'第' + c['clause'] + '款' if c['clause'] else ''}\n{c['text']}"
            for i, c in enumerate(ctx)) or "（无）"
        labels = {f["key"]: f["label"] for f in dt["fields"]}
        fact_lines = "\n".join(f"- {labels.get(k, k)}：{v}" for k, v in fields.items()
                               if str(v or "").strip())
        out = llm.chat_json(
            [{"role": "system", "content": DRAFT_SYS},
             {"role": "user", "content":
              f"文书类型：{dt['title']}\n起草要点：{dt['guide']}\n\n"
              f"事实要素：\n{fact_lines or '（未提供，全部用占位符）'}\n\n"
              f"可用条文：\n{ctx_block}"}],
            max_tokens=1800, temperature=0.3, timeout=90)
        doc = str(out.get("document") or "").strip()
        if not doc:
            raise RuntimeError("生成失败：模型未返回文书内容，请重试")
        # 引用校验：citations 必须落在检索集内，原文从库内取
        provided = {(c["source"], c["article"]) for c in ctx}
        valid_refs = []
        for ref in (out.get("citations") or []):
            m = re.match(r"《(.+?)》(第.+?条)(第.+?款)?", str(ref).strip())
            if m and (m.group(1), m.group(2)) in provided:
                valid_refs.append(f"《{m.group(1)}》{m.group(2)}{m.group(3) or ''}")
        resolved, _ = resolve_citations(kc, valid_refs)
        # 正文内联引用扫描：库内核验不到的逐条提示人工确认
        unchecked = sorted({f"《{m.group(1)}》{m.group(2)}" for m in
                            re.finditer(r"《(.+?)》(第[一二三四五六七八九十百零\d]+条)", doc)
                            if (m.group(1), m.group(2)) not in provided})
        warnings = [
            "本文书为 AI 生成初稿，必须经律师或法务审核后方可对外使用",
            "日期、金额、姓名等事实信息请逐项人工核对；【待填写】占位须补全后再用",
        ]
        if unchecked:
            warnings.append("文中下列条文引用未能在知识库内核验，请人工确认："
                            + "、".join(unchecked))
        if dt.get("risk"):
            warnings.append(dt["risk"])
        return {"title": dt["title"], "document": doc, "citations": resolved,
                "warnings": warnings, "llm_used": True}
    finally:
        kc.close()


# ============ AI 律师审核（文书发出前合规审查）============
# 审核清单依据：库内法条 + 实务通行标准（工会程序为必经程序、"视为自动离职"无
# 法律依据、送达层级：直接送达 → 同住成年亲属 → EMS 留痕 → 公告兜底）。

GENERAL_RUBRIC = [
    "事实表述具体可验证（时间、地点、行为、数据），无情绪化或威胁性措辞",
    "全部日期、金额、姓名与事实要素一致，文中不得出现来源不明的数字",
    "占位符【待填写】必须全部补全后才能发出",
    "落款（公司全称）、日期、员工签收栏完整；一式两份，签收留存",
    "送达可留痕：优先直接送达本人签收；拒收时同住成年亲属签收或 EMS 邮寄"
    "（面单注明文件名称）并保留回执；穷尽上述方式才可公告送达",
]

REVIEW_RULES: dict[str, dict] = {
    "mutual_termination": {
        "query": "协商一致 解除 经济补偿 支付",
        "points": [
            "明确写明依据《劳动合同法》第三十六条协商一致解除，而非单方解除",
            "补偿金额、支付时间、支付方式、个税代扣口径明确",
            "工资结算至离职日；未休年假折算、报销、奖金是否了结写明",
            "社保公积金缴至具体月份明确",
            "包含「双方再无其他劳动争议」一次性了结条款",
            "如有竞业限制约定：启动还是豁免必须写明，不写明离职后仍可能产生补偿义务",
        ],
    },
    "dismissal_notice": {
        "query": "解除 通知工会 不得解除 提前三十日 经济补偿",
        "points": [
            "解除事由落到第三十九/四十条的具体某一项，事实与该项构成要件对应",
            "依第四十条解除的：提前三十日书面通知或支付一个月代通知金，并附经济补偿",
            "排查第四十二条禁止情形：医疗期内、三期女职工、工伤停工留薪期、"
            "连续工作满十五年且距退休不足五年——任一命中即不得依四十/四十一条解除",
            "解除理由已事先通知工会（第四十三条）；未建工会的向当地总工会履行"
            "变通告知并留痕，否则程序违法（必经程序，起诉前可补正）",
            "所依据的规章制度经民主程序制定并已公示告知（第四条），证据已固定",
        ],
    },
    "warning_letter": {
        "query": "规章制度 劳动纪律 民主程序 公示",
        "points": [
            "违纪事实具体（时间地点行为证据），不用「屡次」「恶劣」等空泛定性替代事实",
            "写明违反的制度名称与具体条款编号，该制度经民主程序制定并已公示",
            "处分与违纪程度相当（过罚相当），首次轻微违纪慎用「严重违纪」定性",
            "给员工申辩/申诉渠道与期限",
            "预留员工签收栏；拒签时注明见证人并拍照留痕",
        ],
    },
    "return_to_work": {
        "query": "旷工 严重违反规章制度 解除",
        "points": [
            "旷工起始日期、累计天数表述准确",
            "返岗期限合理（一般不少于 3 个工作日）",
            "要求限期提交未到岗的书面说明与证明材料",
            "后果只能写「将依规章制度认定旷工并可能依第三十九条解除」，"
            "绝不能写「视为自动离职」——自动离职不是法定解除类型，按此处理"
            "大概率被认定违法解除",
            "多渠道送达并留痕（EMS 面单注明「催告返岗通知书」字样）",
        ],
    },
    "transfer_notice": {
        "query": "变更劳动合同 协商一致 工作岗位",
        "points": [
            "调岗原则需协商一致（第三十五条）；单方调岗必须写明经营必要性等合理性理由",
            "新岗位薪酬是否变化必须明示；降薪调岗风险极高",
            "新岗位与员工技能体力匹配，无侮辱性惩罚性",
            "给员工异议反馈渠道与期限",
            "工作地点/通勤变化是否显著增加员工负担需评估并说明",
        ],
    },
    "probation_fail": {
        "query": "试用期 不符合录用条件 解除 试用期期限",
        "points": [
            "录用条件已在入职时书面明示并有签收证据，通知书中点明该文件名称",
            "考核事实具体，与录用条件逐项对应，不能只下「不符合录用条件」的结论",
            "必须在试用期届满前作出并送达，超期即丧失该解除依据",
            "试用期长短合法（第十九条），约定超限的试用期条款本身无效",
            "解除理由事先通知工会（第四十三条）同样适用于试用期解除",
        ],
    },
}

REVIEW_SYS = """你是劳动法执业律师，对 HR 即将发出的文书做发出前合规审核。只依据提供的【条文】、【审核清单】与中国劳动法通行实务判断，输出 JSON：
{"verdict": "pass|revise|block", "summary": "30 字内总评",
 "findings": [{"severity": "blocker|risk|polish", "point": "问题标题", "detail": "问题说明（涉及条文时写《法规名》第X条）", "fix": "具体修改建议"}],
 "checklist": [{"item": "清单项原文", "ok": true, "note": "核对结论（20 字内）"}]}
判级标准：blocker = 不改不能发出（违法、程序硬伤、关键事实缺失）；risk = 有败诉或争议风险，应当修改；polish = 表述优化建议。
规则：
1. checklist 必须逐项覆盖给出的全部审核清单；
2. 引用条文必须来自提供的【条文】，写法与《法规名》第X条一致，禁止凭记忆引用；
3. 事实判断只基于文书内容与事实要素，不臆测文书之外的情况；
4. 没有问题就判 pass，不要为了显得专业而虚构问题。"""


def review_document(doc_type: str, document: str, fields: dict,
                    region_name: str | None) -> dict:
    dt = DOC_TYPES.get(doc_type)
    if not dt:
        raise ValueError(f"未知文书类型：{doc_type}")
    if not str(document or "").strip():
        raise ValueError("document 不能为空")
    if not llm.available():
        raise RuntimeError("未配置 DeepSeek API，AI 审核不可用")
    rules = REVIEW_RULES.get(doc_type, {"query": dt["query"], "points": []})
    rubric = GENERAL_RUBRIC + rules["points"]

    # —— 规则层硬校验（不依赖 LLM，确定性）——
    rule_findings: list[dict] = []
    placeholders = re.findall(r"【待填写[：:][^】]*】", document)
    if placeholders:
        rule_findings.append({
            "severity": "blocker", "point": "存在未补全的占位符",
            "detail": "文中仍有：" + "、".join(dict.fromkeys(placeholders)),
            "fix": "逐项补全真实信息后才能发出", "_rule": "placeholder"})
    if doc_type in ("return_to_work", "dismissal_notice", "warning_letter") \
            and re.search(r"(视为自动离职|自动离职处理|视为离职|自行离职处理)", document):
        rule_findings.append({
            "severity": "blocker", "point": "「视为自动离职」无法律依据",
            "detail": "解除劳动合同只能由用人单位依法定情形作出并送达，「自动离职」"
                      "不是法定解除类型，按此表述处理大概率被认定违法解除（2N）",
            "fix": "删除该表述，改为「公司将依据规章制度并按《中华人民共和国劳动合同法》"
                   "第三十九条解除劳动合同」", "_rule": "auto_quit"})
    if MEDICAL_RE.search(json.dumps(fields, ensure_ascii=False) + document) \
            and doc_type in ("dismissal_notice", "probation_fail"):
        rule_findings.append({
            "severity": "blocker", "point": "涉及患病/医疗期员工的单方解除",
            "detail": "事实要素或文书中出现患病/病假语境：医疗期内禁止依第四十/四十一条"
                      "解除（第四十二条），需先核实医疗期是否届满",
            "fix": "核实医疗期（劳部发〔1994〕479号第三条）；未满则停发本通知，"
                   "改走协商或等期满后依法定程序处理", "_rule": "medical"})

    # —— LLM 律师复审 ——
    kc = kconn()
    try:
        ctx = retrieve(kc, rules["query"] + " " + dt["query"], region_name, k=6)
        ctx_block = "\n\n".join(
            f"【条文 {i + 1}】《{c['source']}》{c['article']}"
            f"{'第' + c['clause'] + '款' if c['clause'] else ''}\n{c['text']}"
            for i, c in enumerate(ctx)) or "（无）"
        labels = {f["key"]: f["label"] for f in dt["fields"]}
        fact_lines = "\n".join(f"- {labels.get(k, k)}：{v}" for k, v in fields.items()
                               if str(v or "").strip()) or "（未提供）"
        rubric_block = "\n".join(f"{i + 1}. {r}" for i, r in enumerate(rubric))
        out = llm.chat_json(
            [{"role": "system", "content": REVIEW_SYS},
             {"role": "user", "content":
              f"文书类型：{dt['title']}\n\n事实要素：\n{fact_lines}\n\n"
              f"审核清单：\n{rubric_block}\n\n可用条文：\n{ctx_block}\n\n"
              f"待审文书全文：\n{document}"}],
            max_tokens=2200, temperature=0.2, timeout=90)
        llm_findings = [f for f in (out.get("findings") or [])
                        if isinstance(f, dict) and f.get("point")]
        for f in llm_findings:
            if f.get("severity") not in ("blocker", "risk", "polish"):
                f["severity"] = "risk"
        checklist = [c for c in (out.get("checklist") or [])
                     if isinstance(c, dict) and c.get("item")]
        verdict = out.get("verdict")
        if verdict not in ("pass", "revise", "block"):
            verdict = "revise"
        findings = rule_findings + llm_findings
        # 最终判定（代码层兜底，规则硬伤优先于 LLM 结论）
        if any(f.get("_rule") in ("auto_quit", "medical") for f in findings):
            verdict = "block"
        elif any(f["severity"] == "blocker" for f in findings) and verdict == "pass":
            verdict = "revise"
        for f in findings:
            f.pop("_rule", None)
        # 审核意见中的引用 → 库内解析展示
        refs = sorted({f"《{m.group(1)}》{m.group(2)}" for f in findings for m in
                       re.finditer(r"《(.+?)》(第[一二三四五六七八九十百零\d]+条)",
                                   str(f.get("detail", "")) + str(f.get("fix", "")))})
        resolved, _ = resolve_citations(kc, refs)
        return {"verdict": verdict,
                "summary": str(out.get("summary") or "").strip(),
                "findings": findings, "checklist": checklist,
                "citations": resolved, "llm_used": True,
                "disclaimer": "AI 审核为算法辅助意见，不构成法律意见；"
                              "重大事项发出前仍建议执业律师人工复核"}
    finally:
        kc.close()


# ============ 主路由 ============

def answer_structured(question: str, default_region: str | None = None,
                      session_id: int | None = None) -> dict:
    kc = kconn()
    try:
        return _answer(kc, question, default_region, session_id)
    finally:
        kc.close()


def _region_id(kc, name: str | None) -> int | None:
    if not name:
        return None
    row = kc.execute("SELECT id FROM region WHERE name = ?", (name,)).fetchone()
    return row[0] if row else None


def _answer(kc, question: str, default_region: str | None,
            session_id: int | None) -> dict:
    new_facts, llm_used = extract_facts(question)
    if MEDICAL_RE.search(question):
        new_facts["medical_context"] = True  # 跨轮持久化（T2.6）
    if FIRE_RE.search(question):
        new_facts["fire_context"] = True  # 单方解除语境同样跨轮持久化
    stored = _session_facts(session_id)
    facts = merge_facts(stored, new_facts)
    if "region" not in facts and default_region in REGIONS:
        facts["region"] = default_region
        facts["region_defaulted"] = True
    region = facts.get("region")
    sid = _ensure_session(session_id, _region_id(kc, region))

    res: dict = {"route": "refuse", "llm_used": llm_used, "conclusion": REFUSE_CONCLUSION,
                 "steps": [], "amount": None, "analysis": None, "citations": [],
                 "cases": [], "region": region, "warnings": [], "clarify": [],
                 "entry": None, "escalate": False, "session_id": sid}
    intent = facts.get("intent", "other")
    # intent 兜底纠偏（规则层不信任 LLM 的路由判断）：
    # ① 明确"开除/辞退 + 问钱"却被判 other/concept → 回到测算路由；
    # ② 医疗期语境 + 单方解除语境（含跨轮）→ 违法解除，按 2N 测风险敞口。
    fire = bool(facts.get("fire_context") or FIRE_RE.search(question))
    money_ask = bool(re.search(r"(多少钱|给多少|怎么[赔补]|补偿|赔偿|测算|算一下)", question))
    if intent in ("other", "concept") and fire and money_ask:
        intent = "severance"
    if fire and facts.get("medical_context") and intent in ("severance", "other", "concept"):
        intent = "unlawful_damages"
    facts["intent"] = intent

    def finish() -> dict:
        if facts.get("region_defaulted") and res["route"] in ("calculator", "rag"):
            res["warnings"].append(f"地区取自页面默认设置（{region}），请确认实际用工所在地")
        # 地方依据适用性声明（T2.5）
        local = sorted({c["region"] for c in res["citations"] if c.get("region") not in (None, "全国")})
        if local:
            res["warnings"].append(f"本回答含地方性依据，适用地区：{'、'.join(local)}")
        _log(sid, question, facts, res)
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
        if hire < date(2008, 1, 1):  # T0.6：分段计算强制转律师
            res.update(route="refuse", escalate=True, conclusion=PRE2008_CONCLUSION)
            return finish()
        term = _parse_date(facts.get("term_date")) or date.today()
        p = fetch_param(kc, region, "social_avg_wage_monthly")
        social = p["value"]["amount"] if p else None
        note = ""
        if p:
            if not p["verified"]:
                note = f"（⚠ 社平为近似值待核验，口径：{p['region_used']} {p['period']}）"
            if p.get("fallback"):
                res["warnings"].append(
                    f"未配置 {region} 市级社平工资，封顶校验按 {p['region_used']} 省级口径，"
                    f"法定口径为设区市级，结果可能偏差")
        if intent == "unlawful_damages":
            calc = unlawful_damages(hire, term, float(facts["monthly_wage"]), social)
            label = "违法解除赔偿金 2N"
        else:
            calc = severance(hire, term, float(facts["monthly_wage"]), social, note)
            label = "经济补偿 N"
        cites, _ = resolve_citations(kc, calc.citations)
        res.update(route="calculator", amount=calc.amount, steps=calc.steps,
                   warnings=calc.warnings + res["warnings"], citations=cites,
                   conclusion=f"按现有要素测算，应支付 {calc.amount:,.2f} 元（{label}）。")
        res["calculator_key"] = calc.key
        _medical_guard(kc, question, facts, hire, term, res, intent)
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
        hire = _parse_date(facts.get("hire_date"))
        annual = statutory_annual_days(years)
        # T0.6：当年入职按"本单位已过日历天数"折算（实施办法第十二条）
        year_start = date(term.year, 1, 1)
        if hire and hire.year == term.year and hire > year_start:
            passed = (term - hire).days + 1
            base_note = f"自当年入职日 {hire.isoformat()} 起算"
        else:
            passed = (term - year_start).days + 1
            base_note = "按全年在职折算"
            if not hire:
                res["warnings"].append("未提供入职日期，按全年在职折算；如系当年入职请补充入职日期重算")
        unused = exit_prorated_unused_days(passed, annual, taken)
        calc = annual_leave_payout(float(facts["monthly_wage"]), unused)
        calc.steps.insert(0, f"累计工龄 {years:g} 年 → 全年应休 {annual} 天；"
                             f"{base_note}已过 {passed} 天，已休 {taken:g} 天 → 应付未休 {unused} 天")
        if not facts.get("taken_days"):
            calc.warnings.append("今年已休天数按 0 计，如已休过年假请补充后重算")
        cites, _ = resolve_citations(kc, calc.citations)
        res.update(route="calculator", amount=calc.amount, steps=calc.steps,
                   warnings=calc.warnings + res["warnings"], citations=cites,
                   conclusion=f"离职折算未休年假 {unused} 天，企业额外应补 {calc.amount:,.2f} 元"
                              f"（200% 口径；含正常工资的 300% 总额见计算过程）。")
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
                   citations=rag["citations"], cases=rag["cases"],
                   warnings=["本回答由检索 + 生成产生，已通过引用存在性、数字溯源与案号校验；"
                             "重要决策前建议人工复核或转律师确认"])
        return finish()

    res.update(route="refuse", escalate=True)
    return finish()


def _log(session_id: int | None, question: str, facts: dict, res: dict) -> None:
    global _LOG_FAILURES
    if not LOG_ENABLED:
        return
    try:
        ac = aconn()
        sid = session_id
        if sid is None:
            cur = ac.execute("INSERT INTO qa_session(region_id, created_at) VALUES (NULL,?)",
                             (now_iso(),))
            sid = cur.lastrowid
            res["session_id"] = sid
        # 要素累积持久化（T2.4）+ 地区回填（T0.5）
        rid = None
        row = None
        if res.get("region"):
            kc = kconn()
            row = kc.execute("SELECT id FROM region WHERE name = ?",
                             (res["region"],)).fetchone()
            kc.close()
            rid = row[0] if row else None
        ac.execute("UPDATE qa_session SET facts = ?, region_id = ? WHERE id = ?",
                   (json.dumps({k: v for k, v in facts.items() if v is not None},
                               ensure_ascii=False, default=str), rid, sid))
        ac.execute("INSERT INTO qa_message(session_id, role, content, created_at) "
                   "VALUES (?,?,?,?)", (sid, "user", question, now_iso()))
        hit_entry = None
        if res.get("entry"):
            kc = kconn()
            r2 = kc.execute("SELECT id FROM entry WHERE slug = ?",
                            (res["entry"]["slug"],)).fetchone()
            kc.close()
            hit_entry = r2[0] if r2 else None
        ac.execute(
            """INSERT INTO qa_message(session_id, role, content, facts, route,
               hit_entry_id, calculator_key, citations, confidence, escalated, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (sid, "assistant", res["conclusion"],
             json.dumps({k: str(v) for k, v in facts.items()}, ensure_ascii=False),
             res["route"], hit_entry, res.get("calculator_key"),
             json.dumps([f"《{c['source']}》{c['article']}" for c in res["citations"]],
                        ensure_ascii=False),
             1.0 if res["route"] in ("entry_hit", "calculator") else
             (0.7 if res["route"] == "rag" else 0.0),
             1 if res["escalate"] else 0, now_iso()))
        ac.commit()
        ac.close()
    except Exception as exc:
        _LOG_FAILURES += 1
        print(f"[log] 落库失败 #{_LOG_FAILURES}: {exc}", file=sys.stderr)


# ============ CLI 文本渲染 ============

def format_text(res: dict) -> str:
    lines = [f"[route={res['route']}  llm={'on' if res['llm_used'] else 'off'}"
             f"  session={res.get('session_id')}]",
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
            clause = f"第{c['clause']}款" if c.get("clause") else ""
            lines.append(f"  · 《{c['source']}》{c['article']}{clause}{flag}")
    for c in res.get("cases", []):
        lines.append(f"【参考案例】{c['title']}：{c['gist']}")
    if res["warnings"]:
        lines.append("【风险提示】")
        lines += [f"  · {w}" for w in res["warnings"]]
    lines.append(f"—— {DISCLAIMER}")
    return "\n".join(lines)
