"""博查（Bocha）联网搜索 —— 仅供运营/知识层脚本使用（接入方案 A）。

用途（PLAN §3 知识层 / EXECUTION T1.2–T1.4 / PRD F9）：
- 为 MISSING.md 中的缺失法规寻找官方文本来源
- 核验流水线（T1.4）按 source_url 重抓官方原文前的源发现
- 法规更新监测（F9）：新法规 / 司法解释发现

边界（CLAUDE.md 架构不变量）：
- 本模块**不得**被 server.py / pipeline.py 引用——问答管线只依据库内法条作答；
  问答侧接入（方案 B）需另行评审后单独开卡。
- 纯标准库实现，仅构建期 / 运营脚本调用，线上运行时零新增依赖。
- 无 BOCHA_API_KEY 时明确报错，不静默降级。
- 搜索 query 应为法规名 / 主题词，**不要传用户原始提问**（PIPL：避免个人信息出库）。

用法：
    python3 src/websearch.py "上海市企业工资支付办法"             # 全网，官方源排前
    python3 src/websearch.py "上海市企业工资支付办法" --official  # 仅官方域名
    python3 src/websearch.py "劳动争议司法解释二 2025" -n 5
"""
from __future__ import annotations

import json
import os
import sys
import urllib.parse
import urllib.request
from pathlib import Path

API_URL = "https://api.bochaai.com/v1/web-search"
ROOT = Path(__file__).resolve().parent.parent

# 官方域名（后缀匹配）：覆盖政府、人大、法院、人社系统站点
OFFICIAL_SUFFIXES = (".gov.cn",)


def _api_key() -> str:
    key = os.environ.get("BOCHA_API_KEY", "").strip()
    if not key:
        envf = ROOT / ".env"
        if envf.exists():
            for line in envf.read_text(encoding="utf-8").splitlines():
                if line.startswith("BOCHA_API_KEY="):
                    key = line.split("=", 1)[1].strip()
                    break
    if not key:
        raise RuntimeError("缺少 BOCHA_API_KEY（写入 .env 或环境变量后重试）")
    return key


def is_official(url: str) -> bool:
    """gov.cn 体系（含 flk.npc.gov.cn、各地人社/法院）视为官方源。"""
    try:
        host = urllib.parse.urlsplit(url).hostname or ""
    except ValueError:
        return False
    return host.endswith(OFFICIAL_SUFFIXES)


def search(query: str, count: int = 10, official_only: bool = False,
           freshness: str = "noLimit", timeout: int = 15) -> list[dict]:
    """博查 Web Search。返回 [{title,url,snippet,summary,site,date,official}]，官方源排前。

    freshness: noLimit / oneDay / oneWeek / oneMonth / oneYear（更新监测场景用短窗口）。
    """
    body = json.dumps({
        "query": query, "count": count, "summary": True, "freshness": freshness,
    }).encode("utf-8")
    req = urllib.request.Request(API_URL, data=body, method="POST", headers={
        "Authorization": f"Bearer {_api_key()}",
        "Content-Type": "application/json",
    })
    with urllib.request.urlopen(req, timeout=timeout) as r:
        out = json.loads(r.read().decode("utf-8"))
    if not isinstance(out, dict) or out.get("code") != 200:
        raise RuntimeError(f"博查 API 异常：{str(out)[:200]}")
    pages = (((out.get("data") or {}).get("webPages") or {}).get("value")) or []
    results = [{
        "title": p.get("name", ""),
        "url": p.get("url", ""),
        "snippet": p.get("snippet", ""),
        "summary": p.get("summary", ""),
        "site": p.get("siteName", ""),
        "date": (p.get("dateLastCrawled") or "")[:10],
        "official": is_official(p.get("url", "")),
    } for p in pages if isinstance(p, dict)]
    if official_only:
        results = [x for x in results if x["official"]]
    return sorted(results, key=lambda x: not x["official"])


def main() -> None:
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    if not args:
        print(__doc__)
        sys.exit(1)
    official = "--official" in sys.argv
    n = 10
    if "-n" in sys.argv:
        n = int(sys.argv[sys.argv.index("-n") + 1])
    rs = search(args[0], count=n, official_only=official)
    if not rs:
        print("（无结果）")
        return
    for i, x in enumerate(rs, 1):
        tag = "官方" if x["official"] else "非官方"
        print(f"{i}. [{tag}] {x['title']}  ·  {x['site']}  ·  {x['date']}")
        print(f"   {x['url']}")
        if x["snippet"]:
            print(f"   {x['snippet'][:120]}")
    print(f"\n共 {len(rs)} 条（官方源已排前；--official 仅看官方）")


if __name__ == "__main__":
    main()
