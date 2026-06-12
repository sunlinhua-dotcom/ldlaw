"""法条核验流水线（T1.4 → 人工闸口 G1）。

把 seed JSON 的法条逐条与官方原文比对，只有"逐字一致"才允许置 verified=1。
verified 状态是 seed JSON 的字段（knowledge.db 整库重建时从 seed 读），因此核验
结果写回 seed JSON，扛得住重建、可 git 审计。

数据流：
  官方快照 data/raw/<slug>.txt（ingest_law.py 抓取时已存）
      └─ split_articles 重新切条 ──┐
  seed data/seed/laws/<slug>.json ─┴─ 逐条 norm 比对 → 一致 / 差异 / 缺失
                                        ↓
                              data/verify_report.md（差异列出，全一致 = 待批）
                                        ↓
                       人工 --approve <slug>（G1）→ verified=1 + 溯源写回 seed

用法：
  python3 src/verify_articles.py --check               # 离线：seed vs data/raw 快照
  python3 src/verify_articles.py --check <slug>...      # 指定法规
  python3 src/verify_articles.py --fetch <slug>...      # 联网：按 manifest fetch_url 重抓再比
  python3 src/verify_articles.py --status               # verified 覆盖率总览
  python3 src/verify_articles.py --approve <slug> --by 张三   # 全一致才置 verified=1

边界：--approve 只有在该法规 0 差异 0 缺失时才放行；有差异先人工核对修 seed。
"""
from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from ingest_law import fetch_html, html_to_text, split_articles  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
LAWS = ROOT / "data" / "seed" / "laws"
RAW = ROOT / "data" / "raw"
REPORT = ROOT / "data" / "verify_report.md"


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def norm(text: str) -> str:
    """比对用归一化：去掉所有空白与零宽字符，标点/汉字原样保留（逐字核验）。"""
    return re.sub(r"[\s​　﻿]+", "", text or "")


# ---------- 核心 diff（纯函数，便于测试）----------

def diff_articles(seed_articles: list[dict],
                  fetched_pairs: list[tuple[str, str]]) -> dict:
    """seed 法条 × 官方切条结果 → 逐条状态。

    返回 {rows:[{article_no,clause_no,status,detail}], counts:{...}, clean:bool}
    status ∈ identical / differs / missing_in_source
    - 整条（clause_no 为空）：norm 文本全等才 identical
    - 拆款行（clause_no 非空）：款文本须为父条官方原文的子串（norm 后）
    """
    fetched = {no: body for no, body in fetched_pairs}
    rows = []
    for a in seed_articles:
        no = a["article_no"]
        clause = a.get("clause_no")
        seed_text = norm(a["text"])
        src_body = fetched.get(no)
        if src_body is None:
            rows.append({"article_no": no, "clause_no": clause,
                         "status": "missing_in_source",
                         "detail": "官方原文中未找到该条"})
            continue
        src_norm = norm(src_body)
        if clause:  # 拆款：款文须落在父条原文内
            ok = seed_text in src_norm
            rows.append({"article_no": no, "clause_no": clause,
                         "status": "identical" if ok else "differs",
                         "detail": "" if ok else "款文不在官方父条原文内"})
        else:
            ok = seed_text == src_norm
            rows.append({"article_no": no, "clause_no": clause,
                         "status": "identical" if ok else "differs",
                         "detail": "" if ok else _first_diff(seed_text, src_norm)})
    counts = {"total": len(rows),
              "identical": sum(r["status"] == "identical" for r in rows),
              "differs": sum(r["status"] == "differs" for r in rows),
              "missing_in_source": sum(r["status"] == "missing_in_source" for r in rows)}
    return {"rows": rows, "counts": counts,
            "clean": counts["differs"] == 0 and counts["missing_in_source"] == 0
                     and counts["total"] > 0}


def _first_diff(a: str, b: str, ctx: int = 14) -> str:
    """定位首个不同字符，给出上下文，便于人工核对。"""
    i = 0
    while i < len(a) and i < len(b) and a[i] == b[i]:
        i += 1
    lo = max(0, i - ctx)
    return (f"首差@{i}字：seed…{a[lo:i + ctx]}… / 官方…{b[lo:i + ctx]}…")


# ---------- 取官方原文 ----------

def fetched_pairs_for(slug: str, manifest: dict, online: bool) -> tuple[list, str]:
    """返回 (切条结果, 来源说明)。online=False 用 data/raw 快照，True 按 manifest 重抓。"""
    if online:
        entry = next((e for e in manifest["laws"] if e["slug"] == slug), None)
        if not entry or not entry.get("fetch_url"):
            raise RuntimeError(f"{slug}: manifest 无 fetch_url，无法联网核验")
        text = html_to_text(fetch_html(entry["fetch_url"]))
        (RAW / f"{slug}.fetch.txt").write_text(text, encoding="utf-8")  # 留存本次快照
        return split_articles(text), f"联网重抓 {entry['fetch_url']}"
    raw = RAW / f"{slug}.txt"
    if not raw.exists():
        raise RuntimeError(f"{slug}: 缺官方快照 {raw}（先 ingest_law.py 抓取或 --fetch）")
    return split_articles(raw.read_text(encoding="utf-8")), f"离线快照 {raw.name}"


def load_seed(slug: str) -> dict:
    f = LAWS / f"{slug}.json"
    if not f.exists():
        raise RuntimeError(f"{slug}: seed 不存在 {f}")
    return json.loads(f.read_text(encoding="utf-8"))


def slugs_from_args(args: list[str], manifest: dict) -> list[str]:
    if args:
        return args
    return [f.stem for f in sorted(LAWS.glob("*.json")) if not f.name.startswith("_")]


# ---------- 命令 ----------

def cmd_check(slugs: list[str], manifest: dict, online: bool) -> int:
    blocks, ready = [], []
    for slug in slugs:
        seed = load_seed(slug)
        try:
            pairs, src = fetched_pairs_for(slug, manifest, online)
        except RuntimeError as exc:
            blocks.append(f"## {slug}\n\n- ⚠ 跳过：{exc}\n")
            continue
        d = diff_articles(seed["articles"], pairs)
        c = d["counts"]
        head = (f"## {seed['title']}（{slug}）\n\n"
                f"- 来源：{src}\n"
                f"- 一致 {c['identical']} / 差异 {c['differs']} / 缺失 {c['missing_in_source']}"
                f" / 共 {c['total']}\n")
        bad = [r for r in d["rows"] if r["status"] != "identical"]
        if d["clean"]:
            head += "- ✅ **全部逐字一致 → 可 `--approve %s`**\n" % slug
            ready.append(slug)
        else:
            head += "\n| 条款 | 状态 | 说明 |\n|---|---|---|\n"
            for r in bad[:60]:
                ck = f"第{r['clause_no']}款" if r["clause_no"] else ""
                head += f"| {r['article_no']}{ck} | {r['status']} | {r['detail']} |\n"
            if len(bad) > 60:
                head += f"| … | … | 另有 {len(bad) - 60} 条差异，见命令行 |\n"
        blocks.append(head)
        print(f"  [{'✅' if d['clean'] else '差异'}] {seed['title']}："
              f"一致 {c['identical']}/{c['total']}"
              + ("" if d["clean"] else f"，差异 {c['differs']}，缺失 {c['missing_in_source']}"))

    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(
        f"# 法条核验报告（{now_iso()}）\n\n"
        f"模式：{'联网重抓' if online else '离线快照'}　|　待批法规："
        f"{('、'.join(ready)) or '（无）'}\n\n" + "\n".join(blocks),
        encoding="utf-8")
    print(f"\n报告已写 {REPORT.relative_to(ROOT)}；待批 {len(ready)} 部"
          + (f"：{'、'.join(ready)}" if ready else ""))
    return 0


def cmd_approve(slug: str, by: str, manifest: dict) -> int:
    seed = load_seed(slug)
    pairs, src = fetched_pairs_for(slug, manifest, online=False)
    d = diff_articles(seed["articles"], pairs)
    if not d["clean"]:
        c = d["counts"]
        print(f"✗ 拒绝核验：{seed['title']} 尚有差异 {c['differs']} / 缺失 "
              f"{c['missing_in_source']}（对照 {src}）。先 --check 核对并修正 seed。")
        return 1
    stamp = now_iso()
    for a in seed["articles"]:
        a["verified"] = True
        a["verified_by"] = by
        a["verified_at"] = stamp
    seed["_verified_note"] = f"经 {by} 对照官方快照逐字核验通过 @ {stamp}（G1）"
    (LAWS / f"{slug}.json").write_text(
        json.dumps(seed, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"✓ {seed['title']}：{d['counts']['total']} 条全部置 verified=1（核验人 {by}）")
    print("  下一步：python3 src/build_knowledge.py 重建生效")
    return 0


def cmd_status(manifest: dict) -> int:
    tot = ver = 0
    rows = []
    for f in sorted(LAWS.glob("*.json")):
        if f.name.startswith("_"):
            continue
        seed = json.loads(f.read_text(encoding="utf-8"))
        arts = seed["articles"]
        v = sum(1 for a in arts if a.get("verified"))
        tot += len(arts)
        ver += v
        rows.append((seed["title"], v, len(arts)))
    print("法条核验覆盖率：")
    for title, v, n in rows:
        bar = "✅" if v == n else ("·" if v == 0 else "◑")
        print(f"  {bar} {v:>3}/{n:<3}  {title}")
    pct = (ver / tot * 100) if tot else 0
    print(f"\n合计 {ver}/{tot} = {pct:.1f}%　（生产发布门槛 = 100%，见 PRD §10）")
    return 0


def main() -> int:
    manifest = json.loads((LAWS / "_manifest.json").read_text(encoding="utf-8"))
    argv = sys.argv[1:]
    args = [a for a in argv if not a.startswith("--")]
    if "--status" in argv:
        return cmd_status(manifest)
    if "--approve" in argv:
        if not args:
            print("用法：--approve <slug> [--by 姓名]")
            return 1
        by = "operator"
        if "--by" in argv and argv.index("--by") + 1 < len(argv):
            by = argv[argv.index("--by") + 1]
        # --by 的值会混进 args，剔除它
        args = [a for a in args if a != by]
        return cmd_approve(args[0], by, manifest)
    if "--check" in argv or "--fetch" in argv:
        online = "--fetch" in argv
        return cmd_check(slugs_from_args(args, manifest), manifest, online)
    print(__doc__)
    return 1


if __name__ == "__main__":
    sys.exit(main())
