#!/usr/bin/env python3
"""Сверка DuckDB ↔ ClickHouse (практика З07, п.4).

    python 07_З07_загрузка_clickhouse/etl/reconcile.py
    python 07_З07_загрузка_clickhouse/etl/reconcile.py --md > evidence/reconciliation_raw.md

Что сверяется и почему:

  1. COUNT(*) по каждой таблице          — не потеряли и не удвоили строки;
  2. SUM главной меры из canonical_metric.sql — то же число, что в DuckDB;
  3. Разбивка по месяцам                  — суммы сошлись, но разъехались по
                                          месяцам (частая ошибка переноса дат);
  4. Разбивка по регионам продавца       — то же для разрезов со справочниками;
  5. Топ-5 категорий                     — проверка денормализованного среза;
  6. Агрегаты по оплате и доставке       — Nullable-колонки переехали целыми;
  7. Контрольные суммы всех мер          — защита от тихой подмены значений.

Эталон — SQL главной метрики из ../../04_З04_аналитический_sql/sql/canonical_metric.sql
"""
from __future__ import annotations

import argparse
import os
import sys
from decimal import Decimal
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

CH_HOST = os.environ.get("CH_HOST", "localhost")
CH_PORT = int(os.environ.get("CH_PORT", 8123))
CH_USER = os.environ.get("CH_USER", "olist")
CH_PASS = os.environ.get("CH_PASS", "olist")
CH_DB = os.environ.get("CH_DB", "olist")

TABLES = ["fact_sales", "dim_product", "dim_product_scd2", "dim_seller",
          "dim_customer", "dim_date", "dim_category", "dim_geo"]

# Меры, которые обязаны совпасть до копейки.
MEASURES = [
    ("GMV доставленных заказов", "amount",
     "WHERE order_status = 'delivered'", 2),
    ("Оборот всех заказов", "amount", "", 2),
    ("Доставка (freight)", "freight_value", "WHERE order_status = 'delivered'", 2),
    ("Продано штук (qty)", "qty", "", 0),
    ("Доставленные позиции", "is_delivered", "", 0),
    ("Опоздавшие доставки", "is_late", "", 0),
    ("Сумма дней доставки", "delivery_days", "", 0),
]


def money(v) -> str:
    return f"{Decimal(str(v)):,.2f}"


def norm(v) -> Decimal:
    return Decimal(str(v)).quantize(Decimal("0.01"))


class Report:
    def __init__(self, md: bool):
        self.md = md
        self.fail = 0
        self.lines: list[str] = []

    def head(self, title: str) -> None:
        self.lines.append(f"\n## {title}\n")
        self.lines.append("| Показатель | DuckDB | ClickHouse | Разница | Итог |")
        self.lines.append("|---|---:|---:|---:|:---:|")

    def row(self, name: str, a, b, dec: int = 2, ok: bool | None = None) -> None:
        fa = money(a) if dec else f"{Decimal(str(a)):,.0f}"
        fb = money(b) if dec else f"{Decimal(str(b)):,.0f}"
        diff = Decimal(str(b)) - Decimal(str(a))
        good = (diff == 0) if ok is None else ok
        if not good:
            self.fail += 1
        fd = money(diff) if dec else f"{diff:,.0f}"
        self.lines.append(
            f"| {name} | {fa} | {fb} | {fd} | {'✅' if good else '❌ РАСХОЖДЕНИЕ'} |")

    def note(self, text: str) -> None:
        self.lines.append(f"\n{text}\n")

    def emit(self) -> None:
        out = "\n".join(self.lines)
        print(out)
        if self.fail:
            print(f"\n!!! РАСХОЖДЕНИЙ: {self.fail}")
            sys.exit(1)
        print("\n✅ Расхождений нет.")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--md", action="store_true")
    args = ap.parse_args()

    con = duckdb.connect(str(WAREHOUSE), read_only=True)
    con.execute("SET TimeZone='UTC'")
    ch = clickhouse_connect.get_client(
        host=CH_HOST, port=CH_PORT, username=CH_USER,
        password=CH_PASS, database=CH_DB)

    r = Report(args.md)

    def q(sql: str) -> list:
        """Все строки ответа ClickHouse."""
        return ch.query(sql).result_rows

    def q1(sql: str):
        """Скаляр из первой строки."""
        return ch.query(sql).result_rows[0][0]

    # ── 1. Число строк ───────────────────────────────────────────────────
    r.head("1. Число строк в таблицах")
    for t in TABLES:
        r.row(t, con.execute(f"SELECT count(*) FROM {t}").fetchone()[0],
              q1(f"SELECT count() FROM olist.{t}"), dec=0)

    # ── 2. Главная метрика (эталон из З04) ───────────────────────────────
    r.head("2. Главная метрика — эталон из `04_З04/sql/canonical_metric.sql`")
    canonical = """
        SELECT
            'GMV_delivered'                      AS metric_name,
            'Сумма amount по заказам в статусе delivered, BRL'
                                               AS metric_definition,
            SUM(amount)                          AS metric_value,
            COUNT(*)                             AS positions_count,
            COUNT(DISTINCT order_id)             AS orders_count
        FROM fact_sales
        WHERE order_status = 'delivered'
    """
    d = con.execute(canonical).fetchone()
    c = q(
        "SELECT toString(round(sum(amount), 2)), count(), uniqExact(order_id) "
        "FROM olist.fact_sales WHERE order_status = 'delivered'")[0]
    r.row("metric_value, BRL", d[2], c[0])
    r.row("positions_count", d[3], c[1], dec=0)
    r.row("orders_count", d[4], c[2], dec=0)
    r.note("> Эталон зафиксирован на З04: **13 221 498.11 BRL**. "
           "ClickHouse обязан дать то же число до копейки.")

    # ── 3. Все меры ──────────────────────────────────────────────────────
    r.head("3. Контрольные суммы всех мер")
    for name, col, where, dec in MEASURES:
        d = con.execute(f"SELECT sum({col}) FROM fact_sales {where}").fetchone()[0]
        c = q1(f"SELECT sum({col}) FROM olist.fact_sales {where}")
        r.row(name, d or 0, c or 0, dec=dec)

    # payment_value_order — единственная мера, которую НЕЛЬЗЯ суммировать
    # по факту. Считаем по заказам, как и предписано в паспорте.
    d = con.execute("SELECT sum(p) FROM (SELECT max(payment_value_order) p "
                    "FROM fact_sales GROUP BY order_id)").fetchone()[0]
    c = q1("SELECT sum(p) FROM (SELECT order_id, max(payment_value_order) p "
           "FROM olist.fact_sales GROUP BY order_id)")
    r.row("Оплата заказов (после схлопывания)", d or 0, c or 0)

    # ── 4. Помесячная разбивка ───────────────────────────────────────────
    r.head("4. Оборот по месяцам — проверка переноса дат")
    d = con.execute("""
        SELECT strftime(order_date_key, '%Y-%m') ym, sum(amount)
        FROM fact_sales WHERE order_status = 'delivered'
        GROUP BY 1 ORDER BY 1""").fetchall()
    c = q("""
        SELECT toString(toStartOfMonth(order_date_key)), sum(amount)
        FROM olist.fact_sales WHERE order_status = 'delivered'
        GROUP BY 1 ORDER BY 1""")
    dmap = {k: v for k, v in d}
    cmap = {str(k)[:7]: v for k, v in c}
    months = sorted(set(dmap) | set(cmap))
    for m in months:
        r.row(m, dmap.get(m, 0), cmap.get(m, 0))

    # ── 5. Регионы продавца ──────────────────────────────────────────────
    r.head("5. Оборот по регионам продавца — проверка JOIN со справочником")
    d = con.execute("""
        SELECT s.seller_state, sum(f.amount)
        FROM fact_sales f JOIN dim_seller s ON s.seller_id = f.seller_id
        WHERE f.order_status = 'delivered' GROUP BY 1 ORDER BY 1""").fetchall()
    c = q("""
        SELECT s.seller_state, sum(f.amount)
        FROM olist.fact_sales f
        JOIN olist.dim_seller s ON s.seller_id = f.seller_id
        WHERE f.order_status = 'delivered' GROUP BY 1 ORDER BY 1""")
    cmap = dict(c)
    for st, val in d:
        r.row(st, val, cmap.get(st, 0))

    # ── 6. Категории (денормализованный срез + версия SCD2) ──────────────
    r.head("6. Оборот по категориям — денормализованный срез в факте")
    d = con.execute("""
        SELECT category_name_pt, sum(amount)
        FROM fact_sales WHERE order_status = 'delivered'
        GROUP BY 1 ORDER BY 1""").fetchall()
    c = q("""
        SELECT category_name_pt, sum(amount)
        FROM olist.fact_sales WHERE order_status = 'delivered'
        GROUP BY 1 ORDER BY 1""")
    cmap = dict(c)
    for cat, val in d:
        r.row(cat, val, cmap.get(cat, 0))

    r.head("7. Оборот по категории НА ДАТУ ПОКУПКИ (SCD2, версия лежит в факте)")
    d = con.execute("""
        SELECT EXTRACT(YEAR FROM s.order_date_key) y, s.category_at_sale_pt, sum(s.amount)
        FROM fact_sales_scd2 s
        WHERE s.order_status = 'delivered'
          AND s.category_at_sale_pt IN ('informatica_acessorios', 'audio')
        GROUP BY 1, 2 ORDER BY 2, 1""").fetchall()
    c = q("""
        SELECT toString(toYear(order_date_key)), category_at_sale_pt, sum(amount)
        FROM olist.fact_sales
        WHERE order_status = 'delivered'
          AND category_at_sale_pt IN ('informatica_acessorios', 'audio')
        GROUP BY 1, 2 ORDER BY 2, 1""")
    cmap = {(str(y), cat): v for y, cat, v in c}
    for y, cat, val in d:
        r.row(f"{int(y)} · {cat}", val, cmap.get((str(int(y)), cat), 0))

    # ── 8. Строка факта целиком ──────────────────────────────────────────
    r.head("8. Одна и та же строка факта в обеих базах")
    key = "fe59a1e006df3ac42bf0ceb876d70969"
    d = con.execute("SELECT order_id, order_item_id, order_date_key, amount, qty, "
                    "freight_value, category_name_pt FROM fact_sales "
                    "WHERE product_id = ? ORDER BY order_item_sk LIMIT 3", [key]).fetchall()
    c = q("SELECT order_id, order_item_id, order_date_key, amount, qty, "
                   "freight_value, category_name_pt FROM olist.fact_sales "
                   f"WHERE product_id = '{key}' ORDER BY order_item_sk LIMIT 3")
    for a, b in zip(d, c):
        same = (a[0] == b[0] and a[1] == b[1] and str(a[2]) == str(b[2])
                and norm(a[3]) == norm(b[3]) and a[4] == b[4]
                and norm(a[5]) == norm(b[5]) and a[6] == b[6])
        r.row(f"{a[0][:12]}… поз. {a[1]} ({a[6]}, {money(a[3])}, qty={a[4]})",
              money(a[3]), money(b[3]), ok=same)

    r.emit()
    con.close()
    ch.close()


if __name__ == "__main__":
    main()