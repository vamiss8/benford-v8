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
import zipfile

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


def build_workbook(path: str, amounts: list[float]) -> str:
    """Writes a workbook the way Excel writes one: shared strings, dates kept
    as plain serial numbers, and a style table saying which is which."""
    main = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
    rel = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
    pkg = "http://schemas.openxmlformats.org/package/2006/relationships"

    strings = ["date", "vendor", "amount", "Northwind Supplies"]
    # style 0 general, 1 custom date, 2 two decimals, 3 built-in date id 14
    styles = (f'<styleSheet xmlns="{main}">'
              '<numFmts count="1">'
              '<numFmt numFmtId="164" formatCode="dd/mm/yyyy"/></numFmts>'
              '<cellXfs count="4">'
              '<xf numFmtId="0"/><xf numFmtId="164"/>'
              '<xf numFmtId="2"/><xf numFmtId="14"/>'
              '</cellXfs></styleSheet>')

    rows = ['<row r="1">'
            '<c r="A1" t="s"><v>0</v></c>'
            '<c r="B1" t="s"><v>1</v></c>'
            '<c r="C1" t="s"><v>2</v></c>'
            '<c r="D1" t="s"><v>0</v></c>'
            '</row>']
    for i, amount in enumerate(amounts, start=2):
        rows.append(f'<row r="{i}">'
                    f'<c r="A{i}" s="1"><v>{45000 + i}</v></c>'
                    f'<c r="B{i}" t="s"><v>3</v></c>'
                    f'<c r="C{i}" s="2"><v>{amount}</v></c>'
                    f'<c r="D{i}" s="3"><v>{45300 + i}</v></c>'
                    '</row>')
    sheet1 = (f'<worksheet xmlns="{main}"><sheetData>'
              + "".join(rows) + '</sheetData></worksheet>')
    # inline string, a boolean, and a gap where column B should be
    sheet2 = (f'<worksheet xmlns="{main}"><sheetData>'
              '<row r="1"><c r="A1" t="inlineStr"><is><t>note</t></is></c>'
              '<c r="C1" t="s"><v>2</v></c></row>'
              '<row r="2"><c r="A2" t="b"><v>1</v></c><c r="C2"><v>17</v></c></row>'
              '</sheetData></worksheet>')

    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("_rels/.rels",
                         f'<Relationships xmlns="{pkg}"><Relationship Id="rId1" '
                         f'Type="{rel}/officeDocument" Target="xl/workbook.xml"/>'
                         '</Relationships>')
        archive.writestr("xl/workbook.xml",
                         f'<workbook xmlns="{main}" xmlns:r="{rel}"><sheets>'
                         '<sheet name="Q3 ledger" sheetId="1" r:id="rId1"/>'
                         '<sheet name="Notes" sheetId="2" r:id="rId2"/>'
                         '</sheets></workbook>')
        archive.writestr("xl/_rels/workbook.xml.rels",
                         f'<Relationships xmlns="{pkg}">'
                         f'<Relationship Id="rId1" Type="{rel}/worksheet" '
                         'Target="worksheets/sheet1.xml"/>'
                         f'<Relationship Id="rId2" Type="{rel}/worksheet" '
                         'Target="worksheets/sheet2.xml"/>'
                         '</Relationships>')
        archive.writestr("xl/styles.xml", styles)
        archive.writestr("xl/sharedStrings.xml",
                         f'<sst xmlns="{main}">'
                         + "".join(f"<si><t>{s}</t></si>" for s in strings)
                         + "</sst>")
        archive.writestr("xl/worksheets/sheet1.xml", sheet1)
        archive.writestr("xl/worksheets/sheet2.xml", sheet2)
    return path


class TestSpreadsheet(unittest.TestCase):
    """An .xlsx is a zip of XML, and the reader has to survive what Excel
    actually puts in there."""

    @classmethod
    def setUpClass(cls):
        cls.dir = tempfile.TemporaryDirectory()
        cls.amounts = [round(v, 2) for v in b.honest_amounts(120, random.Random(5))]
        cls.book = build_workbook(os.path.join(cls.dir.name, "ledger.xlsx"),
                                  cls.amounts)

    @classmethod
    def tearDownClass(cls):
        cls.dir.cleanup()

    def test_dates_are_not_mistaken_for_data(self):
        """A date in Excel is the number 45000-odd. Reading it as an amount
        would feed the law exactly the kind of number it cannot handle."""
        names = [s.name for s in b.series_from_xlsx(self.book, None)]
        self.assertEqual(names, ["Q3 ledger!amount", "Notes!amount"])

    def test_amounts_survive_the_round_trip(self):
        series = b.series_from_xlsx(self.book, None, sheet="Q3 ledger")
        self.assertEqual(series[0].name, "amount")
        self.assertEqual(series[0].values, self.amounts)

    def test_single_sheet_needs_no_prefix(self):
        series = b.series_from_xlsx(self.book, None, sheet="Notes")
        self.assertEqual([s.name for s in series], ["amount"])
        self.assertEqual(series[0].values, [17.0])

    def test_named_column(self):
        series = b.series_from_xlsx(self.book, "amount", sheet="Q3 ledger")
        self.assertEqual(len(series), 1)

    def test_unknown_sheet_is_reported(self):
        with self.assertRaises(b.SpreadsheetError):
            b.series_from_xlsx(self.book, None, sheet="nope")

    def test_not_a_workbook_is_reported(self):
        path = os.path.join(self.dir.name, "fake.xlsx")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("this is not a zip")
        with self.assertRaises(b.SpreadsheetError):
            b.series_from_xlsx(path, None)

    def test_date_format_detection(self):
        for code in ("dd/mm/yyyy", "[$-409]h:mm AM/PM", "mmm yy", "d.m.yy"):
            self.assertTrue(b._is_date_format(code), code)
        for code in ("General", "0.00", '#,##0.00" EUR"', "0.00%", "[Red]-0.00"):
            self.assertFalse(b._is_date_format(code), code)

    def test_column_letters(self):
        self.assertEqual(b._column_index("A1"), 0)
        self.assertEqual(b._column_index("Z9"), 25)
        self.assertEqual(b._column_index("AA1"), 26)
        self.assertEqual(b._column_index("BC12"), 54)
        self.assertIsNone(b._column_index(None))

    def test_cli_reads_a_workbook(self):
        with contextlib.redirect_stdout(io.StringIO()):
            code = b.main([self.book, "--sheet", "Q3 ledger", "--no-color"])
        self.assertEqual(code, 0)


class TestSmallSamples(unittest.TestCase):
    def test_short_series_is_not_scored(self):
        """Two numbers cannot disagree with the law, so they must not be
        accused of it."""
        result = b.analyze(b.Series("tiny", [17.0, 23.0]))
        score, flags = b.risk_score(result)
        self.assertEqual(score, 0)
        self.assertIn("too small", flags[0])

    def test_the_threshold_is_the_documented_one(self):
        rng = random.Random(11)
        values = [rng.uniform(100, 9999) for _ in range(b.MIN_SAMPLE)]
        self.assertGreater(b.risk_score(b.analyze(b.Series("n", values)))[0], 0)


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
