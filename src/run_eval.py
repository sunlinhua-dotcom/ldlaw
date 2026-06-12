"""评测跑分器（T2.7）。

逐题过 answer_structured，算四个硬指标：
  ① 编造引用率   answer 里引用不在 knowledge.db = 编造（PRD 红线，门槛 = 0）
  ② 拒答恰当率   should_refuse 题应 refuse；可答题不应 refuse（clarify 算可答）
  ③ 引用完整率   gold_citations 命中率（按 法规名+条号）
  ④ 结论正确率   计算题金额匹配 / 拒答题路由匹配 / 概念题= 不编造且非误拒
                （律师金标准的语义判分留 T2.6 → reasoner judge，本起步集用机器可判口径）

起步评测集（data/eval/eval_v1.jsonl）的金标准全部机器可判（计算器金额、应拒答路由、
法条存在性），非 LLM 自说自话；regex 可解析要素，无 LLM 也能确定性跑。

用法：
  python3 src/run_eval.py                      # 跑 data/eval/eval_v1.jsonl
  python3 src/run_eval.py data/eval/xxx.jsonl  # 指定评测集
退出码：编造引用 > 0 → 非 0（CI/preship 红线）。
"""
from __future__ import annotations

import json
import re
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pipeline  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
EVAL_DIR = ROOT / "data" / "eval"
KDB = ROOT / "db" / "knowledge.db"

# M0 门槛（PLAN §4.3）
THRESHOLDS = {"fabricated_rate": 0.0, "conclusion": 0.85, "refuse": 0.80, "citation": 0.75}


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _resolve(kc, ref: str) -> bool:
    """'《法规》第X条[第Y款]' 是否在 knowledge.db 内（编造判定）。"""
    m = re.match(r"《(.+?)》(第.+?条)(?:第(.+?)款)?$", ref.strip())
    if not m:
        return False
    src, art, clause = m.group(1), m.group(2), m.group(3)
    row = kc.execute(
        """SELECT 1 FROM legal_article la JOIN legal_source ls ON ls.id = la.source_id
           WHERE ls.title = ? AND la.article_no = ?""", (src, art)).fetchone()
    return row is not None


def _cite_key(source: str, article: str) -> str:
    return f"《{source}》{article}"


def evaluate(items: list[dict]) -> dict:
    pipeline.LOG_ENABLED = False  # 评测流量不落 app.db
    kc = sqlite3.connect(KDB)
    rows, fabricated_total, cite_total = [], 0, 0
    refuse_ok = concl_ok = cite_hit = 0
    refuse_n = concl_n = cite_n = 0

    for it in items:
        res = pipeline.answer_structured(it["question"], it.get("region") or None)
        route = res["route"]
        cites = [_cite_key(c["source"], c["article"]) for c in res.get("citations", [])]

        # ① 编造引用
        fab = [c for c in cites if not _resolve(kc, c)]
        fabricated_total += len(fab)
        cite_total += len(cites)

        # ② 拒答恰当（clarify 视为"未误拒"）
        want_refuse = bool(it.get("should_refuse"))
        got_refuse = route == "refuse"
        rf_ok = (got_refuse == want_refuse)
        refuse_ok += rf_ok
        refuse_n += 1

        # ③ 引用完整率（有金标准引用的题才计）
        gold_c = it.get("gold_citations") or []
        item_cite_hit = None
        if gold_c:
            present = sum(any(g.split("》")[0] in c and g.split("》")[1] in c
                              for c in cites) for g in gold_c)
            cite_hit += present
            cite_n += len(gold_c)
            item_cite_hit = f"{present}/{len(gold_c)}"

        # ④ 结论正确
        if "gold_amount" in it:
            cc = res.get("amount") is not None and abs(res["amount"] - it["gold_amount"]) < 1
        elif it.get("gold_route"):
            cc = route == it["gold_route"]
        elif want_refuse:
            cc = got_refuse and (res.get("escalate") if it.get("gold_escalate") else True)
        else:
            cc = (not got_refuse) and not fab  # 可答概念题：不误拒且不编造
        concl_ok += cc
        concl_n += 1

        rows.append({"id": it["id"], "cat": it.get("category", ""), "route": route,
                     "amount": res.get("amount"), "refuse_ok": rf_ok, "concl_ok": cc,
                     "fab": fab, "cite_hit": item_cite_hit, "escalate": res.get("escalate")})
    kc.close()

    m = {
        "fabricated_rate": (fabricated_total / cite_total) if cite_total else 0.0,
        "fabricated_count": fabricated_total,
        "refuse": refuse_ok / refuse_n if refuse_n else 0,
        "conclusion": concl_ok / concl_n if concl_n else 0,
        "citation": cite_hit / cite_n if cite_n else 0,
        "n": len(items), "cite_total": cite_total,
    }
    return {"metrics": m, "rows": rows}


def render(out: dict) -> str:
    m = out["metrics"]
    def gate(key, val, lower_better=False):
        t = THRESHOLDS[key]
        ok = (val <= t) if lower_better else (val >= t)
        return "✅" if ok else "❌"
    lines = [f"# 评测报告（{now_iso()}）", "",
             f"题数 {m['n']} · 引用合计 {m['cite_total']}", "",
             "| 指标 | 得分 | 门槛 | |", "|---|---|---|---|",
             f"| 编造引用率 | {m['fabricated_rate']:.1%}（{m['fabricated_count']} 条）| =0 | {gate('fabricated_rate', m['fabricated_rate'], True)} |",
             f"| 结论正确率 | {m['conclusion']:.1%} | ≥85% | {gate('conclusion', m['conclusion'])} |",
             f"| 拒答恰当率 | {m['refuse']:.1%} | ≥80% | {gate('refuse', m['refuse'])} |",
             f"| 引用完整率 | {m['citation']:.1%} | ≥75% | {gate('citation', m['citation'])} |", "",
             "| 题 | 类别 | 路由 | 金额 | 拒答 | 结论 | 编造 | 引用 |",
             "|---|---|---|---|---|---|---|---|"]
    for r in out["rows"]:
        lines.append(f"| {r['id']} | {r['cat']} | {r['route']} | "
                     f"{r['amount'] if r['amount'] is not None else '—'} | "
                     f"{'✓' if r['refuse_ok'] else '✗'} | {'✓' if r['concl_ok'] else '✗'} | "
                     f"{('⚠'+str(len(r['fab']))) if r['fab'] else '—'} | {r['cite_hit'] or '—'} |")
    return "\n".join(lines) + "\n"


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    path = Path(args[0]) if args else (EVAL_DIR / "eval_v1.jsonl")
    items = [json.loads(ln) for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]
    out = evaluate(items)
    m = out["metrics"]
    report = render(out)
    stamp = now_iso()[:10]
    (EVAL_DIR / f"report_{stamp}.md").write_text(report, encoding="utf-8")

    # 落 eval_run
    try:
        ac = sqlite3.connect(ROOT / "db" / "app.db", timeout=5)
        ac.execute("""INSERT INTO eval_run(run_at, system_version, score_overall,
                      score_by_topic, fabricated_citation_count) VALUES (?,?,?,?,?)""",
                   (now_iso(), "eval_v1", m["conclusion"], json.dumps(m, ensure_ascii=False),
                    m["fabricated_count"]))
        ac.commit(); ac.close()
    except Exception as exc:
        print(f"[eval_run 落库失败] {exc}", file=sys.stderr)

    print(report)
    print(f"报告已写 data/eval/report_{stamp}.md")
    if m["fabricated_count"] > 0:
        print(f"\n❌ 编造引用 {m['fabricated_count']} 条 > 0（PRD 红线），退出码非 0")
        return 1
    print("\n✅ 编造引用率 = 0（红线通过）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
