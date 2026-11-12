#!/usr/bin/env python3
"""Один и тот же вопрос к DuckDB и к ClickHouse — ответ должен совпасть.

    python 08_З08_запросы_и_ключи_ch/etl/compare_backends.py
    python 08_З08_запросы_и_ключи_ch/etl/compare_backends.py \
        --out 08_З08_запросы_и_ключи_ch/evidence/compare_raw.txt

ЗАЧЕМ. З08 требует «показать, что таблицы в ClickHouse отвечают на ваши
вопросы». «Запрос выполнился без ошибки» — это не ответ. Поэтому здесь
три запроса З04 прогоняются в обеих базах, и результаты сравниваются
по строкам: совпал оборот — значит витрина перенесена, а не просто
«таблица есть».

ПОЧЕМУ SQL ДВУМЯ ТЕКСТАМИ. Эталон главной метрики в обеих базах —
ОДИН И ТОТ ЖЕ текст (это и проверяется). А два аналитических запроса
различаются мелочами синтаксиса (`LAG` → `lagInFrame`, приведение Decimal
к Float64 при делении). Держать их в одном файле с ветвлением по движку
значит спрятать эти различия — а они и есть содержание лабораторной.
Поэтому тексты лежат рядом, по одному на базу, и сравниваются результаты.

Сравнение чисел: деньги — точно (Decimal), доли — с допуском 1e-6
(в DuckDB и ClickHouse разный порядок округления Double).
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

try:
    import duckdb
except ModuleNotFoundError:
    sys.exit("Нужен duckdb:  pip install duckdb")
try:
    import clickhouse_connect
except ModuleNotFoundError:
    sys.exit("Нужен clickhouse-connect:  pip install clickhouse-connect")

WORK = Path(__file__).resolve().parents[1]
PROJECT = WORK.parents[0]
WAREHOUSE = PROJECT / "data" / "warehouse.duckdb"

TOL = 1e-6

# ── 1. Эталон главной метрики ────────────────────────────────────────────────
# Текст ОДИН. Именно это и доказываем: он переносится без правок.
CANONICAL = """
SELECT
    'GMV_delivered'                     AS metric_name,
    SUM(amount)                         AS metric_value,
    COUNT(*)                            AS positions_count,
    COUNT(DISTINCT order_id)            AS orders_count,
    SUM(qty)                            AS units_count
FROM fact_sales
WHERE order_status = 'delivered'
"""

# ── 2. Топ-5 категорий 2018 ──────────────────────────────────────────────────
TOP5_DUCK = """
WITH by_category AS (
    SELECT category_name_pt AS category, SUM(amount) AS gmv_brl, SUM(qty) AS units
    FROM fact_sales
    WHERE order_status = 'delivered'
      AND order_date_key >= DATE '2018-01-01' AND order_date_key < DATE '2019-01-01'
    GROUP BY 1
)
SELECT category, gmv_brl, units,
       gmv_brl / SUM(gmv_brl) OVER () AS share_of_gmv,
       RANK() OVER (ORDER BY gmv_brl DESC) AS place
FROM by_category ORDER BY gmv_brl DESC LIMIT 5
"""
TOP5_CH = """
WITH by_category AS (
    SELECT category_name_pt AS category, SUM(amount) AS gmv_brl, SUM(qty) AS units
    FROM fact_sales
    WHERE order_status = 'delivered'
      AND order_date_key >= DATE '2018-01-01' AND order_date_key < DATE '2019-01-01'
    GROUP BY category_name_pt
)
SELECT category, gmv_brl, units,
       toFloat64(gmv_brl) / toFloat64(SUM(gmv_brl) OVER ()) AS share_of_gmv,
       RANK() OVER (ORDER BY gmv_brl DESC) AS place
FROM by_category ORDER BY gmv_brl DESC LIMIT 5
"""

# ── 3. Помесячная динамика 2017 ──────────────────────────────────────────────
MONTHLY_DUCK = """
WITH monthly AS (
    SELECT strftime(order_date_key, '%Y-%m') AS year_month,
           COUNT(DISTINCT order_id) AS orders, COUNT(*) AS positions,
           SUM(qty) AS units, SUM(amount) AS gmv_brl
    FROM fact_sales
    WHERE order_status = 'delivered'
      AND order_date_key >= DATE '2017-01-01' AND order_date_key < DATE '2018-01-01'
    GROUP BY 1
)
SELECT year_month, orders, positions, units, gmv_brl,
       LAG(gmv_brl) OVER (ORDER BY year_month) AS prev_month_gmv,
       gmv_brl - LAG(gmv_brl) OVER (ORDER BY year_month) AS gmv_delta_brl
FROM monthly ORDER BY year_month
"""
MONTHLY_CH = """
WITH monthly AS (
    SELECT formatDateTime(toStartOfMonth(order_date_key), '%Y-%m') AS year_month,
           COUNT(DISTINCT order_id) AS orders, COUNT(*) AS positions,
           SUM(qty) AS units, SUM(amount) AS gmv_brl
    FROM fact_sales
    WHERE order_status = 'delivered'
      AND order_date_key >= DATE '2017-01-01' AND order_date_key < DATE '2018-01-01'
    GROUP BY year_month
)
SELECT year_month, orders, positions, units, gmv_brl,
       lagInFrame(toNullable(gmv_brl), 1, NULL) OVER (
           ORDER BY year_month ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS prev_month_gmv,
       gmv_brl - lagInFrame(toNullable(gmv_brl), 1, NULL) OVER (
           ORDER BY year_month ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS gmv_delta_brl
FROM monthly ORDER BY year_month
"""

CASES = [
    ("1. Эталон главной метрики (один и тот же SQL)", CANONICAL, CANONICAL),
    ("2. Топ-5 категорий 2018", TOP5_DUCK, TOP5_CH),
    ("3. Помесячная динамика 2017", MONTHLY_DUCK, MONTHLY_CH),
]


def same(a: object, b: object) -> bool:
    """Сравнивает два значения: числа — с допуском, остальное — как есть."""
    if a is None or b is None:
        return a is None and b is None
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return abs(float(a) - float(b)) <= TOL
    try:                                       # Decimal и прочее числовое
        return abs(float(a) - float(b)) <= TOL  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return str(a) == str(b)


def compare(name: str, rows_a: list[tuple], rows_b: list[tuple]) -> tuple[bool, str]:
    if len(rows_a) != len(rows_b):
        return False, f"разное число строк: DuckDB {len(rows_a)}, ClickHouse {len(rows_b)}"
    bad: list[str] = []
    for i, (ra, rb) in enumerate(zip(rows_a, rows_b), 1):
        for j, (va, vb) in enumerate(zip(ra, rb)):
            if not same(va, vb):
                bad.append(f"строка {i}, колонка {j}: DuckDB={va!r}  ClickHouse={vb!r}")
    if bad:
        return False, "; ".join(bad[:5])
    return True, f"{len(rows_a)} строк(и) × {len(rows_a[0])} колонок — совпало"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", help="записать протокол в файл")
    args = ap.parse_args()

    duck = duckdb.connect(str(WAREHOUSE), read_only=True)
    ch = clickhouse_connect.get_client(
        host="localhost", port=8123, username="olist",
        password="olist", database="olist")

    log: list[str] = ["# Сверка DuckDB ↔ ClickHouse (З08)", ""]
    failed = 0
    for name, sql_duck, sql_ch in CASES:
        rows_duck = duck.execute(sql_duck).fetchall()
        rows_ch = ch.query(sql_ch).result_rows
        ok, note = compare(name, rows_duck, rows_ch)
        failed += 0 if ok else 1
        mark = "OK  " if ok else "FAIL"
        line = f"[{mark}] {name}: {note}"
        print(line)
        log.append(f"- {line}")

    duck.close()
    ch.close()

    verdict = ("ИТОГ: расхождений нет — обе базы отвечают одинаково."
               if not failed else f"ИТОГ: расхождений {failed}.")
    print(verdict)
    log += ["", verdict, ""]

    if args.out:
        p = Path(args.out)
        if not p.is_absolute():
            p = PROJECT / p
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("\n".join(log), encoding="utf-8")
        print(f"Протокол сохранён: {p}")

    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
