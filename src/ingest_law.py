"""法规切条工具（T1.1）：从官方网页抓取全文 → 自动切条 → seed JSON。

用法：
  python3 src/ingest_law.py            # 处理 manifest 中全部条目
  python3 src/ingest_law.py <slug>...  # 只处理指定条目
  python3 src/ingest_law.py --offline <slug>  # 跳过抓取，用 data/raw/<slug>.txt

数据流（文本不经过任何模型，零编造风险）：
  manifest(_manifest.json) → 抓官方页面 → 去 HTML → 定位正文 → 按"第X条"切条
  → 条号连续性自检 → data/seed/laws/<slug>.json（verified 一律 0，待 G1 核验）

抓取失败的条目会列入 data/raw/MISSING.md，可人工把全文存为
data/raw/<slug>.txt 后用 --offline 重跑。
"""
from __future__ import annotations

import json
import re
import sys
import urllib.request
from html import unescape
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from zhnum import article_no_to_int, cjk_to_int, norm_clause  # noqa: F401

ROOT = Path(__file__).resolve().parent.parent
LAWS = ROOT / "data" / "seed" / "laws"
RAW = ROOT / "data" / "raw"
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36")

# 主题关键词规则（命中数排序取前 2；无命中用 manifest 的 default_topics）
TOPIC_RULES = [
    ("招聘入职", r"招用|录用|就业歧视|职业介绍"),
    ("劳动合同", r"劳动合同的订立|书面劳动合同|试用期|无固定期限|固定期限|合同文本|劳务派遣|非全日制"),
    ("工时与加班", r"工作时间|延长工作时间|加班|工时|休息日安排.{0,4}工作"),
    ("休息休假", r"年休假|休假|休息日|法定休假日"),
    ("工资与福利", r"工资|劳动报酬|最低工资|报酬"),
    ("社保公积金", r"社会保险|保险费|养老保险|医疗保险|失业保险|生育保险"),
    ("规章制度", r"规章制度|公示|民主"),
    ("调岗调薪", r"变更劳动合同|调整工作岗位|不能胜任"),
    ("解除与终止", r"解除|终止"),
    ("经济补偿与赔偿", r"经济补偿|赔偿金|二倍|惩罚性"),
    ("竞业限制与保密", r"竞业限制|保密|商业秘密"),
    ("女职工与三期", r"女职工|孕期|产期|哺乳期|产假|生育|流产"),
    ("工伤", r"工伤|职业病|伤残"),
    ("劳动争议", r"仲裁|调解|争议|诉讼"),
]

ART_RE = re.compile(r"^第([一二三四五六七八九十百零]{1,7})条")


def fetch_html(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = resp.read()
    for enc in ("utf-8", "gb18030"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="ignore")


def html_to_text(html: str) -> str:
    html = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", html)
    html = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</tr>|</li>", "\n", html)
    text = re.sub(r"<[^>]+>", "", html)
    text = unescape(text)
    text = text.replace("　", " ").replace("\xa0", " ")
    lines = [ln.strip() for ln in text.splitlines()]
    return "\n".join(ln for ln in lines if ln)


def split_articles(text: str) -> list[tuple[str, str]]:
    """正文 → [(第X条, 条文全文含款)]。以行首"第X条"为界。"""
    lines = text.splitlines()
    # 定位第一处行首"第一条"，截掉页面导航/目录等头部噪音
    start = next((i for i, ln in enumerate(lines)
                  if ART_RE.match(ln) and article_no_to_int(ln[:8] + "条") == 1
                  or ln.startswith("第一条")), None)
    if start is None:
        start = next((i for i, ln in enumerate(lines) if ART_RE.match(ln)), 0)
    arts: list[tuple[str, list[str]]] = []
    for ln in lines[start:]:
        m = ART_RE.match(ln)
        if m:
            no = f"第{m.group(1)}条"
            body = ln[m.end():].lstrip(" 　:：").strip()
            arts.append((no, [body] if body else []))
        elif arts:
            # 页脚噪音启发式：抓到末条后出现明显非条文行则停止
            if re.match(r"^(附件|相关链接|扫一扫|打印|【|（来源|来源[:：]|分享到|国家规章库|"
                        r"国家法律法规数据库|版权所有|主办|链接[:：]|返回顶部|分享|关闭|"
                        r"网站地图|访问电脑版|手机版|电脑版|联系电话|传真|网站标识码|"
                        r"责任编辑|地址[:：]|邮编|.{0,4}ICP备|.{0,4}公网安备)", ln):
                break
            arts[-1][1].append(ln)
    out = []
    for no, parts in arts:
        body = "\n".join(p for p in parts if p).strip()
        if body:
            out.append((no, body))
    return out


def pick_topics(text: str, default: list[str]) -> list[str]:
    scored = [(len(re.findall(pat, text)), name) for name, pat in TOPIC_RULES]
    scored = [(c, n) for c, n in scored if c > 0]
    scored.sort(reverse=True)
    return [n for _, n in scored[:2]] or default


def check_sequence(arts: list[tuple[str, str]]) -> list[str]:
    issues, prev = [], 0
    for no, _ in arts:
        n = article_no_to_int(no)
        if n != prev + 1:
            issues.append(f"条号跳变：{no}（前一条为第 {prev} 条）")
        prev = n
    return issues


def process(entry: dict, offline: bool = False) -> tuple[bool, str]:
    slug = entry["slug"]
    if offline:
        raw_file = RAW / f"{slug}.txt"
        if not raw_file.exists():
            return False, f"--offline 但 {raw_file} 不存在"
        text = raw_file.read_text(encoding="utf-8")
    else:
        try:
            text = html_to_text(fetch_html(entry["fetch_url"]))
        except Exception as exc:
            return False, f"抓取失败：{exc}"
        (RAW / f"{slug}.txt").write_text(text, encoding="utf-8")  # 留存原始文本备核验
    arts = split_articles(text)
    if len(arts) < entry.get("min_articles", 3):
        return False, f"切条数 {len(arts)} < 下限 {entry.get('min_articles', 3)}，疑似页面结构未识别"
    issues = check_sequence(arts)
    default_topics = entry.get("default_topics", ["劳动合同"])
    clause_splits: dict = entry.get("clause_splits", {})
    articles = []
    for no, body in arts:
        articles.append({"article_no": no, "clause_no": None, "text": body,
                         "topics": pick_topics(body, default_topics), "verified": False})
        # 需单独引用的款：按行拆出（第 n 行 = 第 n 款，法律条文款以换行分隔）
        for clause in clause_splits.get(no, []):
            paras = [p for p in body.split("\n") if p]
            idx = cjk_to_int(clause) - 1
            if 0 <= idx < len(paras):
                articles.append({"article_no": no, "clause_no": norm_clause(clause),
                                 "text": paras[idx],
                                 "topics": pick_topics(paras[idx], default_topics),
                                 "verified": False})
    out = {
        "_comment": f"由 ingest_law.py 自官方页面抓取切条；原始文本存 data/raw/{slug}.txt；"
                    f"verified=0 待 G1 核验。" + (f" 切条告警：{issues}" if issues else ""),
        "title": entry["title"], "doc_no": entry.get("doc_no"),
        "issuer": entry.get("issuer"), "level": entry["level"],
        "region": entry.get("region", "CN"),
        "publish_date": entry.get("publish_date"),
        "effective_date": entry.get("effective_date"),
        "status": "active", "source_url": entry["source_url"],
        "coverage": entry.get("coverage", "full"),
        "articles": articles,
    }
    LAWS.mkdir(parents=True, exist_ok=True)
    (LAWS / f"{slug}.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    warn = f"；⚠ {len(issues)} 处条号跳变" if issues else ""
    return True, f"{len(arts)} 条（含拆款共 {len(articles)} 行）{warn}"


def main() -> None:
    RAW.mkdir(parents=True, exist_ok=True)
    manifest = json.loads((LAWS / "_manifest.json").read_text(encoding="utf-8"))
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    offline = "--offline" in sys.argv
    entries = [e for e in manifest["laws"] if not args or e["slug"] in args]
    missing = []
    for e in entries:
        ok, msg = process(e, offline=offline)
        print(f"  [{'✓' if ok else '✗'}] {e['title']}: {msg}")
        if not ok:
            missing.append((e, msg))
    if missing:
        md = ["# 抓取失败清单（人工提供 data/raw/<slug>.txt 后 --offline 重跑）", ""]
        md += [f"- **{e['title']}**（{e['slug']}）：{m}\n  来源：{e['fetch_url']}"
               for e, m in missing]
        (RAW / "MISSING.md").write_text("\n".join(md), encoding="utf-8")
        print(f"\n⚠ {len(missing)} 部抓取失败，已写 data/raw/MISSING.md")


if __name__ == "__main__":
    main()
