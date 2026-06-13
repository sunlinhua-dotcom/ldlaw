"""生成词条律师审核清单（G2）：data/review/entries_review.md。

逐条输出：标题 + 结论 + 操作指引 + 风险点 + 引用法条原文（从 knowledge.db 取） +
勾选位（通过 / 驳回 / 修改意见）。供合作律师在 G2 闸口逐条核。
"""
import json
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
KDB = ROOT / "db" / "knowledge.db"
OUT = ROOT / "data" / "review" / "entries_review.md"


def cite_text(kc, source: str, article_no: str) -> str:
    row = kc.execute(
        """SELECT la.text FROM legal_article la JOIN legal_source ls ON ls.id = la.source_id
           WHERE ls.title = ? AND la.article_no = ? AND la.clause_no IS NULL""",
        (source, article_no)).fetchone()
    return row[0] if row else "（库内未找到原文）"


def main() -> None:
    kc = sqlite3.connect(KDB)
    rows = kc.execute(
        "SELECT title, slug, status, body FROM entry ORDER BY status, slug").fetchall()
    md = ["# 词条律师审核清单（G2）", "",
          f"共 {len(rows)} 条。请逐条勾选：通过 / 驳回，并在『修改意见』写明。",
          "审核通过后由运营置 status=published、reviewed_by/reviewed_at。", "",
          "---", ""]
    for title, slug, status, body in rows:
        b = json.loads(body)
        md.append(f"## {title}")
        md.append(f"`{slug}` · 状态 **{status}**\n")
        md.append(f"**结论**：{b['conclusion']}\n")
        if b.get("how_to"):
            md.append("**操作指引**：")
            md += [f"{i+1}. {s}" for i, s in enumerate(b["how_to"])]
            md.append("")
        if b.get("pitfalls"):
            md.append("**常见误区/风险**：")
            md += [f"- {s}" for s in b["pitfalls"]]
            md.append("")
        # 引用原文
        cites = kc.execute(
            """SELECT ls.title, la.article_no, la.text
               FROM entry e JOIN entry_citation ec ON ec.entry_id = e.id
               JOIN legal_article la ON la.id = ec.article_id
               JOIN legal_source ls ON ls.id = la.source_id
               WHERE e.slug = ? ORDER BY ec.rowid""", (slug,)).fetchall()
        if cites:
            md.append("**引用法条原文**：")
            for src, ano, text in cites:
                md.append(f"- 《{src}》{ano}：{text}")
            md.append("")
        md.append("**审核**：☐ 通过　☐ 驳回　修改意见：________________")
        md.append("\n---\n")
    kc.close()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("\n".join(md), encoding="utf-8")
    print(f"已写 {OUT.relative_to(ROOT)}（{len(rows)} 条）")


if __name__ == "__main__":
    main()
