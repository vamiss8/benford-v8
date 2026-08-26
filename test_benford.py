#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Self-check for the Benford detector:  python test_benford.py"""
from __future__ import annotations

import contextlib
import io
import json
import math
import os
import random
import tempfile
import unittest

import benford as b


class TestParsing(unittest.TestCase):
    def test_plain(self):
        self.assertEqual(b.parse_number("42"), 42.0)
        self.assertEqual(b.parse_number(" -3.5 "), -3.5)

    def test_currency_and_spaces(self):
        self.assertEqual(b.parse_number("1 234,56 ₽"), 1234.56)
        self.assertEqual(b.parse_number("$1,234.56"), 1234.56)
        self.assertEqual(b.parse_number("12,5%"), 12.5)

    def test_accounting_negative(self):
        self.assertEqual(b.parse_number("(1 000,00)"), -1000.0)

    def test_thousands_vs_decimal(self):
        self.assertEqual(b.parse_number("1.234,56"), 1234.56)
        self.assertEqual(b.parse_number("1,234"), 1234.0)
        self.assertEqual(b.parse_number("1,23"), 1.23)

    def test_garbage(self):
        for junk in ("", "   ", None, "no data", "2025-05-23", "abc"):
            self.assertIsNone(b.parse_number(junk), junk)


class TestDigits(unittest.TestCase):
    def test_first(self):
        self.assertEqual(b.first_digit(0.00427), 4)
        self.assertEqual(b.first_digit(-95000), 9)
        self.assertEqual(b.first_digit(1e300), 1)
        self.assertIsNone(b.first_digit(0.0))

    def test_second(self):
        self.assertEqual(b.second_digit(0.00427), 2)
        self.assertEqual(b.second_digit(900), 0)
        self.assertEqual(b.second_digit(1234.56), 2)
        self.assertIsNone(b.second_digit(9), "a one-digit number has no second digit")

    def test_last_two(self):
        self.assertEqual(b.last_two(1234.99), 34)
        self.assertIsNone(b.last_two(7.5))

    def test_scale_invariance(self):
        """Changing units must not change the leading digit."""
        for value in (12.7, 340.0, 0.089):
            self.assertEqual(b.first_digit(value), b.first_digit(value * 1000))


class TestLaw(unittest.TestCase):
    def test_expected_sums_to_one(self):
        self.assertAlmostEqual(sum(b.FIRST_EXPECTED.values()), 1.0, places=12)
        self.assertAlmostEqual(sum(b.SECOND_EXPECTED.values()), 1.0, places=12)

    def test_known_values(self):
        self.assertAlmostEqual(b.FIRST_EXPECTED[1], 0.30103, places=5)
        self.assertAlmostEqual(b.FIRST_EXPECTED[9], 0.04576, places=5)

    def test_chi2_pvalue_matches_tables(self):
        # published critical values for 8 degrees of freedom
        self.assertAlmostEqual(b.chi2_pvalue(15.507, 8), 0.05, places=3)
        self.assertAlmostEqual(b.chi2_pvalue(20.090, 8), 0.01, places=3)
        self.assertAlmostEqual(b.chi2_pvalue(0.0, 8), 1.0, places=6)
        self.assertLess(b.chi2_pvalue(500.0, 8), 1e-20)

    def test_pvalue_monotone(self):
        previous = 1.0
        for chi2 in (1, 5, 10, 20, 40):
            current = b.chi2_pvalue(chi2, 8)
            self.assertLess(current, previous)
            previous = current


class TestDetection(unittest.TestCase):
    """The point of the whole thing: tell genuine data from invented data."""

    def test_fibonacci_conforms(self):
        test = b.first_digit_test(b.fibonacci(1000))
        self.assertLess(test.mad, 0.006, "Fibonacci must land on the law")
        self.assertEqual(test.verdict, "close conformity")

    def test_powers_of_two_conform(self):
        test = b.first_digit_test(b.powers(2, 1000))
        self.assertLess(test.mad, 0.006)

    def test_generators_survive_float_ceiling(self):
        """Asking for an absurdly long series must not crash anything."""
        self.assertTrue(all(math.isfinite(v) for v in b.fibonacci(5000)))
        self.assertTrue(all(math.isfinite(v) for v in b.powers(10, 5000)))
        self.assertLess(len(b.fibonacci(5000)), 5000)

    def test_uniform_random_fails(self):
        rng = random.Random(7)
        values = [rng.uniform(100, 9999) for _ in range(2000)]
        test = b.first_digit_test(values)
        self.assertGreater(test.mad, 0.015)
        self.assertLess(test.pvalue, 0.01)

    def test_honest_vs_cooked_scores(self):
        rng = random.Random(1)
        honest = b.analyze(b.Series("honest", b.honest_amounts(1500, rng)), 5000)
        cooked = b.analyze(b.Series("cooked", b.cooked_amounts(1500, rng)), 5000)
        self.assertLess(b.risk_score(honest)[0], 30,
                        "honest data must not raise an alarm")
        self.assertGreater(b.risk_score(cooked)[0], 70,
                           "invented data must get caught")

    def test_threshold_scan_finds_cluster(self):
        rng = random.Random(3)
        values = [4900.0] * 40 + [rng.uniform(1, 900) for _ in range(200)]
        scan = b.threshold_scan(values, 5000)
        self.assertEqual(scan["below"], 40)
        self.assertEqual(scan["above"], 0)

    def test_zero_and_inf_dropped(self):
        result = b.analyze(b.Series("dirty", [0.0, 0.0, 1.0, 2.0, 3.0]))
        self.assertEqual(result["n_used"], 3)


class TestLoaders(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)

    def _tmp(self, name: str, text: str) -> str:
        path = os.path.join(self.dir.name, name)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)
        return path

    def test_csv_picks_numeric_column_only(self):
        path = self._tmp("a.csv", "date;vendor;amount\n"
                                  "2025-01-01;Northwind Supplies;1 200,50\n"
                                  "2025-01-02;Harbor Logistics;930,00\n")
        series = b.series_from_csv(path, None)
        self.assertEqual([s.name for s in series], ["amount"])
        self.assertEqual(series[0].values, [1200.50, 930.00])

    def test_csv_named_column(self):
        path = self._tmp("b.csv", "code,amount\n7,10\n8,20\n")
        series = b.series_from_csv(path, "code")
        self.assertEqual(series[0].values, [7.0, 8.0])

    def test_csv_without_header(self):
        path = self._tmp("c.csv", "10,20\n30,40\n")
        series = b.series_from_csv(path, None)
        self.assertEqual(len(series), 2)
        self.assertEqual(series[0].values, [10.0, 30.0])

    def test_json_walks_nested(self):
        payload = '{"a": [1, 2], "b": {"c": "3,5"}, "d": true}'
        series = b.series_from_json(payload, "j")
        self.assertEqual(sorted(series[0].values), [1.0, 2.0, 3.5])

    def test_text_extraction(self):
        series = b.series_from_text("total 1 234,50 plus another 99 cents", "t")
        self.assertEqual(series[0].values, [1234.50, 99.0])


class TestCLI(unittest.TestCase):
    def test_why_exits_clean(self):
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(b.main(["--why", "--no-color"]), 0)

    def test_fib_exits_clean(self):
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(b.main(["--fib", "200", "--no-color"]), 0)

    def test_json_output_is_valid(self):
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = b.main(["samples/invoices_cooked.csv", "--json",
                           "--limit", "5000"])
        self.assertEqual(code, 0)
        payload = json.loads(buffer.getvalue())
        self.assertEqual(payload["name"], "amount")
        self.assertGreater(payload["risk_score"], 70)
        self.assertAlmostEqual(sum(payload["first_digit"]["expected_p"].values()),
                               1.0, places=4)

    def test_cooked_file_returns_alarm_code(self):
        with contextlib.redirect_stdout(io.StringIO()):
            code = b.main(["samples/invoices_cooked.csv", "--no-color"])
        self.assertEqual(code, 3, "suspicious data must exit with code 3")

    def test_honest_file_returns_clean_code(self):
        with contextlib.redirect_stdout(io.StringIO()):
            code = b.main(["samples/invoices_honest.csv", "--no-color"])
        self.assertEqual(code, 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
