#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
BENFORD DETECTOR  —  pocket-sized digital forensics.

Benford's law: in numbers that come from the real world the leading digit is
not uniform. A one shows up about 30.1% of the time, a nine only 4.6%.
People who invent numbers off the top of their head don't know this and
spread the digits out almost evenly. That is how cooked books get caught.

No dependencies — standard library only.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import os
import random
import re
import sys
import xml.etree.ElementTree as ET
import zipfile
from collections import Counter
from dataclasses import dataclass, field

__version__ = "1.0.0"

# Windows console: UTF-8 output and ANSI colors

def _prepare_console() -> None:
    if os.name == "nt":
        try:
            import ctypes
            kernel = ctypes.windll.kernel32
            kernel.SetConsoleOutputCP(65001)
            handle = kernel.GetStdHandle(-11)
            mode = ctypes.c_uint32()
            if kernel.GetConsoleMode(handle, ctypes.byref(mode)):
                kernel.SetConsoleMode(handle, mode.value | 0x0004)  # VT processing
        except Exception:
            pass
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


class C:
    """Palette. Switched off by --no-color or when piped somewhere."""
    enabled = True

    RESET = "\033[0m"; BOLD = "\033[1m"; DIM = "\033[2m"
    RED = "\033[31m"; GREEN = "\033[32m"; YELLOW = "\033[33m"
    BLUE = "\033[34m"; MAGENTA = "\033[35m"; CYAN = "\033[36m"; GREY = "\033[90m"

    @classmethod
    def p(cls, text: str, *codes: str) -> str:
        if not cls.enabled or not codes:
            return text
        return "".join(codes) + text + cls.RESET


# --------------------------------------------------------------------------
# The math behind the law
# --------------------------------------------------------------------------

# P(leading digit = d) = log10(1 + 1/d)
FIRST_EXPECTED = {d: math.log10(1 + 1 / d) for d in range(1, 10)}

# P(second digit = d) = sum_{k=1..9} log10(1 + 1/(10k + d))
SECOND_EXPECTED = {
    d: sum(math.log10(1 + 1 / (10 * k + d)) for k in range(1, 10)) for d in range(10)
}

# Nigrini's thresholds for MAD, the mean absolute deviation.
MAD_FIRST = ((0.006, "close conformity"),
             (0.012, "acceptable conformity"),
             (0.015, "marginal conformity"),
             (float("inf"), "NONCONFORMITY"))
MAD_SECOND = ((0.008, "close conformity"),
              (0.010, "acceptable conformity"),
              (0.012, "marginal conformity"),
              (float("inf"), "NONCONFORMITY"))


def _gamma_q(s: float, x: float) -> float:
    """Regularized upper incomplete gamma Q(s, x). Needed for the p-value."""
    if x < 0 or s <= 0:
        raise ValueError("bad arguments")
    if x == 0:
        return 1.0
    if x < s + 1.0:                       # series expansion for P(s, x)
        ap, total, term = s, 1.0 / s, 1.0 / s
        for _ in range(1000):
            ap += 1
            term *= x / ap
            total += term
            if abs(term) < abs(total) * 1e-14:
                break
        return 1.0 - total * math.exp(-x + s * math.log(x) - math.lgamma(s))
    # continued fraction for Q(s, x)
    tiny = 1e-300
    b, c, d = x + 1.0 - s, 1.0 / tiny, 1.0 / (x + 1.0 - s)
    h = d
    for i in range(1, 1000):
        an = -i * (i - s)
        b += 2.0
        d = an * d + b
        if abs(d) < tiny:
            d = tiny
        c = b + an / c
        if abs(c) < tiny:
            c = tiny
        d = 1.0 / d
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < 1e-14:
            break
    return h * math.exp(-x + s * math.log(x) - math.lgamma(s))


def chi2_pvalue(chi2: float, df: int) -> float:
    """Odds of seeing a deviation this large purely by chance."""
    if chi2 <= 0:
        return 1.0
    return _gamma_q(df / 2.0, chi2 / 2.0)


def verdict_for(mad: float, table) -> str:
    for limit, label in table:
        if mad < limit:
            return label
    return table[-1][1]


# --------------------------------------------------------------------------
# Number parsing
# --------------------------------------------------------------------------

_TRASH = str.maketrans("", "", "   '`$€£¥₽%")
NUM_IN_TEXT = re.compile(r"[-+(]?\d[\d  ',.]*\d|\d")

# Past this point a float no longer stores its integer part exactly, so any
# talk of "the last digits" becomes meaningless.
EXACT_LIMIT = 1e12


def parse_number(raw: str) -> float | None:
    """Forgiving parser: '1 234,56 EUR', '(1,234.56)', '-12.5%' all work."""
    if raw is None:
        return None
    text = str(raw).strip()
    if not text:
        return None
    negative = text.startswith("(") and text.endswith(")")   # accounting minus
    text = text.strip("()").translate(_TRASH)
    if not text or not any(ch.isdigit() for ch in text):
        return None
    # Pick the decimal separator: whichever sits further right wins.
    dot, comma = text.rfind("."), text.rfind(",")
    if dot >= 0 and comma >= 0:
        decimal, thousands = (".", ",") if dot > comma else (",", ".")
        text = text.replace(thousands, "").replace(decimal, ".")
    elif comma >= 0:
        tail = text[comma + 1:]
        text = text.replace(",", "" if (len(tail) == 3 and comma > 0) else ".")
    try:
        value = float(text)
    except ValueError:
        return None
    if not math.isfinite(value):
        return None
    return -value if negative else value


def significand(value: float) -> str:
    """Significant digits: -0.00427 -> '427', 900 -> '900', 9 -> '9'.

    Uses the shortest float repr, which never pads zeros the number does not
    actually have — otherwise a plain 9 would appear to have a second digit.
    """
    magnitude = abs(value)
    if magnitude == 0 or not math.isfinite(magnitude):
        return ""
    mantissa = repr(magnitude).split("e")[0].split("E")[0]
    if "." in mantissa:
        mantissa = mantissa.rstrip("0").rstrip(".")
    return mantissa.replace(".", "").lstrip("0")


def first_digit(value: float) -> int | None:
    digits = significand(value)
    return int(digits[0]) if digits else None


def second_digit(value: float) -> int | None:
    digits = significand(value)
    return int(digits[1]) if len(digits) >= 2 else None


def last_two(value: float) -> int | None:
    """Last two digits of the integer part — catches round, invented amounts."""
    whole = int(abs(value))
    return whole % 100 if whole >= 10 else None


# --------------------------------------------------------------------------
# Data sources
# --------------------------------------------------------------------------

@dataclass
class Series:
    name: str
    values: list[float] = field(default_factory=list)

    def __len__(self) -> int:
        return len(self.values)


def _numeric_share(cells: list[str]) -> float:
    filled = [c for c in cells if str(c).strip()]
    if not filled:
        return 0.0
    parsed = sum(1 for c in filled if parse_number(c) is not None)
    return parsed / len(filled)


def series_from_rows(rows: list[list[str]], wanted: str | None,
                     min_share: float = 0.8, prefix: str = "") -> list[Series]:
    """Turns a rectangle of cells into one series per numeric column.

    Shared by every table source, so a spreadsheet and a CSV pick their
    columns by exactly the same rule.
    """
    rows = [r for r in rows if any(str(c).strip() for c in r)]
    if not rows:
        return []

    header = rows[0]
    body = rows[1:]
    # Is that first row a header? If it is mostly numeric, it is not.
    if body and _numeric_share(header) > 0.5:
        body = rows
        header = [f"column {i + 1}" for i in range(len(rows[0]))]

    found: list[Series] = []
    for index, name in enumerate(header):
        cells = [r[index] for r in body if index < len(r)]
        if not cells:
            continue
        title = (str(name).strip() or f"column {index + 1}")
        if wanted is not None:
            if title.lower() != wanted.lower() and str(index + 1) != wanted:
                continue
        elif _numeric_share(cells) < min_share:
            continue
        values = [v for v in (parse_number(c) for c in cells) if v is not None]
        if values:
            found.append(Series(prefix + title, values))
    return found


def series_from_csv(path: str, wanted: str | None, min_share: float = 0.8) -> list[Series]:
    with open(path, newline="", encoding="utf-8-sig", errors="replace") as fh:
        sample = fh.read(8192)
        fh.seek(0)
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
        except csv.Error:
            dialect = csv.excel
        rows = list(csv.reader(fh, dialect))
    return series_from_rows(rows, wanted, min_share)


# --------------------------------------------------------------------------
# Spreadsheets
#
# An .xlsx file is a zip of XML, so the standard library is enough to read
# one and the tool stays dependency free.
# --------------------------------------------------------------------------

SHEET_NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
REL_NS = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"

# Built-in number formats that mean a date or a time. Excel stores those as
# plain numbers, and dates are exactly what Benford's law must never see.
DATE_FORMAT_IDS = set(range(14, 23)) | set(range(27, 37)) | set(range(45, 48)) \
    | set(range(50, 59))

_FORMAT_NOISE = re.compile(r'\[[^\]]*\]|"[^"]*"|\\.')
CELL_LETTERS = re.compile(r"([A-Za-z]+)")


class SpreadsheetError(Exception):
    """The file claims to be a workbook but cannot be read as one."""


def _is_date_format(code: str) -> bool:
    """A format code is a date if anything is left of y/m/d/h/s once the
    colours, literals and escapes are stripped out."""
    return any(ch in "ymdhs" for ch in _FORMAT_NOISE.sub("", code).lower())


def _date_style_indexes(archive: zipfile.ZipFile) -> set[int]:
    """Which style slots format their number as a date."""
    try:
        root = ET.fromstring(archive.read("xl/styles.xml"))
    except (KeyError, ET.ParseError):
        return set()
    custom = {}
    for entry in root.iter(f"{SHEET_NS}numFmt"):
        try:
            custom[int(entry.get("numFmtId", -1))] = entry.get("formatCode", "")
        except ValueError:
            continue
    formats = root.find(f"{SHEET_NS}cellXfs")
    if formats is None:
        return set()
    dated = set()
    for index, style in enumerate(formats):
        try:
            format_id = int(style.get("numFmtId", 0))
        except ValueError:
            continue
        if format_id in DATE_FORMAT_IDS or _is_date_format(custom.get(format_id, "")):
            dated.add(index)
    return dated


def _shared_strings(archive: zipfile.ZipFile) -> list[str]:
    try:
        root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
    except (KeyError, ET.ParseError):
        return []
    # A string can be split into runs, so gather every <t> under the entry.
    return ["".join(node.text or "" for node in item.iter(f"{SHEET_NS}t"))
            for item in root.iter(f"{SHEET_NS}si")]


def _worksheets(archive: zipfile.ZipFile) -> list[tuple[str, str]]:
    """Sheet names in workbook order, paired with their path inside the zip."""
    names = set(archive.namelist())
    try:
        book = ET.fromstring(archive.read("xl/workbook.xml"))
    except (KeyError, ET.ParseError):
        book = None

    targets = {}
    try:
        rels = ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
        for rel in rels:
            targets[rel.get("Id")] = rel.get("Target", "")
    except (KeyError, ET.ParseError):
        pass

    found: list[tuple[str, str]] = []
    if book is not None:
        for sheet in book.iter(f"{SHEET_NS}sheet"):
            target = targets.get(sheet.get(f"{REL_NS}id"), "")
            path = target.lstrip("/")
            if path and not path.startswith("xl/"):
                path = "xl/" + path
            if path in names:
                found.append((sheet.get("name") or "sheet", path))
    if found:
        return found
    # No workbook part, or relationships we could not follow: take the sheets
    # as they lie in the archive.
    return [(os.path.splitext(os.path.basename(p))[0], p)
            for p in sorted(names) if p.startswith("xl/worksheets/sheet")
            and p.endswith(".xml")]


def _column_index(ref: str | None) -> int | None:
    """'BC12' -> 54. Cells may be missing, so the position is what places them."""
    match = CELL_LETTERS.match(ref or "")
    if not match:
        return None
    index = 0
    for letter in match.group(1).upper():
        index = index * 26 + (ord(letter) - 64)
    return index - 1


def _sheet_rows(archive: zipfile.ZipFile, path: str, strings: list[str],
                date_styles: set[int]):
    """Streams a worksheet row by row, so a big export never lands in memory
    all at once."""
    with archive.open(path) as handle:
        for _, element in ET.iterparse(handle, ("end",)):
            if element.tag != f"{SHEET_NS}row":
                continue
            cells: dict[int, str] = {}
            for cell in element:
                index = _column_index(cell.get("r"))
                if index is None:
                    continue
                kind = cell.get("t")
                if kind == "s":
                    node = cell.find(f"{SHEET_NS}v")
                    try:
                        text = strings[int(node.text)] if node is not None else ""
                    except (ValueError, IndexError):
                        text = ""
                elif kind == "inlineStr":
                    text = "".join(n.text or "" for n in cell.iter(f"{SHEET_NS}t"))
                elif kind == "b":
                    text = ""                      # TRUE/FALSE is not a measurement
                else:
                    node = cell.find(f"{SHEET_NS}v")
                    text = (node.text or "") if node is not None else ""
                    try:
                        if int(cell.get("s", -1)) in date_styles:
                            text = ""              # a date serial, not an amount
                    except ValueError:
                        pass
                if text:
                    cells[index] = text
            yield [cells.get(i, "") for i in range(max(cells) + 1)] if cells else []
            element.clear()


def series_from_xlsx(path: str, wanted: str | None, sheet: str | None = None,
                     min_share: float = 0.8) -> list[Series]:
    try:
        archive = zipfile.ZipFile(path)
    except (zipfile.BadZipFile, OSError) as exc:
        raise SpreadsheetError(f"not a readable workbook: {exc}") from exc

    with archive:
        sheets = _worksheets(archive)
        if sheet is not None:
            sheets = [(name, p) for name, p in sheets if name.lower() == sheet.lower()]
            if not sheets:
                raise SpreadsheetError(f"no sheet named '{sheet}'")
        strings = _shared_strings(archive)
        date_styles = _date_style_indexes(archive)

        found: list[Series] = []
        for name, sheet_path in sheets:
            rows = list(_sheet_rows(archive, sheet_path, strings, date_styles))
            prefix = "" if len(sheets) == 1 else f"{name}!"
            found.extend(series_from_rows(rows, wanted, min_share, prefix))
    return found


def series_from_text(text: str, name: str) -> list[Series]:
    values = [v for v in (parse_number(m.group()) for m in NUM_IN_TEXT.finditer(text))
              if v is not None]
    return [Series(name, values)] if values else []


def series_from_json(text: str, name: str) -> list[Series]:
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return []
    found: list[float] = []

    def walk(node):
        if isinstance(node, bool):
            return
        if isinstance(node, (int, float)):
            found.append(float(node))
        elif isinstance(node, dict):
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)
        elif isinstance(node, str):
            value = parse_number(node)
            if value is not None:
                found.append(value)

    walk(data)
    return [Series(name, found)] if found else []


def load_series(path: str, column: str | None,
                sheet: str | None = None) -> list[Series]:
    if path == "-":
        return series_from_text(sys.stdin.read(), "stdin")
    lower = path.lower()
    if lower.endswith((".xlsx", ".xlsm")):
        return series_from_xlsx(path, column, sheet)
    if lower.endswith((".csv", ".tsv")):
        return series_from_csv(path, column)
    with open(path, encoding="utf-8-sig", errors="replace") as fh:
        text = fh.read()
    name = os.path.basename(path)
    if lower.endswith(".json"):
        return series_from_json(text, name) or series_from_text(text, name)
    return series_from_text(text, name)


# --------------------------------------------------------------------------
# Analysis
# --------------------------------------------------------------------------

@dataclass
class DigitTest:
    title: str
    digits: list[int]
    observed: dict[int, int]
    expected_p: dict[int, float]
    n: int
    mad: float
    verdict: str
    chi2: float
    df: int
    pvalue: float
    z: dict[int, float]


def digit_test(values, extractor, expected_p, title, mad_table) -> DigitTest | None:
    digits = sorted(expected_p)
    seen = [d for d in (extractor(v) for v in values) if d is not None]
    n = len(seen)
    if n == 0:
        return None
    observed = Counter(seen)
    mad = sum(abs(observed.get(d, 0) / n - expected_p[d]) for d in digits) / len(digits)

    chi2 = 0.0
    z: dict[int, float] = {}
    for d in digits:
        expected_n = expected_p[d] * n
        chi2 += (observed.get(d, 0) - expected_n) ** 2 / expected_n
        share, expected_share = observed.get(d, 0) / n, expected_p[d]
        sigma = math.sqrt(expected_share * (1 - expected_share) / n)
        # continuity correction keeps small samples from crying wolf
        diff = abs(share - expected_share)
        correction = 1 / (2 * n)
        z[d] = (diff - correction) / sigma if diff > correction and sigma else 0.0

    df = len(digits) - 1
    return DigitTest(title, digits, dict(observed), expected_p, n, mad,
                     verdict_for(mad, mad_table), chi2, df, chi2_pvalue(chi2, df), z)


def first_digit_test(values) -> DigitTest | None:
    return digit_test(values, first_digit, FIRST_EXPECTED, "Leading digit", MAD_FIRST)


def second_digit_test(values) -> DigitTest | None:
    return digit_test(values, second_digit, SECOND_EXPECTED, "Second digit", MAD_SECOND)


def last_two_test(values) -> dict | None:
    """The last two digits should be uniform. People favour 00, 50 and 99."""
    exact = [v for v in values if abs(v) < EXACT_LIMIT]
    seen = [d for d in (last_two(v) for v in exact) if d is not None]
    n = len(seen)
    if n < 100:
        return None
    counts = Counter(seen)
    expected_n = n / 100
    chi2 = sum((counts.get(d, 0) - expected_n) ** 2 / expected_n for d in range(100))
    return {"n": n, "chi2": chi2, "df": 99, "pvalue": chi2_pvalue(chi2, 99),
            "top": [(d, c, c / n) for d, c in counts.most_common(5)],
            "expected_share": 0.01}


def round_number_bias(values) -> dict:
    """Share of suspiciously tidy numbers. Real amounts are rarely round.

    Roundness is measured on the integer part: cents only get in the way,
    while the last digit of the whole units is uniform in genuine data.
    The share of amounts with no cents at all is reported separately —
    invented figures push it through the roof.
    """
    usable = [abs(v) for v in values if 10 <= abs(v) < EXACT_LIMIT]
    n = len(usable)
    if not n:
        return {"n": 0}
    wholes = [int(v) for v in usable]

    def share(step):
        return sum(1 for w in wholes if w % step == 0) / n

    no_cents = sum(1 for v in usable if v == int(v)) / n
    return {"n": n, "ends_0": share(10), "ends_00": share(100),
            "ends_000": share(1000), "no_cents": no_cents,
            "expected_0": 0.10, "expected_00": 0.01, "expected_000": 0.001}


def duplicates(values, top: int = 5) -> list[tuple[float, int]]:
    counts = Counter(values)
    return [(value, count) for value, count in counts.most_common(top) if count > 1]


def threshold_scan(values, limit: float, band: float = 0.10) -> dict:
    """The classic embezzlement tell: amounts creeping just under the
    approval limit so nobody has to sign them off."""
    below = sum(1 for v in values if limit * (1 - band) <= abs(v) < limit)
    above = sum(1 for v in values if limit <= abs(v) <= limit * (1 + band))
    ratio = below / above if above else float("inf") if below else 0.0
    return {"limit": limit, "band": band, "below": below, "above": above,
            "ratio": ratio}


def analyze(series: Series, limit: float | None = None) -> dict:
    values = [v for v in series.values if v != 0 and math.isfinite(v)]
    return {
        "name": series.name,
        "n_total": len(series.values),
        "n_used": len(values),
        "first": first_digit_test(values),
        "second": second_digit_test(values),
        "last_two": last_two_test(values),
        "round": round_number_bias(values),
        "dups": duplicates(values),
        "threshold": threshold_scan(values, limit) if limit else None,
    }


# Under this many values the law is drowned out by ordinary chance, so
# scoring the deviation would only manufacture false alarms.
MIN_SAMPLE = 100


def risk_score(result: dict) -> tuple[int, list[str]]:
    """0..100. A reason to look closer, not a verdict."""
    if result["n_used"] < MIN_SAMPLE:
        return 0, [f"sample too small to score: {result['n_used']} values, "
                   f"{MIN_SAMPLE} needed"]

    score, flags = 0, []

    first = result["first"]
    if first:
        if first.mad >= 0.015:
            score += 45
            flags.append("leading digit is far off the law")
        elif first.mad >= 0.012:
            score += 28
            flags.append("leading digit sits on the edge of acceptable")
        elif first.mad >= 0.006:
            score += 12
        if first.pvalue < 0.01 and first.n >= 100:
            score += 10
            flags.append("chi-square rejects conformity (p < 0.01)")

    second = result["second"]
    if second and second.mad >= 0.012:
        score += 12
        flags.append("second digit drifts as well")

    rounds = result["round"]
    if rounds.get("n", 0) >= 50:
        if rounds["ends_00"] > 0.05:
            score += 15
            flags.append(f"{rounds['ends_00']:.0%} of amounts are round hundreds "
                         f"instead of ~1%")
        elif rounds["ends_0"] > 0.25:
            score += 8
            flags.append(f"{rounds['ends_0']:.0%} of amounts are round tens "
                         f"instead of ~10%")

    tail = result["last_two"]
    if tail and tail["pvalue"] < 0.01:
        score += 10
        flags.append("last two digits are not uniformly spread")

    threshold = result["threshold"]
    if threshold and threshold["ratio"] > 2 and threshold["below"] >= 5:
        score += 15
        flags.append(f"amounts pile up under the {threshold['limit']:g} limit "
                     f"({threshold['below']} against {threshold['above']})")

    return min(score, 100), flags


# --------------------------------------------------------------------------
# Output
# --------------------------------------------------------------------------

TRACK = 34  # width of the scale, in characters


def _bar(observed_share: float, expected_share: float, scale: float) -> str:
    """Bar for the observed share, plus a marker where the law expected it."""
    cells = ["·"] * TRACK
    filled = min(TRACK, max(0, round(observed_share / scale * TRACK)))
    for i in range(filled):
        cells[i] = "█"
    mark = min(TRACK - 1, max(0, round(expected_share / scale * TRACK) - 1))
    cells[mark] = C.p("┃", C.YELLOW) if cells[mark] == "·" else C.p("┃", C.BOLD, C.YELLOW)
    return "".join(cells)


def _z_color(z: float) -> str:
    return C.GREEN if z < 1.96 else (C.YELLOW if z < 2.58 else C.RED)


def print_digit_test(test: DigitTest) -> None:
    print(C.p(f"  {test.title}", C.BOLD, C.CYAN) + C.p(f"   (n = {test.n})", C.GREY))
    scale = max(max(test.observed.get(d, 0) / test.n for d in test.digits),
                max(test.expected_p.values())) * 1.08
    for d in test.digits:
        observed = test.observed.get(d, 0) / test.n
        expected = test.expected_p[d]
        z = test.z[d]
        line = (f"   {d} │{_bar(observed, expected, scale)}│ "
                f"{observed * 100:5.1f}%  "
                + C.p(f"expected {expected * 100:4.1f}%", C.GREY)
                + "  " + C.p(f"{(observed - expected) * 100:+5.1f} pp", _z_color(z)))
        if z >= 2.58:
            line += C.p("  <- anomaly", C.RED, C.BOLD)
        elif z >= 1.96:
            line += C.p("  <- suspicious", C.YELLOW)
        print(line)
    tone = C.GREEN if test.mad < 0.012 else (C.YELLOW if test.mad < 0.015 else C.RED)
    print(f"   MAD = {test.mad:.4f} -> " + C.p(test.verdict, tone, C.BOLD)
          + C.p(f"   |   chi2 = {test.chi2:.1f}, p = {test.pvalue:.4f}", C.GREY))
    print()


def _meter(score: int) -> str:
    width = 24
    filled = round(score / 100 * width)
    color = C.GREEN if score < 30 else (C.YELLOW if score < 60 else C.RED)
    return C.p("█" * filled, color) + C.p("░" * (width - filled), C.GREY)


def print_report(result: dict, *, show_second: bool, show_last2: bool) -> None:
    print()
    print(C.p("=" * 78, C.BLUE))
    print(C.p(f"  {result['name']}", C.BOLD)
          + C.p(f"   —  {result['n_used']} numbers in play "
                f"(of {result['n_total']})", C.GREY))
    print(C.p("=" * 78, C.BLUE))
    print()

    if result["n_used"] < MIN_SAMPLE:
        print(C.p(f"  Too little data: Benford's law needs about {MIN_SAMPLE} "
                  "numbers before it means anything.", C.YELLOW))
        print()

    if result["first"]:
        print_digit_test(result["first"])
    if show_second and result["second"]:
        print_digit_test(result["second"])

    rounds = result["round"]
    if rounds.get("n"):
        print(C.p("  Round numbers", C.BOLD, C.CYAN)
              + C.p(f"   (over {rounds['n']} values)", C.GREY))
        for key, label, expected in (("ends_0", "whole part ends 0", 0.10),
                                     ("ends_00", "whole part ends 00", 0.01),
                                     ("ends_000", "whole part ends 000", 0.001)):
            got = rounds[key]
            hot = got > expected * 3 and got > 0.02
            print(f"   {label:<20} {got * 100:5.1f}%  "
                  + C.p(f"expected ~{expected * 100:.1f}%", C.GREY)
                  + (C.p("   <- too many", C.RED) if hot else ""))
        print(f"   {'no cents at all':<20} {rounds['no_cents'] * 100:5.1f}%  "
              + C.p("for reference: invented amounts score high here", C.GREY))
        print()

    tail = result["last_two"]
    if show_last2 and tail:
        print(C.p("  Last two digits", C.BOLD, C.CYAN)
              + C.p("   (should be uniform, ~1.0% each)", C.GREY))
        for value, count, share in tail["top"]:
            hot = share > 0.03
            print(f"   ..{value:02d}  {count:>5} times  {share * 100:5.1f}%"
                  + (C.p("   <- far too often", C.RED) if hot else ""))
        print(C.p(f"   chi2 = {tail['chi2']:.1f}, p = {tail['pvalue']:.4f}", C.GREY))
        print()

    dups = result["dups"]
    if dups:
        print(C.p("  Repeated values", C.BOLD, C.CYAN))
        for value, count in dups:
            line = (f"   {value:>18,.2f}  x{count:<3}"
                    + (C.p("   <- copy-paste?", C.YELLOW) if count >= 4 else ""))
            print(line.rstrip())
        print()

    threshold = result["threshold"]
    if threshold:
        print(C.p(f"  Approval limit {threshold['limit']:g}", C.BOLD, C.CYAN))
        print(f"   just below: {threshold['below']}    "
              f"just above: {threshold['above']}")
        if threshold["ratio"] > 2 and threshold["below"] >= 5:
            print(C.p("   <- amounts are clearly being shaved to fit",
                      C.RED, C.BOLD))
        print()

    score, flags = risk_score(result)
    label = ("data looks alive" if score < 30 else
             "worth a closer look" if score < 60 else
             "data smells handmade")
    tone = C.GREEN if score < 30 else (C.YELLOW if score < 60 else C.RED)
    print(C.p("  Verdict", C.BOLD, C.CYAN))
    print(f"   {_meter(score)}  {score}/100  " + C.p(label, tone, C.BOLD))
    for flag in flags:
        print(C.p(f"     - {flag}", C.GREY))
    if not flags:
        print(C.p("     - nothing worth mentioning turned up", C.GREY))
    print()
    print(C.p("   This is a flashlight, not a verdict: Benford's law gives you "
              "a reason", C.GREY))
    print(C.p("   to check, never a proof of fraud.", C.GREY))
    print()


def result_to_json(result: dict) -> dict:
    def pack(test):
        if not test:
            return None
        return {"n": test.n, "mad": round(test.mad, 6), "verdict": test.verdict,
                "chi2": round(test.chi2, 4), "df": test.df,
                "pvalue": round(test.pvalue, 6),
                "observed": {str(d): test.observed.get(d, 0) for d in test.digits},
                "expected_p": {str(d): round(p, 6)
                               for d, p in test.expected_p.items()},
                "z": {str(d): round(value, 4) for d, value in test.z.items()}}

    score, flags = risk_score(result)
    return {"name": result["name"], "n_total": result["n_total"],
            "n_used": result["n_used"], "first_digit": pack(result["first"]),
            "second_digit": pack(result["second"]),
            "last_two": result["last_two"], "round_numbers": result["round"],
            "duplicates": [{"value": value, "count": count}
                           for value, count in result["dups"]],
            "threshold": result["threshold"],
            "risk_score": score, "flags": flags}


# --------------------------------------------------------------------------
# Data generators for the demo and the test suite
# --------------------------------------------------------------------------

FLOAT_CEILING = 10 ** 300  # beyond this a float overflows


def honest_amounts(n: int, rng: random.Random) -> list[float]:
    """Genuine invoice totals: price x quantity x adjustment.
    A product of independent quantities drifts towards Benford all on its
    own — that is the real secret behind the law."""
    out = []
    for _ in range(n):
        price = rng.lognormvariate(4.2, 1.9)
        quantity = rng.choice([1, 1, 1, 2, 3, 4, 7, 12, 24, 50, 144])
        adjustment = rng.uniform(0.85, 1.35)
        out.append(round(price * quantity * adjustment, 2))
    return out


def cooked_amounts(n: int, rng: random.Random, limit: float = 5000.0) -> list[float]:
    """Amounts invented by a human: digits spread roughly evenly, plenty of
    round figures, and a crowd sitting right under the approval limit."""
    out = []
    for _ in range(n):
        roll = rng.random()
        if roll < 0.22:                                  # shaved under the limit
            value = rng.uniform(limit * 0.88, limit * 0.999)
        elif roll < 0.45:                                # a tidy number off the cuff
            value = rng.choice([50, 100, 250, 500, 1000, 1500, 2000, 2500]) * \
                    rng.choice([1, 1, 2, 3])
        else:                                            # uniform invention
            value = rng.uniform(100, 9999)
        cents = rng.choice([0, 0, 0, 50, 99, rng.randrange(100)])
        out.append(round(int(value) + cents / 100, 2))
    return out


def fibonacci(n: int) -> list[float]:
    a, b, out = 1, 1, []
    for _ in range(n):
        if a > FLOAT_CEILING:
            break
        out.append(float(a))
        a, b = b, a + b
    return out


def powers(base: int, n: int) -> list[float]:
    out = []
    for k in range(1, n + 1):
        value = base ** k
        if value > FLOAT_CEILING:
            break
        out.append(float(value))
    return out


WHY = """
  WHY THE ONE KEEPS WINNING

  Take a deposit growing at 10% a year. Going from 1000 to 2000 takes seven
  years; getting from 9000 to 10000 takes a single year. A number simply
  spends longer with a one in front — the gap from 1 to 2 is a doubling,
  while the gap from 9 to 10 is a mere 11%.

  Everything that grows by multiplication rather than addition behaves this
  way: prices, city populations, river lengths, electricity bills, file
  sizes, Fibonacci numbers. Hence the formula:

     P(leading digit = d) = log10(1 + 1/d)

     1 - 30.1%      4 - 9.7%       7 - 5.8%
     2 - 17.6%      5 - 7.9%       8 - 5.1%
     3 - 12.5%      6 - 6.7%       9 - 4.6%

  The law does not care about units. Convert dollars to yen or metres to
  feet and the distribution of leading digits will not budge. That property
  is called scale invariance, and it is what makes the law so sturdy.

  WHERE IT DOES NOT APPLY

  - numbers with artificial bounds: human height, body temperature, IQ;
  - identifiers people hand out: phone numbers, SKUs, tax IDs, house numbers;
  - samples under a hundred values, where randomness still rules.

  HOW IT GETS USED

  Auditors run ledger entries through the test: genuine figures obey the
  law, hand-typed ones almost never do, because a person spreads the digits
  out roughly evenly. The same trick has been aimed at corporate filings and
  at the macroeconomic statistics of entire countries. Remember what a
  mismatch actually means: "look closer here", not "this is fraud".
"""


def run_demo(seed: int = 20260826) -> None:
    rng = random.Random(seed)
    print()
    print(C.p("  DEMO: two sets of invoices. One is real, the other was "
              "made up by a human.", C.BOLD, C.MAGENTA))
    print(C.p("  They look alike to the eye. Let's ask the leading digit.",
              C.GREY))

    for title, values, limit in (
        ("Invoices from a real process", honest_amounts(900, rng), 5000.0),
        ("Invoices invented by a human", cooked_amounts(900, rng), 5000.0),
        ("The first 900 Fibonacci numbers", fibonacci(900), None),
    ):
        print_report(analyze(Series(title, values), limit),
                     show_second=False, show_last2=False)

    print(C.p("  The moral: people are terrible at inventing random numbers. "
              "They deal", C.BOLD))
    print(C.p("  the digits out evenly, love round figures, hug the limits — "
              "and get caught.", C.BOLD))
    print()


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

BANNER = r"""
   ___                    _              _
  | _ ) ___ _ _  / _|___ _ _ __| |  DIGITAL FORENSICS
  | _ \/ -_) ' \|  _/ _ \ '_/ _` |  the leading digit tells on you
  |___/\___|_||_|_| \___/_| \__,_|
"""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="benford",
        description="Checks whether numbers obey Benford's law.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""examples:
  python benford.py samples/invoices_honest.csv
  python benford.py samples/invoices_cooked.csv --limit 5000 --second --last2
  python benford.py ledger.xlsx --sheet Q3 --column amount
  python benford.py report.csv --column amount --json
  cat numbers.txt | python benford.py -
  python benford.py --demo
  python benford.py --why""")
    parser.add_argument("files", nargs="*",
                        help="xlsx / CSV / JSON / plain text, or - for stdin")
    parser.add_argument("-c", "--column",
                        help="table column, by name or by number")
    parser.add_argument("-s", "--sheet",
                        help="worksheet to read, by name (xlsx only)")
    parser.add_argument("-l", "--limit", type=float,
                        help="approval limit: look for amounts piling up below it")
    parser.add_argument("-2", "--second", action="store_true",
                        help="add the second-digit test")
    parser.add_argument("--last2", action="store_true",
                        help="add the last-two-digits test")
    parser.add_argument("-a", "--all", action="store_true", help="run every test")
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    parser.add_argument("--demo", action="store_true",
                        help="watch the law at work on three data sets")
    parser.add_argument("--why", action="store_true",
                        help="explain why the law works")
    parser.add_argument("--fib", type=int, metavar="N",
                        help="check the first N Fibonacci numbers")
    parser.add_argument("--no-color", action="store_true", help="plain output")
    parser.add_argument("--min-rows", type=int, default=1,
                        help="skip series shorter than N values (default 1)")
    parser.add_argument("-V", "--version", action="version",
                        version=f"benford {__version__}")
    return parser


def main(argv: list[str] | None = None) -> int:
    _prepare_console()
    parser = build_parser()
    args = parser.parse_args(argv)

    C.enabled = not args.no_color and sys.stdout.isatty() and not args.json
    show_second = args.second or args.all
    show_last2 = args.last2 or args.all

    if args.why:
        print(C.p(WHY, C.CYAN) if C.enabled else WHY)
        return 0

    if args.demo:
        run_demo()
        return 0

    if args.fib:
        print_report(analyze(Series(f"Fibonacci numbers (first {args.fib})",
                                    fibonacci(args.fib))),
                     show_second=show_second, show_last2=show_last2)
        return 0

    if not args.files:
        if C.enabled:
            print(C.p(BANNER, C.CYAN, C.BOLD))
        parser.print_help()
        print("\nStart with  python benford.py --demo\n")
        return 0

    all_series: list[Series] = []
    for path in args.files:
        if path != "-" and not os.path.exists(path):
            print(C.p(f"no such file: {path}", C.RED), file=sys.stderr)
            return 2
        try:
            found = load_series(path, args.column, args.sheet)
        except (OSError, SpreadsheetError) as exc:
            print(C.p(f"could not read {path}: {exc}", C.RED), file=sys.stderr)
            return 2
        if not found:
            print(C.p(f"no numbers found in {path}"
                      + (f", column '{args.column}'" if args.column else ""),
                      C.YELLOW), file=sys.stderr)
        all_series.extend(s for s in found if len(s) >= args.min_rows)

    if not all_series:
        print(C.p("nothing to analyze", C.RED), file=sys.stderr)
        return 1

    results = [analyze(series, args.limit) for series in all_series]

    if args.json:
        payload = [result_to_json(r) for r in results]
        print(json.dumps(payload if len(payload) > 1 else payload[0],
                         ensure_ascii=False, indent=2))
        return 0

    for result in results:
        print_report(result, show_second=show_second, show_last2=show_last2)

    worst = max(risk_score(r)[0] for r in results)
    return 0 if worst < 60 else 3


if __name__ == "__main__":
    sys.exit(main())
