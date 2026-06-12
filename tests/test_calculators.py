"""计算器单元测试：python3 tests/test_calculators.py"""
import sys
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from calculators import (service_n, severance, unlawful_damages,
                         statutory_annual_days, exit_prorated_unused_days,
                         annual_leave_payout)


class TestServiceN(unittest.TestCase):
    def test_exact_years(self):
        self.assertEqual(service_n(date(2023, 6, 1), date(2026, 6, 1)), 3.0)

    def test_under_six_months_remainder(self):
        # 3 年 + 3 个月 9 天 → 不满六个月 → +0.5
        self.assertEqual(service_n(date(2023, 6, 1), date(2026, 9, 10)), 3.5)

    def test_over_six_months_remainder(self):
        # 3 年 + 6 个月 14 天 → 六个月以上 → 按 1 年
        self.assertEqual(service_n(date(2023, 6, 1), date(2026, 12, 15)), 4.0)

    def test_exactly_six_months_remainder(self):
        # 「六个月以上」含本数 → 按 1 年
        self.assertEqual(service_n(date(2023, 6, 1), date(2026, 12, 1)), 4.0)

    def test_under_six_months_total(self):
        self.assertEqual(service_n(date(2026, 1, 10), date(2026, 5, 1)), 0.5)

    def test_invalid(self):
        with self.assertRaises(ValueError):
            service_n(date(2026, 6, 1), date(2026, 6, 1))


class TestSeverance(unittest.TestCase):
    def test_plain(self):
        r = severance(date(2023, 6, 1), date(2026, 6, 10), 15000)
        self.assertEqual(r.amount, 15000 * 3.5)

    def test_cap_base_and_years(self):
        # 月薪 5 万 > 3 × 社平 1.2 万 → 基数 3.6 万；工龄 15 年 → 年限封顶 12
        r = severance(date(2010, 1, 1), date(2025, 6, 1), 50000, social_avg_monthly=12000)
        self.assertEqual(r.amount, 36000 * 12)

    def test_no_cap_when_below(self):
        r = severance(date(2023, 6, 1), date(2026, 6, 1), 15000, social_avg_monthly=12000)
        self.assertEqual(r.amount, 15000 * 3)

    def test_unlawful_is_double(self):
        s = severance(date(2023, 6, 1), date(2026, 6, 1), 15000, social_avg_monthly=12000)
        d = unlawful_damages(date(2023, 6, 1), date(2026, 6, 1), 15000, social_avg_monthly=12000)
        self.assertEqual(d.amount, s.amount * 2)


class TestAnnualLeave(unittest.TestCase):
    def test_statutory_days(self):
        self.assertEqual(statutory_annual_days(0.5), 0)
        self.assertEqual(statutory_annual_days(3), 5)
        self.assertEqual(statutory_annual_days(12), 10)
        self.assertEqual(statutory_annual_days(25), 15)

    def test_exit_prorate_floor(self):
        # 已过 183 天、全年 5 天、已休 0 → 183/365*5 = 2.506 → 2 天（不足 1 天不付）
        self.assertEqual(exit_prorated_unused_days(183, 5, 0), 2)

    def test_exit_prorate_negative_clamped(self):
        self.assertEqual(exit_prorated_unused_days(30, 5, 3), 0)

    def test_payout(self):
        # T0.6 口径：amount = 企业额外应补 200%；300% 法定总额并列在 steps 中
        r = annual_leave_payout(monthly_wage=8700, unused_days=2)
        daily = 8700 / 21.75  # = 400
        self.assertAlmostEqual(r.amount, daily * 2 * 2, places=2)
        self.assertTrue(any("300%" in s for s in r.steps), "300% 总额应在计算过程中并列展示")


if __name__ == "__main__":
    unittest.main(verbosity=2)
