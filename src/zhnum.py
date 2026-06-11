"""中文数字工具（条号/款号解析与归一化，标准库实现）。"""
from __future__ import annotations

_DIG = {"零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4,
        "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
_UNIT = {"十": 10, "百": 100, "千": 1000}


def cjk_to_int(s: str) -> int:
    """中文数字 → int（支持到千位，如 一百零三 → 103）。解析不出返回 0。"""
    total, num = 0, 0
    for ch in s:
        if ch in _DIG:
            num = _DIG[ch]
        elif ch in _UNIT:
            total += (num or 1) * _UNIT[ch]
            num = 0
    return total + num


def int_to_cjk(n: int) -> str:
    """int → 中文数字（1–9999，法律条号足够）。"""
    if n <= 0:
        return str(n)
    digits = "零一二三四五六七八九"
    parts = []
    for unit_val, unit_ch in ((1000, "千"), (100, "百"), (10, "十")):
        d = n // unit_val
        if d:
            # 十位为 1 且无更高位时省"一"（十五而非一十五）
            if not (unit_val == 10 and d == 1 and not parts):
                parts.append(digits[d])
            parts.append(unit_ch)
            n -= d * unit_val
        elif parts and n:
            if parts[-1] != "零":
                parts.append("零")
    if n:
        parts.append(digits[n])
    return "".join(parts)


def norm_clause(s: str | None) -> str | None:
    """款号归一化为中文数字：'3' → '三'，'三' → '三'，None → None。"""
    if s is None:
        return None
    s = str(s).strip()
    if not s:
        return None
    if s.isdigit():
        return int_to_cjk(int(s))
    return s


def article_no_to_int(article_no: str) -> int:
    """'第八十二条' → 82。解析不出返回 0。"""
    import re
    m = re.match(r"第(.+?)条", article_no)
    return cjk_to_int(m.group(1)) if m else 0
