import importlib
import sys
import unittest
from pathlib import Path
from types import ModuleType
from unittest.mock import patch

PACKAGE = "_period_parser_test_package"
package = ModuleType(PACKAGE)
package.__path__ = [str(Path(__file__).resolve().parents[1])]
with patch.dict(sys.modules, {PACKAGE: package}):
    period_times = importlib.import_module(f"{PACKAGE}.period_times")


class PeriodTimesTest(unittest.TestCase):
    def test_mapping_rows_and_plain_text_produce_same_sparse_profile(self):
        expected = {1: ("08:25", "09:10"), 3: ("10:15", "11:00")}
        inputs = [
            '{"1":["08:25","09:10"],"3":["10:15","11:00"]}',
            '{"periods":[{"period":3,"start":"10:15","end":"11:00"},'
            '{"period":1,"start":"08:25","end":"09:10"}]}',
            "第1节 08:25-09:10\n\n第3节：10:15～11:00",
            "1 08:25-09:10\n3 10:15-11:00",
        ]
        for data in inputs:
            with self.subTest(data=data):
                self.assertEqual(period_times.parse_period_times(data), expected)

    def test_malformed_profiles_fail_without_partial_results(self):
        invalid = [
            "第1节 08:00-08:45\n第2节 08:30-09:15",
            "第1节 08:45-08:00",
            "第1节 08:00-08:45\n无法读取第2节",
            '{"1":["08:00","08:45"],"1":["09:00","09:45"]}',
            '{"1":["08:00","08:45"],"01":["09:00","09:45"]}',
            '[{"period":1,"start":"08:00"}]',
            '{"1":["08:00:20","08:45:20"]}',
            '{"1":["08:00+08:00","08:45+08:00"]}',
            '{"1":["0800","0845"]}',
            '{"1":["24:00","25:00"]}',
            '{"0":["08:00","08:45"]}',
            "{}",
            "[]",
        ]
        for data in invalid:
            with self.subTest(data=data), self.assertRaises(period_times.ImportProblem):
                period_times.parse_period_times(data)


if __name__ == "__main__":
    unittest.main()
