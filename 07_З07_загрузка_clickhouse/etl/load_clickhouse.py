#!/usr/bin/env python3
"""Загрузка витрины Olist из DuckDB в ClickHouse (практика З07).

    python 07_З07_загрузка_clickhouse/etl/load_clickhouse.py
    python 07_З07_загрузка_clickhouse/etl/load_clickhouse.py --tear-down

Что делает:
  1. выполняет sql/ddl_clickhouse.sql (создаёт таблицы, если их нет);
  2. читает DuckDB-витрину и грузит справочники, затем факт;
  3. при --tear-down грузит ВТОРЫМ проходом и проверяет, что дубли
     не появились.

ПОВТОРНАЯ ЗАГРУЗКА. Стратегия — ПОЛНЫЙ REFRESH: перед вставкой каждая
таблица очищается (TRUNCATE), затем данные заливаются заново. Поэтому
второй проход даёт тот же результат, а не вдвое больше строк.

Соединение: CH_USER=olist / CH_PASS=olist / http://localhost:8123, база olist.
Переопределяется переменными окружения CH_HOST, CH_PORT, CH_USER, CH_PASS, CH_DB.
"""
from __future__ import annotations

import argparse
import os
import sys
import time
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
SQL = WORK / "sql"
WAREHOUSE = PROJECT / "data" / "warehouse.duckdb"

CH_HOST = os.environ.get("CH_HOST", "localhost")
CH_PORT = int(os.environ.get("CH_PORT", 8123))
CH_USER = os.environ.get("CH_USER", "olist")
CH_PASS = os.environ.get("CH_PASS", "olist")
CH_DB = os.environ.get("CH_DB", "olist")

# Порядок загрузки: справочники → факт (как в DuckDB).
DIMS = ["dim_geo", "dim_category", "dim_date",
        "dim_customer", "dim_seller", "dim_product", "dim_product_scd2"]
TABLES = DIMS + ["fact_sales"]

# Колонки факта в ClickHouse. Порядок совпадает с порядком в DDL.
# Последние три — версия товара на дату покупки (SCD2, З06). В DuckDB их
# даёт представление fact_sales_scd2, здесь они становятся обычными
# колонками: ClickHouse плохо индексирует джойн по неравенствам
# (order_purchase_timestamp BETWEEN valid_from AND valid_to), поэтому
# версия вычисляется один раз при загрузке, а не на каждый отчёт.
FACT_COLS = [
    "order_item_sk", "order_id", "order_item_id",
    "order_date_key", "customer_id", "seller_id", "product_id", "category_name_pt",
    "order_status", "order_purchase_timestamp",
    "order_delivered_carrier_date", "order_delivered_customer_date",
    "order_estimated_delivery_date", "shipping_limit_date",
    "amount", "qty", "freight_value", "payment_value_order",
    "is_delivered", "delivery_days", "is_late",
    "payment_type", "review_score",
    "product_sk", "version_no", "category_at_sale_pt",
]

# Откуда берём факт: представление с посчитанной версией товара.
SOURCE = {"fact_sales": "fact_sales_scd2"}

BATCH = 20_000


def rule(t: str = "") -> None:
    print("\n" + "─" * 78)
    if t:
        print(t)
        print("─" * 78)


def rule_of_blocks(sql: str) -> list[str]:
    """Режет SQL-файл на операторы CREATE TABLE.

    Разделитель — строка, начинающаяся с CREATE TABLE, а НЕ пустая строка:
    в DDL между `CREATE TABLE ...` и открывающей скобкой стоит пустая строка
    (так читается), и деление по пустым строкам рвёт выражения пополам.
    """
    stmts, buf = [], []
    for line in sql.splitlines():
        if line.strip().upper().startswith("CREATE TABLE") and buf:
            stmts.append("\n".join(buf))
            buf = []
        if line.strip() or buf:
            buf.append(line)
    if buf:
        stmts.append("\n".join(buf))
    return [s for s in stmts if "CREATE TABLE" in s.upper()]


def get_client():
    return clickhouse_connect.get_client(
        host=CH_HOST, port=CH_PORT, username=CH_USER, password=CH_PASS, database=CH_DB
    )


def run_ddl(client) -> None:
    """Пересоздаёт таблицы.

    Именно DROP + CREATE, а не CREATE TABLE IF NOT EXISTS: иначе правка
    DDL (например, смена типа колонки) никогда не применится — таблица
    уже есть, IF NOT EXISTS молча пропустит, и загрузка упадёт позже,
    на вставке, с невнятной ошибкой. Для учебного стенда дешевле
    пересоздать, чем держать миграции.
    """
    print(">> Пересоздаём таблицы (DROP + CREATE)")
    for t in TABLES:
        client.command(f"DROP TABLE IF EXISTS olist.{t}")
    for stmt in rule_of_blocks((SQL / "ddl_clickhouse.sql").read_text(encoding="utf-8")):
        client.command(stmt)
        name = stmt.split("olist.")[1].split("\n")[0].strip()
        engine = [l for l in stmt.splitlines() if l.startswith("ENGINE")][0].split("=")[1].split("(")[0].strip()
        order = [l for l in stmt.splitlines() if l.startswith("ORDER BY") or l.startswith("PARTITION BY")]
        print(f"   {name:<22} {engine:<20} {' | '.join(x.strip() for x in order)[:58]}")


def truncate(client) -> None:
    print("\n>> Полный refresh: очищаем целевые таблицы перед вставкой")
    for t in TABLES:
        client.command(f"TRUNCATE TABLE olist.{t}")
        print(f"   TRUNCATE olist.{t}")


def copy_table(con, client, table: str) -> int:
    """Переливает одну таблицу из DuckDB в ClickHouse.

    ДАТЫ И ВРЕМЯ едут числами, а не объектами datetime:
      Date      -> число дней с 1970-01-01;
      DateTime  -> число секунд с 1970-01-01 UTC.
    Так перенос не зависит от часового пояса машины, на которой
    запущен скрипт: наивный datetime.clickhouse-connect сам
    интерпретирует в локальной зоне, и на двух разных машинах
    данные встали бы на разные сутки.

    Заодно это снимает ограничение WinAPI на datetime(1900, 1, 1):
    ClickHouse всё равно не хранит годы вне 1970…2106.
    """
    t0 = time.time()
    src = SOURCE.get(table, table)

    if table in ("fact_sales",):
        base = f"(SELECT {', '.join(FACT_COLS)} FROM {src})"
        probe = con.execute(f"SELECT * FROM {base} LIMIT 0").description
    else:
        base = src
        probe = con.execute(f"SELECT * FROM {base} LIMIT 0").description

    cols = [d[0] for d in probe]
    types = [str(d[1]) for d in probe]

    proj = []
    for c, t in zip(cols, types):
        if "TIMESTAMP" in t:
            proj.append(f"CAST(epoch({c}) AS BIGINT)")
        elif t == "DATE":
            # DuckDB не умеет CAST(date AS INTEGER) — считаем дни от эпохи.
            proj.append(f"date_diff('day', DATE '1970-01-01', {c})")
        else:
            proj.append(c)

    cur = con.execute(f"SELECT {', '.join(proj)} FROM {base}")
    total = 0
    while True:
        rows = cur.fetchmany(BATCH)
        if not rows:
            break
        client.insert(table, rows, column_names=cols)
        total += len(rows)
    dt = time.time() - t0
    print(f"   {table:<22} {total:>9,} строк  за {dt:5.1f} с")
    return total


def counts(client) -> dict:
    out = {}
    for t in TABLES:
        out[t] = client.command(f"SELECT count() FROM olist.{t}")
    out["_gmv"] = client.command(
        "SELECT toString(round(sum(amount), 2)) FROM olist.fact_sales "
        "WHERE order_status = 'delivered'"
    )
    return out


def show_counts(c: dict, label: str) -> None:
    print(f"\n{label}")
    for k in TABLES:
        print(f"   {k:<22} {c[k]:>9,}")
    print(f"   {'GMV_delivered':<22} {c['_gmv']:>19}")


def load(client, con) -> dict:
    rule("ЗАГРУЗКА")
    truncate(client)
    for t in DIMS:
        copy_table(con, client, t)
    copy_table(con, client, "fact_sales")      # факт строго последним
    client.command("OPTIMIZE TABLE olist.dim_product_scd2 FINAL")
    return counts(client)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tear-down", action="store_true",
                    help="загрузить дважды подряд и сравнить")
    args = ap.parse_args()

    if not WAREHOUSE.exists():
        sys.exit(f"Нет DuckDB-витрины {WAREHOUSE}. Сначала прогоните З03 build_duckdb.py")

    print("=" * 78)
    print("ЗАГРУЗКА ВИТРИНЫ OLIST В CLICKHOUSE (З07)")
    print("=" * 78)
    print(f"Источник : {WAREHOUSE}")
    print(f"ClickHouse: {CH_USER}@{CH_HOST}:{CH_PORT}/{CH_DB}")

    client = get_client()
    print(f"Сервер   : {client.command('SELECT version()')}")

    rule("DDL")
    run_ddl(client)

    con = duckdb.connect(str(WAREHOUSE), read_only=True)
    con.execute("SET TimeZone='UTC'")

    first = load(client, con)
    rule("ИТОГ ПРОГОНА 1")
    show_counts(first, "  ClickHouse после первой загрузки:")

    if True:
        print("\n" + "#" * 78)
        print("# ВТОРОЙ ПРОГОН — проверяем, что повторная загрузка не плодит дубли")
        print("#" * 78)
        second = load(client, con)
        rule("СРАВНЕНИЕ ПРОГОНОВ")
        for t in TABLES:
            ratio = second[t] / first[t] if first[t] else 1
            print(f"   {t:<22} {first[t]:>9,} -> {second[t]:>9,}   "
                  f"{ratio:.2f}x {'OK' if ratio == 1 else '!!! ДУБЛИ !!!'}")
        ratio = float(second["_gmv"]) / float(first["_gmv"])
        print(f"   {'GMV_delivered':<22} {first['_gmv']:>19} -> "
              f"{second['_gmv']:>19}   {ratio:.2f}x "
              f"{'OK' if ratio == 1 else '!!! ИЗМЕНИЛОСЬ !!!'}")

    con.close()
    client.close()


if __name__ == "__main__":
    main()