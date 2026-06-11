"""确定性计算器（PRD §F3）：涉钱问题用代码算，不用模型算。

实现范围（M0 三件套）：
- severance        经济补偿金 N（《劳动合同法》第四十六、四十七条）
- unlawful_damages 违法解除赔偿金 2N（第八十七条）
- annual_leave     年休假天数 / 离职折算 / 未休折算工资（年休假条例及实施办法）

已知边界（输出 warnings 里会声明）：
- 2008-01-01 前入职的分段计算口径（上海等地）未实现；
- 月工资口径 = 解除前 12 个月平均应发工资，由调用方提供，不在此校验。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

MONTH_DAYS = [31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
PAID_DAYS_PER_MONTH = 21.75  # 月计薪天数（劳社部发〔2008〕3 号口径）


def _leap(y: int) -> bool:
    return y % 4 == 0 and (y % 100 != 0 or y % 400 == 0)


def _add_months(d: date, months: int) -> date:
    y = d.year + (d.month - 1 + months) // 12
    m = (d.month - 1 + months) % 12 + 1
    dim = 29 if (m == 2 and _leap(y)) else MONTH_DAYS[m - 1]
    return date(y, m, min(d.day, dim))


def service_n(hire: date, term: date) -> float:
    """第四十七条折算年限 N：每满一年 1 个月；六个月以上不满一年按一年；不满六个月按半个月。"""
    if term <= hire:
        raise ValueError("离职日期必须晚于入职日期")
    years = 0
    while _add_months(hire, (years + 1) * 12) <= term:
        years += 1
    rem_start = _add_months(hire, years * 12)
    if rem_start == term:
        return float(years)
    if _add_months(rem_start, 6) <= term:
        return years + 1.0
    return years + 0.5


@dataclass
class CalcResult:
    key: str
    amount: float
    steps: list[str] = field(default_factory=list)
    citations: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def severance(hire: date, term: date, avg_monthly_wage: float,
              social_avg_monthly: float | None = None,
              social_avg_note: str = "") -> CalcResult:
    """经济补偿金。social_avg_monthly = 当地上年度职工月平均工资（用于 3 倍封顶判断）。"""
    n = service_n(hire, term)
    base = float(avg_monthly_wage)
    steps = [f"工作年限 {hire.isoformat()} 至 {term.isoformat()}，折算 N = {n:g} 个月"]
    warnings = ["月工资口径 = 解除/终止前 12 个月平均应发工资（含奖金、津贴）",
                "2008-01-01 前入职的分段计算口径未在演示版实现，遇到请转律师核算"]
    if social_avg_monthly is not None:
        cap = 3 * social_avg_monthly
        if base > cap:
            steps.append(
                f"月工资 {base:,.0f} 元 > 3 × 社平 {social_avg_monthly:,.0f} = {cap:,.0f} 元，"
                f"基数封顶为 {cap:,.0f} 元，年限封顶 12 年{social_avg_note}")
            base = cap
            if n > 12:
                steps.append(f"N 由 {n:g} 封顶至 12")
                n = 12.0
        else:
            steps.append(f"月工资 {base:,.0f} 元 ≤ 3 × 社平，不触发封顶{social_avg_note}")
    else:
        warnings.append("未提供当地社平工资，未校验 3 倍封顶规则")
    amount = round(base * n, 2)
    steps.append(f"经济补偿 = {base:,.0f} × {n:g} = {amount:,.2f} 元")
    return CalcResult(
        key="severance", amount=amount, steps=steps,
        citations=["《中华人民共和国劳动合同法》第四十六条",
                   "《中华人民共和国劳动合同法》第四十七条"],
        warnings=warnings,
    )


def unlawful_damages(hire: date, term: date, avg_monthly_wage: float,
                     social_avg_monthly: float | None = None) -> CalcResult:
    """违法解除赔偿金 = 经济补偿标准的二倍（第八十七条）。"""
    s = severance(hire, term, avg_monthly_wage, social_avg_monthly)
    amount = round(s.amount * 2, 2)
    steps = s.steps + [f"违法解除赔偿金 = 经济补偿 {s.amount:,.2f} × 2 = {amount:,.2f} 元"]
    return CalcResult(
        key="unlawful_damages", amount=amount, steps=steps,
        citations=s.citations + ["《中华人民共和国劳动合同法》第八十七条"],
        warnings=s.warnings + ["支付赔偿金的不再同时支付经济补偿（司法解释口径，正式版入库后引用）"],
    )


def statutory_annual_days(cumulative_years: float) -> int:
    """累计工龄对应的全年应休年假天数（条例第三条）。"""
    if cumulative_years < 1:
        return 0
    if cumulative_years < 10:
        return 5
    if cumulative_years < 20:
        return 10
    return 15


def exit_prorated_unused_days(year_passed_days: int, annual_days: int,
                              taken_days: float) -> int:
    """离职折算应付未休天数（实施办法第十二条）：不足 1 整天部分不支付。"""
    payable = int(year_passed_days / 365 * annual_days - taken_days)
    return max(payable, 0)


def annual_leave_payout(monthly_wage: float, unused_days: int) -> CalcResult:
    """未休年假折算工资：日工资 300%，其中含正常工资（额外 200%）。"""
    daily = monthly_wage / PAID_DAYS_PER_MONTH
    total = round(daily * 3 * unused_days, 2)
    extra = round(daily * 2 * unused_days, 2)
    steps = [
        f"日工资 = {monthly_wage:,.0f} ÷ 21.75 = {daily:,.2f} 元",
        f"未休 {unused_days} 天 × 日工资 × 300% = {total:,.2f} 元（其中含正常工资，额外应补 {extra:,.2f} 元）",
    ]
    return CalcResult(
        key="annual_leave", amount=total, steps=steps,
        citations=["《职工带薪年休假条例》第三条",
                   "《职工带薪年休假条例》第五条第三款",
                   "《企业职工带薪年休假实施办法》第十条",
                   "《企业职工带薪年休假实施办法》第十二条"],
        warnings=["「累计工龄」含此前其他单位年限，需员工提供证明材料"],
    )
