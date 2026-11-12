#!/usr/bin/env python3
"""Сборка витрины Olist в DuckDB (практика З03).

    python 03_З03_витрина_duckdb/etl/build_duckdb.py            # пересобрать
    python 03_З03_витрина_duckdb/etl/build_duckdb.py --checks   # + прогнать проверки

Что делает:
  1. читает ../../data/raw/*.csv;
  2. выполняет sql/ddl_duckdb.sql    — создаёт таблицы (типы объявлены явно);
  3. выполняет sql/load_duckdb.sql   — чистит таблицы и заливает заново;
  4. (--checks) выполняет sql/checks_duckdb.sql и печатает результат.

Идемпотентность: шаг 3 начинается с DELETE FROM по всем таблицам,
поэтому второй запуск даёт ровно тот же результат, а не удвоение.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

try:
    import duckdb
except ModuleNotFoundError:
    sys.exit("Нужен duckdb:  pip install duckdb")

WORK = Path(__file__).resolve().parents[1]        # .../03_З03_витрина_duckdb
PROJECT = WORK.parents[0]                        # .../OLAP лабы
SQL = WORK / "sql"
WAREHOUSE = PROJECT / "data" / "warehouse.duckdb"

# Порядок обязателен: сначала создать, потом чистить, потом заливать.
TABLES = ["fact_sales", "dim_product", "dim_seller", "dim_customer",
          "dim_date", "dim_category", "dim_geo"]


def find_raw_dir() -> Path:
    import os
    env = os.environ.get("RAW_DIR")
    candidates = [Path(env)] if env else [PROJECT / "data" / "raw",
                                          PROJECT / "raw data"]
    for c in candidates:
        if c.is_dir() and any(c.glob("*.csv")):
            return c
    sys.exit("Не нашёл папку с CSV. Задай RAW_DIR=<путь>.")


def run_script(con, path: Path, raw: Path) -> None:
    sql = path.read_text(encoding="utf-8")
    sql = sql.replace("{RAW}", raw.as_posix()).replace("{ROOT}", PROJECT.as_posix())
    print(f"   {path.name} ...", end=" ", flush=True)
    con.execute(sql)
    print("ok")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--checks", action="store_true", help="прогнать проверки после сборки")
    args = ap.parse_args()

    raw = find_raw_dir()
    WAREHOUSE.parent.mkdir(parents=True, exist_ok=True)

    print(f"DuckDB     : {duckdb.__version__}")
    print(f"Сырьё      : {raw}")
    print(f"База       : {WAREHOUSE}")
    print()

    con = duckdb.connect(str(WAREHOUSE))
    con.execute("SET TimeZone='UTC'")

    print("1/3 DDL")
    run_script(con, SQL / "ddl_duckdb.sql", raw)

    print("2/3 Загрузка (полный refresh: сначала чистим, потом кладём)")
    for t in TABLES:
        con.execute(f"DELETE FROM {t};")
    run_script(con, SQL / "load_duckdb.sql", raw)

    print("3/3 Итог")
    rows = con.execute(
        "SELECT table_name, row_count FROM ("
        "  SELECT 'fact_sales' AS table_name, COUNT(*) AS row_count FROM fact_sales"
        "  UNION ALL SELECT 'dim_product',  COUNT(*) FROM dim_product"
        "  UNION ALL SELECT 'dim_customer', COUNT(*) FROM dim_customer"
        "  UNION ALL SELECT 'dim_seller',   COUNT(*) FROM dim_seller"
        "  UNION ALL SELECT 'dim_category', COUNT(*) FROM dim_category"
        "  UNION ALL SELECT 'dim_date',     COUNT(*) FROM dim_date"
        "  UNION ALL SELECT 'dim_geo',      COUNT(*) FROM dim_geo"
        ") ORDER BY 1"
    ).fetchall()
    for name, cnt in rows:
        print(f"   {name:<13} {cnt:>9,}")

    gmv = con.execute(
        "SELECT SUM(amount), COUNT(*), COUNT(DISTINCT order_id), SUM(qty) "
        "FROM fact_sales WHERE order_status = 'delivered'"
    ).fetchone()
    print(f"\n   Главная метрика GMV_delivered = {gmv[0]:,.2f} BRL "
          f"({gmv[1]:,} позиций, {gmv[2]:,} заказов, {gmv[3]:,} штук)")
    con.close()

    if args.checks:
        # Вывод проверочных запросов. Делегируем run_checks.py: он разбирает
        # checks_duckdb.sql на отдельные запросы и печатает каждый результат.
        # con.execute() с многооператорным текстом показывает только последний.
        import runpy
        import sys
        sys.argv = [str(Path(__file__).with_name("run_checks.py"))]
        print("\n>> Проверки (sql/checks_duckdb.sql)\n")
        runpy.run_path(str(Path(__file__).with_name("run_checks.py")),
                       run_name="__main__")


if __name__ == "__main__":
    main()