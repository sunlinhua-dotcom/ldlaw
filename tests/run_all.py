"""统一测试入口（T0.8）：发现并运行 tests/ 下全部 test_*.py。

用法：
  python3 tests/run_all.py            # 跑全部单元测试
  python3 tests/run_all.py -v         # 详细输出

退出码非 0 = 有用例失败（CI / preship 钩子据此判定）。
"""
import sys
import unittest
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent


def build_suite() -> unittest.TestSuite:
    return unittest.defaultTestLoader.discover(
        start_dir=str(TESTS_DIR), pattern="test_*.py")


def main() -> int:
    verbosity = 2 if "-v" in sys.argv else 1
    result = unittest.TextTestRunner(verbosity=verbosity).run(build_suite())
    n = result.testsRun
    print(f"\n汇总：运行 {n} 个用例，失败 {len(result.failures)}，错误 {len(result.errors)}")
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main())
