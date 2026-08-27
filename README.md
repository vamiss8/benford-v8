# benford-detector

Pocket-sized digital forensics for the terminal · Карманная цифровая криминалистика в терминале

[English](#english) · [Русский](#russian)

---

# ENGLISH

A small tool I wrote to see how far a single statistical law gets you against
made-up numbers. You hand it a table, it tells you whether the figures look
like they came from the world or out of somebody's head.

One file, no dependencies, Python 3.10+.

```bash
python benford.py --demo
```

## The trick

In numbers that come from the real world the leading digit is not uniform. A
one turns up about 30% of the time, a nine only 4.6%:

```
P(leading digit = d) = log10(1 + 1/d)

  1 - 30.1%      4 - 9.7%       7 - 5.8%
  2 - 17.6%      5 - 7.9%       8 - 5.1%
  3 - 12.5%      6 - 6.7%       9 - 4.6%
```

Take a deposit growing at 10% a year. Going from 1000 to 2000 takes seven
years; getting from 9000 to 10000 takes one. A number simply spends longer with
a one in front. Everything that grows by multiplication behaves this way:
prices, city populations, river lengths, electricity bills, file sizes,
Fibonacci numbers.

People inventing numbers off the top of their head have no idea about this and
spread the digits out roughly evenly. That is the whole detector in one
sentence.

`python benford.py --why` prints the long version.

## How it works

```
file ──► parse_number() ──► significand() ──► digit tests ──┐
 CSV      "1 234,56 ₽"        "123456"        MAD, chi2     │
 JSON     -> 1234.56                                        ▼
 text                                                  risk score
 stdin                        round numbers ──────────►  0..100
                              duplicates
                              approval limit
```

You point it at a file and it finds the numeric column itself — a column
qualifies when at least 80% of its filled cells parse as numbers, which is what
keeps dates and invoice codes out of the sample. With several numeric columns
it reports on each; `--column` pins one down by name or by number.

The parser is deliberately forgiving, because real exports are messy:
`1 234,56 ₽`, `$1,234.56`, `(1 000,00)` with the accounting minus, `12,5%`.
When a number contains both separators, whichever sits further right is the
decimal one.

The ambiguous case — a lone comma in `1,234` — turned out not to matter at all.
Read as thousands it is 1234, read as a decimal it is 1.234, and the leading
digits are `1`, `2` either way. Scale invariance means the tests never see the
difference, so the rule there is a coin toss that costs nothing.

## What the report says

```
  Leading digit   (n = 1200)
   1 │█████████████·················┃···│  12.5%  expected 30.1%  -17.6 pp  <- anomaly
   4 │█████████┃█████████████████████···│  30.9%  expected  9.7%  +21.2 pp  <- anomaly
   MAD = 0.0650 -> NONCONFORMITY   |   chi2 = 767.1, p = 0.0000
```

`█` is how often the digit actually showed up, `┃` is where the law expected
it. Beyond that:

| Line | What it means |
| ---- | ------------- |
| **MAD** | mean absolute deviation, against Nigrini's thresholds: under 0.006 close conformity, under 0.012 acceptable, under 0.015 marginal, above that nonconformity |
| **chi2, p** | the odds of a gap this wide appearing by chance; below 0.01 coincidence no longer explains it |
| **Round numbers** | share of amounts ending in 0, 00, 000, plus the share with no cents at all |
| **Repeated values** | the same amount copy-pasted over and over |
| **Approval limit** | how many amounts hug the threshold from below — the classic tell of a purchase split so no single part needs a signature |
| **Verdict** | a 0–100 risk score and the list of what looked off |

Exit codes are meant for CI: `0` data looks alive, `3` risk score 60 or above,
`1`/`2` nothing to analyze or the file could not be read.

## Usage

```bash
python benford.py samples/invoices_honest.csv
```

```bash
python benford.py samples/invoices_cooked.csv --all --limit 5000
```

```bash
cat numbers.txt | python benford.py -
```

| Flag | What it does |
| ---- | ------------ |
| `-c`, `--column` | CSV column, by name or by number |
| `-l`, `--limit` | approval limit: look for amounts piling up below it |
| `-2`, `--second` | add the second-digit test |
| `--last2` | add the last-two-digits test |
| `-a`, `--all` | run every test |
| `--json` | machine-readable output |
| `--demo` | watch the law at work on three data sets |
| `--why` | explain why the law works |
| `--fib N` | check the first N Fibonacci numbers |
| `--min-rows N` | skip series shorter than N values |
| `--no-color` | plain output |

## Notes from building it

**Two of the tests were lying about big numbers.** Roundness and the last-two-
digits test both said the Fibonacci sequence was 84% round hundreds, which is
nonsense — past 2^53 a float no longer stores its integer part exactly, so
every huge value looks divisible by everything. Both tests now stop at 1e12 and
say how many values they actually used.

**Roundness moved to the integer part.** Measuring it on the whole amount meant
`1234.56` could never be a multiple of ten, so honest invoices scored 0.1%
against an expected 10% and the metric was pure noise. Counting the whole units
instead puts honest data at 10.8% and invented data at 32.1%, which is the
signal I wanted. The share of amounts with no cents at all is reported next to
it, because that is where hand-typed figures really give themselves away —
0.7% honest against 47.8% invented.

**The `.12e` format was inventing digits.** Formatting a value to twelve
decimals pads zeros the number does not have, so a plain `9` appeared to have a
second digit of `0`. The second-digit test was quietly polluted by every
single-digit value in the sample. It now uses the shortest float repr, which
pads nothing. A test caught this, not me.

## Where the law does not apply

- Numbers with artificial bounds: human height, body temperature, IQ.
- Identifiers people hand out: phone numbers, SKUs, tax IDs, house numbers.
- Samples under a hundred values, where randomness still rules.

And a mismatch means "look closer here", never "this is fraud". It is a
flashlight, not a verdict, and the report says so on every run.

## Layout

```
benford.py               the whole tool
test_benford.py          30 tests, run with: python test_benford.py
samples/make_samples.py  demo data generator, fixed seed
samples/invoices_*.csv   two sets of invoices: honest and invented
```

The demo data is generated, not collected. The honest set is price × quantity ×
adjustment, and a product of independent random quantities drifts towards
Benford entirely on its own — that is the real reason the law works at all. The
invented set is what a person produces: digits spread evenly, round figures
everywhere, and a crowd of payments sitting just under the 5000 approval limit.
The first scores 12 risk points out of 100, the second scores all 100.

## Tests

```bash
python test_benford.py
```

They cover messy-number parsing, the math of the law, p-values against
published chi-square tables, the CSV/JSON/text loaders, and the one assertion
that actually matters: honest data must pass and invented data must get caught.

## Roadmap

- [x] Leading and second digit tests, MAD, chi-square with p-values
- [x] Round numbers, duplicates, approval-limit clustering
- [x] CSV / JSON / text loaders with column autodetection
- [x] Risk score, JSON output, CI-friendly exit codes
- [ ] Nigrini's summation test
- [ ] Reading .xlsx directly
- [ ] A standalone HTML report

---

# RUSSIAN

Маленький инструмент, который я написал, чтобы посмотреть, как далеко один
статистический закон уезжает против выдуманных чисел. Даёшь ему таблицу — он
говорит, похожи ли цифры на пришедшие из жизни или на сочинённые в голове.

Один файл, без зависимостей, Python 3.10+.

```bash
python benford.py --demo
```

## В чём фокус

В «настоящих» числах первая значащая цифра не равновероятна. Единица
встречается примерно в 30.1% случаев, а девятка — всего в 4.6%:

```
P(первая цифра = d) = log10(1 + 1/d)

  1 — 30.1%      4 — 9.7%       7 — 5.8%
  2 — 17.6%      5 — 7.9%       8 — 5.1%
  3 — 12.5%      6 — 6.7%       9 — 4.6%
```

Возьмите вклад под 10% годовых. От 1000 до 2000 сумма будет расти семь лет, а
от 9000 до 10000 доберётся за год. Число просто дольше «живёт» с единицей в
начале. Так ведёт себя всё, что растёт умножением: цены, население городов,
длины рек, счета за электричество, размеры файлов, числа Фибоначчи.

А человек, выдумывающий числа из головы, об этом не догадывается и
раскладывает цифры примерно поровну. Вот и весь детектор в одном предложении.

`python benford.py --why` печатает длинную версию.

## Как устроено

```
файл ──► parse_number() ──► significand() ──► тесты цифр ──┐
 CSV      "1 234,56 ₽"        "123456"        MAD, chi2    │
 JSON     -> 1234.56                                       ▼
 текст                                                 оценка риска
 stdin                        круглые числа ──────────►   0..100
                              повторы
                              лимит согласования
```

Показываешь ему файл — числовую колонку он находит сам: колонка подходит, если
хотя бы 80% заполненных ячеек разбираются как числа. Именно это отсекает даты и
коды документов. Если числовых колонок несколько, разберёт каждую; `--column`
указывает конкретную по имени или номеру.

Парсер намеренно всеядный, потому что настоящие выгрузки — грязные:
`1 234,56 ₽`, `$1,234.56`, `(1 000,00)` с бухгалтерским минусом, `12,5%`. Если
в числе оба разделителя, десятичным считается тот, что правее.

Неоднозначный случай — одинокая запятая в `1,234` — оказался вообще неважен.
Как тысячи это 1234, как дробь это 1.234, и первые цифры в обоих случаях `1`,
`2`. Из-за масштабной инвариантности тесты этой разницы не видят, так что
правило там — подбрасывание монетки, которое ничего не стоит.

## Что показывает отчёт

```
  Leading digit   (n = 1200)
   1 │█████████████·················┃···│  12.5%  expected 30.1%  -17.6 pp  <- anomaly
   4 │█████████┃█████████████████████···│  30.9%  expected  9.7%  +21.2 pp  <- anomaly
   MAD = 0.0650 -> NONCONFORMITY   |   chi2 = 767.1, p = 0.0000
```

`█` — сколько раз цифра встретилась на самом деле, `┃` — где её ждал закон.
Дальше:

| Строка | Что означает |
| ------ | ------------ |
| **MAD** | средняя абсолютная девиация против порогов Нигрини: до 0.006 образцовое соответствие, до 0.012 приемлемое, до 0.015 пограничное, дальше — не соответствует |
| **chi2, p** | вероятность увидеть такое расхождение по чистой случайности; меньше 0.01 — совпадением это уже не объяснить |
| **Round numbers** | доля сумм, кончающихся на 0, 00, 000, и отдельно доля сумм без копеек |
| **Repeated values** | одни и те же суммы, размноженные копипастой |
| **Approval limit** | сколько сумм жмётся к порогу снизу — классический след закупки, разбитой так, чтобы каждая часть не требовала подписи |
| **Verdict** | оценка риска 0–100 и список того, что именно смутило |

Коды возврата сделаны под CI: `0` — данные выглядят живыми, `3` — риск 60 и
выше, `1`/`2` — анализировать нечего или файл не прочитался.

## Как пользоваться

```bash
python benford.py samples/invoices_honest.csv
```

```bash
python benford.py samples/invoices_cooked.csv --all --limit 5000
```

```bash
cat numbers.txt | python benford.py -
```

| Флаг | Зачем |
| ---- | ----- |
| `-c`, `--column` | колонка CSV по имени или номеру |
| `-l`, `--limit` | лимит согласования: искать скопление сумм под ним |
| `-2`, `--second` | добавить тест второй цифры |
| `--last2` | добавить тест последних двух цифр |
| `-a`, `--all` | все тесты сразу |
| `--json` | машинный вывод |
| `--demo` | посмотреть закон в работе на трёх наборах |
| `--why` | объяснить, почему закон работает |
| `--fib N` | проверить первые N чисел Фибоначчи |
| `--min-rows N` | пропускать серии короче N значений |
| `--no-color` | вывод без цвета |

## Что выяснилось по дороге

**Два теста врали про большие числа.** Круглость и тест последних двух цифр
дружно заявили, что числа Фибоначчи на 84% состоят из круглых сотен, — чушь:
после 2^53 float перестаёт точно хранить целую часть, и любое огромное значение
выглядит делящимся на что угодно. Теперь оба теста останавливаются на 1e12 и
честно пишут, по скольким значениям посчитали.

**Круглость переехала на целую часть.** Пока она считалась по всей сумме,
`1234.56` не мог быть кратен десяти в принципе: честные счета давали 0.1% при
ожидаемых 10%, и метрика была чистым шумом. По целым рублям честные данные дают
10.8%, выдуманные — 32.1%, вот это уже сигнал. Рядом печатается доля сумм вообще
без копеек, потому что именно там ручные цифры выдают себя: 0.7% против 47.8%.

**Формат `.12e` придумывал цифры.** Он дописывает нули, которых в числе нет,
поэтому у обычной девятки находилась вторая цифра `0`. Тест второй цифры тихо
загрязнялся каждым однозначным значением выборки. Теперь используется
кратчайшее представление float, которое не дописывает ничего. Поймал это тест,
а не я.

## Где закон не работает

- Числа с искусственными границами: рост человека, температура тела, IQ.
- Назначенные людьми коды: телефоны, артикулы, ИНН, номера домов.
- Выборки меньше сотни значений — там правит случайность.

И несоответствие означает «проверьте внимательнее», а не «здесь мошенничество».
Это фонарик, а не приговор, о чём отчёт сообщает при каждом запуске.

## Файлы

```
benford.py               весь инструмент
test_benford.py          30 тестов, запуск: python test_benford.py
samples/make_samples.py  генератор демо-данных, сид зафиксирован
samples/invoices_*.csv   два набора счетов: честный и выдуманный
```

Демо-данные сгенерированы, а не собраны. Честный набор — это цена × количество ×
коэффициент, и произведение независимых случайных величин само сползает к
Бенфорду. Собственно, поэтому закон и работает. Выдуманный набор — то, что
выдаёт человек: равномерные цифры, круглые суммы всюду и толпа платежей прямо
под лимитом согласования в 5000. Первый набирает 12 баллов риска из 100, второй
— все 100.

## Тесты

```bash
python test_benford.py
```

Проверяют разбор грязных чисел, математику закона, p-value по опубликованным
таблицам chi-квадрат, загрузчики CSV/JSON/текста и главное утверждение: честные
данные обязаны проходить, выдуманные — попадаться.

## Что дальше

- [x] Тесты первой и второй цифры, MAD, chi-квадрат с p-value
- [x] Круглые числа, повторы, скопление под лимитом согласования
- [x] Загрузчики CSV / JSON / текста с автоопределением колонки
- [x] Оценка риска, JSON-вывод, коды возврата под CI
- [ ] Тест сумм по Нигрини
- [ ] Чтение .xlsx напрямую
- [ ] Отдельный HTML-отчёт

---

## License

MIT
