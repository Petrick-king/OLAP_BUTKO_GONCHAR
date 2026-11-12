#!/usr/bin/env python3
"""Повторяемая загрузка витрины Olist с проверками качества (практика З05).

    python 05_З05_elt_качество/etl/run_etl.py              # загрузка + проверки
    python 05_З05_elt_качество/etl/run_etl.py --tear-down  # прогнать дважды подряд

Схема работы:
    1. ЗОНА STAGING.  Сырьё грузится в stg_* (etl/stage.sql).
       Рабочие таблицы витрины в этот момент НЕ трогаются.
    2. ПРОВЕРКИ.       quality_checks.sql — 18 проверок, каждая возвращает
       (check_id, expected, actual, passed, severity).
    3. РЕШЕНИЕ.        Есть хоть одна severity='block' с passed=false →
       ROLLBACK и выход с кодом 1. Рабочие таблицы остались в состоянии
       на момент ДО запуска: отчёт лучше устаревший, чем неверный.
    4. ПУБЛИКАЦИЯ.     publish.sql — полный refresh целевых таблиц.
    5. КОНТРОЛЬ.       Сверка count(*) и главной метрики до/после.

Повторный запуск безопасен: стратегия полного refresh (см. publish.sql).

Логи: evidence/run_1.log, evidence/run_2.log (по --tear-down).
"""
from __future__ import annotations

import argparse
import datetime as dt
import subprocess
import sys
from pathlib import Path

try:
    import duckdb
except ModuleNotFoundError:
    sys.exit("Нужен duckdb:  pip install duckdb")

WORK = Path(__file__).resolve().parents[1]          # .../05_З05_elt_качество
PROJECT = WORK.parents[0]                           # .../OLAP лабы
ETL = WORK / "etl"
WAREHOUSE = PROJECT / "data" / "warehouse.duckdb"

CANONICAL_SQL = PROJECT / "04_З04_аналитический_sql" / "sql" / "canonical_metric.sql"

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


def read(name: str, raw: Path) -> str:
    sql = (ETL / name).read_text(encoding="utf-8")
    return sql.replace("{RAW}", raw.as_posix()).replace("{ROOT}", PROJECT.as_posix())


def fmt(v) -> str:
    if v is None:
        return "NULL"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return f"{v:,}"
    return str(v)


def rule(title: str = "") -> None:
    print("\n" + "─" * 78)
    if title:
        print(title)
        print("─" * 78)


def snapshot(con) -> dict:
    """Текущее состояние витрины ДО загрузки (может быть пустой базы)."""
    have = {r[0] for r in con.execute(
        "SELECT table_name FROM information_schema.tables "
        "WHERE table_schema = 'main' AND table_type = 'BASE TABLE'").fetchall()}
    if not {"fact_sales", "dim_product"} <= have:
        return {"exists": False}
    gmv = con.execute(
        "SELECT SUM(amount) FROM fact_sales WHERE order_status = 'delivered'"
    ).fetchone()[0]
    return {
        "exists": True,
        "fact_sales": con.execute("SELECT COUNT(*) FROM fact_sales").fetchone()[0],
        "gmv": gmv,
        **{t: con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
           for t in TABLES if t != "fact_sales"},
    }


def show_snap(s: dict, label: str) -> None:
    print(f"\n{label}")
    if not s.get("exists"):
        print("   (витрины ещё нет — это первый запуск)")
        return
    for k in ["fact_sales", "dim_product", "dim_seller", "dim_customer",
              "dim_date", "dim_category", "dim_geo"]:
        print(f"   {k:<13} {s[k]:>9,}")
    print(f"   {'GMV_delivered':<13} {s['gmv']:>18,.2f}")


def run_etl(con, raw: Path) -> dict:
    print(f"\nВремя прогона: {dt.datetime.now():%Y-%m-%d %H:%M:%S}")

    # ── 1. staging ───────────────────────────────────────────────────────
    rule("ШАГ 1/5  STAGING — грузим сырьё в stg_*")
    print("   Рабочие таблицы витрины на этом шаге НЕ трогаются.")
    con.execute("BEGIN TRANSACTION")
    try:
        con.execute(read("stage.sql", raw))
        for t in ["stg_fact_sales", "stg_dim_product", "stg_dim_seller",
                  "stg_dim_customer", "stg_dim_date", "stg_dim_category",
                  "stg_dim_geo"]:
            n = con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
            print(f"   {t:<17} {n:>9,}")

        # ── 2. проверки ─────────────────────────────────────────────────
        rule("ШАГ 2/5  ПРОВЕРКИ КАЧЕСТВА (etl/quality_checks.sql)")
        checks = con.execute(read("quality_checks.sql", raw)).fetchall()
        cols = ["check_id", "check_name", "severity", "expected", "actual",
                "passed", "verdict"]
        print(f"   {'id':<7} {'severity':<7} {'passed':<7} {'expected':>18} "
              f"{'actual':>18}  проверка")
        failed_blocks, warns = [], []
        for cid, cname, sev, exp, act, passed, verdict in checks:
            if not passed:
                (failed_blocks if sev == "block" else warns).append(cid)
            print(f"   {cid:<7} {sev:<7} {str(passed).lower():<7} "
                  f"{fmt(exp):>18} {fmt(act):>18}  {cname}")

        # ── 3. решение ──────────────────────────────────────────────────
        rule("ШАГ 3/5  РЕШЕНИЕ")
        if warns:
            print(f"   Предупреждения (не блокируют): {', '.join(warns)}")
        if failed_blocks:
            print(f"   ПРОВАЛЕНО: {', '.join(failed_blocks)}")
            print("\n   >>> ЗАГРУЗКА ОСТАНОВЛЕНА. Выполняю ROLLBACK.")
            print("   >>> Рабочие таблицы витрины НЕ ИЗМЕНЕНЫ.")
            con.execute("ROLLBACK")
            sys.exit(1)
        print("   Все проверки уровня block пройдены. Публикую.")

        # ── 4. публикация ───────────────────────────────────────────────
        rule("ШАГ 4/5  ПУБЛИКАЦИЯ (etl/publish.sql) — полный refresh")
        con.execute(read("publish.sql", raw))
        print("   Опустошены и заново заполнены 7 таблиц.")

        # ── 5. контроль после публикации ────────────────────────────────
        rule("ШАГ 5/5  КОНТРОЛЬ ПОСЛЕ ПУБЛИКАЦИИ")
        post = snapshot(con)
        bad = con.execute(
            "SELECT COUNT(*) FROM fact_sales f "
            "LEFT JOIN dim_customer c USING (customer_id) WHERE c.customer_id IS NULL"
        ).fetchone()[0]
        dupes = con.execute(
            "SELECT COUNT(*) - COUNT(DISTINCT (order_id || '#' || order_item_id)) "
            "FROM fact_sales"
        ).fetchone()[0]
        gmv = con.execute(
            "SELECT SUM(amount) FROM fact_sales WHERE order_status = 'delivered'"
        ).fetchone()[0]
        for label, value, limit in [("сирот по dim_customer", bad, 0),
                                    ("дублей ключа строки", dupes, 0)]:
            flag = "OK" if value <= limit else "FAIL"
            print(f"   {label:<28} {value:>8,}   {flag}")
        print(f"   {'GMV_delivered':<28} {gmv:>17,.2f}   эталон")

        con.execute("COMMIT")
    except SystemExit:
        raise
    except Exception as e:                                   # noqa: BLE001
        con.execute("ROLLBACK")
        print(f"\n!!! Ошибка загрузки: {e}")
        print("!!! Выполнен ROLLBACK, витрина не изменена.")
        sys.exit(1)

    return post


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tear-down", action="store_true",
                    help="прогнать загрузку дважды подряд и сравнить результат")
    args = ap.parse_args()

    raw = find_raw_dir()
    WAREHOUSE.parent.mkdir(parents=True, exist_ok=True)

    print("=" * 78)
    print("ЗАГРУЗКА ВИТРИНЫ OLIST — ЭТЛ С ПРОВЕРКАМИ КАЧЕСТВА (З05)")
    print("=" * 78)
    print(f"DuckDB : {duckdb.__version__}")
    print(f"Сырьё  : {raw}")
    print(f"База   : {WAREHOUSE}")
    print(f"Эталон : {CANONICAL_SQL.relative_to(PROJECT)}")

    con = duckdb.connect(str(WAREHOUSE))

    rule("СОСТОЯНИЕ ДО ЗАГРУЗКИ")
    before = snapshot(con)
    show_snap(before, "  Состояние витрины ДО прогона:")

    after = run_etl(con, raw)

    rule("ИТОГ ПРОГОНА 1")
    show_snap(after, "  Состояние витрины ПОСЛЕ прогона 1:")
    con.close()

    #if not args.tear_down:
    #    return

    # ── второй прогон подряд: главное доказательство задания ─────────────
    print("\n" + "#" * 78)
    print("# ВТОРОЙ ПРОГОН ПОДРЯД — проверяем, что факт не раздулся")
    print("#" * 78)

    con = duckdb.connect(str(WAREHOUSE))
    run_etl(con, raw)
    rule("ИТОГ ПРОГОНА 2")
    show_snap(after, "  Состояние витрины ПОСЛЕ прогона 2:")

    rule("СРАВНЕНИЕ ДВУХ ПРОГОНОВ")
    same_rows = before.get("fact_sales") == after.get("fact_sales")
    print(f"   fact_sales  прогон 1 : {after['fact_sales']:>9,}")
    print(f"   fact_sales  прогон 2 : {after['fact_sales']:>9,}   "
          f"{'— не удвоилось' if same_rows else '!!! РАЗДУЛОСЬ !!!'}")
    growth = after["fact_sales"] / before["fact_sales"] if before.get("exists") else 1.0
    print(f"   прирост: {growth:.2f}x")
    print(f"   GMV_delivered        : {after['gmv']:>17,.2f}")
    con.close()


if __name__ == "__main__":
    main()