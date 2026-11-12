#!/usr/bin/env python3
"""Сборка и сверка сводной таблицы З09.

    python 09_З09_mv_витрина/etl/build_mv.py
    python 09_З09_mv_витрина/etl/build_mv.py --rebuild-twice
    python 09_З09_mv_витрина/etl/build_mv.py --out 09_З09_mv_витрина/evidence/mv_checks_raw.txt

ЧТО ДЕЛАЕТ
  1. выполняет sql/ddl_mv.sql        — таблица сводки + materialized view;
  2. выполняет sql/rebuild_mv.sql    — TRUNCATE + INSERT ... SELECT из факта;
  3. сверяет суммы и число строк сводки с детальной таблицей;
  4. с --rebuild-twice повторяет п.2–3 и убеждается, что пересборка
     идемпотентна: строк не прибавилось, сумма не изменилась.

ПОЧЕМУ ПЕРЕСБОРКА, А НЕ «ПУСТЬ VIEW САМА»
  Загрузка факта в проекте — полный refresh (TRUNCATE + INSERT). View
  срабатывает на каждую вставку, поэтому после перезагрузки в сводке
  лежали бы и старые, и новые строки. Порядок «очистить сводку → залить
  заново» делает результат воспроизводимым при любом числе прогонов.

Соединение: CH_HOST / CH_PORT / CH_USER / CH_PASS / CH_DB,
по умолчанию olist:olist на http://localhost:8123, база olist.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

try:
    import clickhouse_connect
except ModuleNotFoundError:
    sys.exit("Нужен clickhouse-connect:  pip install clickhouse-connect")

WORK = Path(__file__).resolve().parents[1]
PROJECT = WORK.parents[0]
SQL_DIR = WORK / "sql"

CH_HOST = os.environ.get("CH_HOST", "localhost")
CH_PORT = int(os.environ.get("CH_PORT", 8123))
CH_USER = os.environ.get("CH_USER", "olist")
CH_PASS = os.environ.get("CH_PASS", "olist")
CH_DB = os.environ.get("CH_DB", "olist")

SUMMARY = "olist.mv_gmv_day_category"
FACT = "olist.fact_sales"

# Эталон: то же число, что даёт canonical_metric.sql.
CANONICAL_SQL = f"""
SELECT sum(amount), count(), uniqExact(order_id)
FROM {FACT}
WHERE order_status = 'delivered'
"""

# Сводка читается через sum(), а не «как есть»: AggregatingMergeTree
# хранит по строке на часть, и до слияния частей один и тот же
# день × категория может встретиться дважды.
SUMMARY_SQL = f"""
SELECT sum(gmv_brl), sum(freight_brl), sum(qty), sum(positions),
       count(DISTINCT order_date_key, category_name_pt)
FROM {SUMMARY}
"""


def split_by_markers(text: str) -> list[tuple[str, str]]:
    """Делит файл по строкам `-- @step N. заголовок`."""
    parts: list[tuple[str, str]] = []
    title = ""
    buf: list[str] = []

    def flush() -> None:
        code = "\n".join(buf).strip()
        if any(line.strip() and not line.strip().startswith("--")
               for line in code.splitlines()):
            parts.append((title or f"Шаг {len(parts) + 1}", code))

    for line in text.splitlines():
        if line.startswith("-- @step"):
            flush()
            title = line[len("-- @step"):].strip()
            buf = []
        else:
            buf.append(line)
    flush()
    return parts


def fmt(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "да" if value else "нет"
    if isinstance(value, float):
        return f"{value:,.6f}".rstrip("0").rstrip(".")
    if isinstance(value, int):
        return f"{value:,}"
    return str(value)


def render(columns: list[str], rows: list[tuple]) -> str:
    cells = [[fmt(v) for v in row] for row in rows]
    widths = [max([len(c), *(len(r[i]) for r in cells)]) for i, c in enumerate(columns)]
    out = ["| " + " | ".join(c.ljust(w) for c, w in zip(columns, widths)) + " |",
           "|" + "|".join("-" * (w + 2) for w in widths) + "|"]
    for row in cells:
        out.append("| " + " | ".join(v.rjust(w) for v, w in zip(row, widths)) + " |")
    return "\n".join(out)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rebuild-twice", action="store_true",
                    help="пересобрать сводку дважды и доказать идемпотентность")
    ap.add_argument("--out", help="записать протокол в файл")
    args = ap.parse_args()

    client = clickhouse_connect.get_client(
        host=CH_HOST, port=CH_PORT, username=CH_USER,
        password=CH_PASS, database=CH_DB)

    log: list[str] = []
    failures = 0

    print("=" * 78)
    print("1. DDL: таблица сводки + materialized view")
    print("=" * 78)
    ddl = (SQL_DIR / "ddl_mv.sql").read_text(encoding="utf-8")
    # DDL-файл без маркеров: выполняем как есть, по одному оператору.
    for stmt in [s.strip() for s in ddl.split(";") if s.strip()]:
        code = "\n".join(line for line in stmt.splitlines()
                         if not line.strip().startswith("--")).strip()
        if not code:
            continue
        client.command(code)
    print("DDL применён.")
    log.append("DDL применён.")

    rebuild = split_by_markers((SQL_DIR / "rebuild_mv.sql").read_text(encoding="utf-8"))

    def do_rebuild(round_no: int) -> tuple:
        print()
        print("=" * 78)
        print(f"{round_no}. Пересборка сводки: TRUNCATE → INSERT ... SELECT")
        print("=" * 78)
        for title, code in rebuild:
            client.command(code)
            print(f"  {title}: выполнено")
            log.append(f"  {title}: выполнено")

        fact_gmv, fact_pos, fact_orders = client.query(CANONICAL_SQL).result_rows[0]
        sum_gmv, sum_freight, sum_qty, sum_pos, sum_keys = \
            client.query(SUMMARY_SQL).result_rows[0]

        print()
        print(render(
            ["что сверяем", "детальный факт", "сводка", "итог"],
            [
                ("оборот, BRL", fmt(fact_gmv), fmt(sum_gmv),
                 "OK" if fact_gmv == sum_gmv else "РАСХОЖДЕНИЕ"),
                ("позиций факта", fmt(fact_pos), fmt(sum_pos),
                 "OK" if fact_pos == sum_pos else "РАСХОЖДЕНИЕ"),
                ("штук (qty)", fmt(fact_pos), fmt(sum_qty),
                 "OK" if fact_pos == sum_qty else "РАСХОЖДЕНИЕ"),
                ("строк сводки (день × категория)", "—", fmt(sum_keys), "—"),
            ]))
        log.append(render(
            ["что сверяем", "детальный факт", "сводка", "итог"],
            [
                ("оборот, BRL", fmt(fact_gmv), fmt(sum_gmv),
                 "OK" if fact_gmv == sum_gmv else "РАСХОЖДЕНИЕ"),
                ("позиций факта", fmt(fact_pos), fmt(sum_pos),
                 "OK" if fact_pos == sum_pos else "РАСХОЖДЕНИЕ"),
                ("штук (qty)", fmt(fact_pos), fmt(sum_qty),
                 "OK" if fact_pos == sum_qty else "РАСХОЖДЕНИЕ"),
                ("строк сводки (день × категория)", "—", fmt(sum_keys), "—"),
            ]))
        return fact_gmv, sum_gmv, fact_pos, sum_pos, sum_qty, sum_keys

    first = do_rebuild(2)
    failures += 0 if (first[0] == first[1] and first[2] == first[3]
                      and first[2] == first[4]) else 1

    if args.rebuild_twice:
        second = do_rebuild(3)
        same = first == second
        print()
        print(f"Идемпотентность: вторая пересборка дала те же числа — "
              f"{'OK' if same else 'РАСХОЖДЕНИЕ'}")
        log.append(f"Идемпотентность: {'OK' if same else 'РАСХОЖДЕНИЕ'}")
        failures += 0 if same else 1

    # Что вообще лежит в сводке — 5 строк для наглядности.
    print()
    print("=" * 78)
    print("4. Как выглядят строки сводки (первые 5 по обороту)")
    print("=" * 78)
    sample = client.query(f"""
        SELECT order_date_key, category_name_pt,
               sum(gmv_brl) AS gmv_brl, sum(qty) AS qty, sum(positions) AS positions
        FROM {SUMMARY}
        GROUP BY order_date_key, category_name_pt
        ORDER BY gmv_brl DESC
        LIMIT 5
    """)
    table = render(list(sample.column_names), sample.result_rows)
    print(table)
    log += [table, ""]

    client.close()

    verdict = ("ИТОГ: сводка сходится с детальной таблицей."
               if not failures else f"ИТОГ: проблем — {failures}.")
    print()
    print(verdict)
    log += ["", verdict, ""]

    if args.out:
        p = Path(args.out)
        if not p.is_absolute():
            p = PROJECT / p
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("\n".join(log), encoding="utf-8")
        print(f"Протокол сохранён: {p}")

    if failures:
        sys.exit(1)


if __name__ == "__main__":
    main()
