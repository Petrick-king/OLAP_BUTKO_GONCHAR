#!/usr/bin/env python3
"""Smoke-проверка локальной лаборатории DuckDB на реальном сырье Olist.

Задание З01 требует убедиться, что инструменты ставятся и читают данные.
Скрипт ничего не создаёт и не меняет — только читает CSV и печатает сводку.

Запуск:  python 01_З01_стенд_и_сырьё/scripts/smoke_duckdb.py
"""
from __future__ import annotations

import sys
from pathlib import Path

try:
    import duckdb
except ModuleNotFoundError:
    sys.exit("Нужен duckdb:  pip install duckdb")


def find_raw_dir() -> Path:
    """Ищем папку сырья: $RAW_DIR -> ../../data/raw -> ../../raw data."""
    env = __import__("os").environ.get("RAW_DIR")
    candidates = [Path(env)] if env else []
    here = Path(__file__).resolve()
    candidates += [
        here.parents[1] / "data" / "raw",   # 01_З01_стенд_и_сырьё/../data/raw
        here.parents[2] / "data" / "raw",   # запасной вариант
        here.parents[2] / "raw data",      # оригинальный архив
    ]
    for c in candidates:
        if c.is_dir() and any(c.glob("*.csv")):
            return c
    sys.exit("Не нашёл папку с CSV. Задай RAW_DIR=<путь>.")


RAW = find_raw_dir()

# Что показываем в smoke-сводке: файл -> что должно быть видно "на глаз".
PROBES = [
    ("olist_orders_dataset.csv", "count(*)", "заказы"),
    ("olist_order_items_dataset.csv", "count(*)", "позиции заказов"),
    ("olist_customers_dataset.csv", "count(*)", "покупатели"),
    ("olist_sellers_dataset.csv", "count(*)", "продавцы"),
    ("olist_products_dataset.csv", "count(*)", "товары"),
    ("olist_order_payments_dataset.csv", "count(*)", "платежи"),
    ("olist_order_reviews_dataset.csv", "count(*)", "отзывы"),
    ("olist_geolocation_dataset.csv", "count(*)", "точки геолокации"),
    ("product_category_name_translation.csv", "count(*)", "переводы категорий"),
]


def main() -> None:
    print(f"DuckDB {duckdb.__version__}")
    print(f"RAW     {RAW}\n")

    con = duckdb.connect()
    con.execute("SET TimeZone='UTC'")

    for fname, expr, label in PROBES:
        path = RAW / fname
        rows = con.execute(
            f"SELECT {expr} FROM read_csv_auto(?, header=true, sample_size=-1)",
            [str(path)],
        ).fetchone()[0]
        print(f"  {label:<22} {fname:<42} {rows:>10,}")

    # Две контрольные цифры, на которые дальше опирается вся витрина.
    con.execute(
        f"""
        CREATE OR REPLACE TEMP VIEW items AS
        SELECT * FROM read_csv_auto('{RAW}/olist_order_items_dataset.csv')
        """
    )
    con.execute(
        f"""
        CREATE OR REPLACE TEMP VIEW orders AS
        SELECT * FROM read_csv_auto('{RAW}/olist_orders_dataset.csv')
        """
    )
    revenue = con.execute(
        "SELECT sum(price) FROM items JOIN orders USING (order_id) "
        "WHERE order_status = 'delivered'"
    ).fetchone()[0]
    print(f"\n  Оборот доставленных заказов (SUM(price)) : {revenue:,.2f}")
    print("\nOK — DuckDB читает сырьё, стенд рабочий.")


if __name__ == "__main__":
    main()