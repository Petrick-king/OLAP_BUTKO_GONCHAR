#!/usr/bin/env python3
"""Доказательство, что materialized view действительно срабатывает.

    python 09_З09_mv_витрина/etl/check_mv_fires.py

ЗАЧЕМ. Сводка в проекте наполняется явным `INSERT ... SELECT` — и это
делает проверку «view работает» неочевидной: числа сходятся, но, может,
view тут вообще ни при чём. Поэтому скрипт делает контролируемый
эксперимент:

    1. запоминает базовые числа (факт и сводка);
    2. вставляет в fact_sales ОДНУ синтетическую строку
       (order_id = '__MV_TEST__', дата 1999-01-01);
    3. проверяет, что в сводке САМА появилась строка 1999-01-01 × __MV_TEST__;
    4. удаляет синтетику из факта И из сводки;
    5. убеждается, что числа вернулись к базовым.

Если шаг 3 не сработал — view не привязана к fact_sales. Если шаг 5
не вернул числа — удаление оставило мусор, и это тоже видно.

СКРИПТ ИЗМЕНЯЕТ ДАННЫЕ, но только своими двумя строками и убирает их
за собой. Шаги 1 и 5 — страховка: если числа не вернулись, скрипт
падает с ненулевым кодом, и это нельзя не заметить.
"""
from __future__ import annotations

import sys

import clickhouse_connect

FACT = "olist.fact_sales"
SUMMARY = "olist.mv_gmv_day_category"
TEST_ORDER = "__MV_TEST__"
TEST_DATE = "1999-01-01"
TEST_CATEGORY = "__mv_test__"

BASELINE_SQL = f"""
SELECT
    (SELECT count() FROM {FACT})                            AS fact_rows,
    (SELECT count() FROM {SUMMARY})                         AS summary_rows,
    (SELECT sum(amount) FROM {FACT}
      WHERE order_status = 'delivered')                     AS fact_gmv,
    (SELECT sum(gmv_brl) FROM {SUMMARY})                    AS summary_gmv
"""

TEST_ROW_SQL = f"""
INSERT INTO {FACT} (
    order_item_sk, order_id, order_item_id, order_date_key,
    customer_id, seller_id, product_id, category_name_pt,
    order_status, order_purchase_timestamp,
    amount, qty, freight_value,
    is_delivered, is_late, delivery_days
) VALUES (
    999999999, '{TEST_ORDER}', 1, DATE '{TEST_DATE}',
    '__mv_test__', '__mv_test__', '__mv_test__', '{TEST_CATEGORY}',
    'delivered', toDateTime('1999-01-01 10:00:00', 'UTC'),
    toDecimal64(12.34, 2), 1, toDecimal64(0, 2),
    1, 0, NULL
)
"""

EMPTY_CHECK_SQL = f"""
SELECT
    (SELECT count() FROM {FACT} WHERE order_id = '{TEST_ORDER}')     AS fact_left,
    (SELECT count() FROM {SUMMARY}
      WHERE order_date_key = DATE '{TEST_DATE}')                     AS summary_left
"""


def baseline(client) -> tuple:
    return client.query(BASELINE_SQL).result_rows[0]


def main() -> None:
    client = clickhouse_connect.get_client(
        host="localhost", port=8123, username="olist",
        password="olist", database="olist")

    failures = 0

    print("=" * 78)
    print("1. Базовые числа до эксперимента")
    print("=" * 78)
    base = baseline(client)
    print(f"  строк факта      : {base[0]:,}")
    print(f"  строк сводки     : {base[1]:,}")
    print(f"  оборот факта     : {base[2]:,}")
    print(f"  оборот сводки    : {base[3]:,}")

    print()
    print("=" * 78)
    print(f"2. Вставляем ОДНУ строку в {FACT}")
    print(f"   order_id={TEST_ORDER}, дата={TEST_DATE}, категория={TEST_CATEGORY}")
    print("=" * 78)
    client.command(TEST_ROW_SQL)

    print()
    print("=" * 78)
    print("3. Появилась ли строка в сводке САМА (без INSERT в неё)?")
    print("=" * 78)
    row = client.query(f"""
        SELECT order_date_key, category_name_pt, gmv_brl, qty, positions
        FROM {SUMMARY}
        WHERE order_date_key = DATE '{TEST_DATE}'
    """).result_rows
    if row:
        print("  ДА — materialized view сработала:")
        print(f"    {row[0]}")
    else:
        print("  НЕТ — view не привязана к факту.")
        failures += 1

    print()
    print("=" * 78)
    print("4. Убираем синтетику из факта и из сводки")
    print("=" * 78)
    client.command(f"ALTER TABLE {FACT} DELETE WHERE order_id = '{TEST_ORDER}' "
                   f"SETTINGS mutations_sync = 1")
    print("  факт очищен")
    # Строка сводки после удаления факта сама не исчезнет: view реагирует
    # на вставки, а не на удаления. Удаляем руками — это и есть цена
    # materialized view, о которой не пишут в учебниках.
    client.command(f"ALTER TABLE {SUMMARY} DELETE WHERE order_date_key = DATE '{TEST_DATE}' "
                   f"SETTINGS mutations_sync = 1")
    print("  сводка очищена (view на DELETE не реагирует — удалили вручную)")

    print()
    print("=" * 78)
    print("5. Проверка: числа вернулись к базовым?")
    print("=" * 78)
    after = baseline(client)
    left = client.query(EMPTY_CHECK_SQL).result_rows[0]

    checks = [
        ("строк факта", base[0], after[0]),
        ("строк сводки", base[1], after[1]),
        ("оборот факта", base[2], after[2]),
        ("оборот сводки", base[3], after[3]),
        ("осталось тестовых строк в факте", 0, left[0]),
        ("осталось тестовых строк в сводке", 0, left[1]),
    ]
    for name, expected, actual in checks:
        ok = expected == actual
        failures += 0 if ok else 1
        print(f"  [{'OK' if ok else 'FAIL'}] {name}: было {expected}, стало {actual}")

    client.close()
    print()
    if failures:
        print(f"ИТОГ: проблем — {failures}. Стенд нужно проверить руками.")
        sys.exit(1)
    print("ИТОГ: view срабатывает, синтетика убрана, числа вернулись к базовым.")


if __name__ == "__main__":
    main()
