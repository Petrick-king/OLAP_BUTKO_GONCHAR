#!/usr/bin/env python3
"""Сборка и демонстрация SCD2 по dim_product (практика З06).

    python 06_З06_scd2_или_cut/etl/build_scd2.py
    python 06_З06_scd2_или_cut/etl/build_scd2.py --md   # вывод в отчёт

Шаги:
  1. ddl_dim_product_scd2.sql  — справочник с версиями + бэкфилл версии 1
                                + представление fact_sales_scd2
  2. scd2_change_2018.sql      — смена атрибута: закрыть старую версию,
                                 вставить новую
  3. scd2_demo.sql             — четыре запроса-демонстрации

Идемпотентно: каждый запуск пересоздаёт dim_product_scd2 с нуля.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

try:
    import duckdb
except ModuleNotFoundError:
    sys.exit("Нужен duckdb:  pip install duckdb")

WORK = Path(__file__).resolve().parents[1]
PROJECT = WORK.parents[0]
SQL = WORK / "sql"
WAREHOUSE = PROJECT / "data" / "warehouse.duckdb"

SEP = re.compile(r"^--\s*[═─]{3,}.*$", re.M)


def blocks(path: Path) -> list[str]:
    parts = SEP.split(path.read_text(encoding="utf-8"))
    out, pending = [], []
    for part in parts:
        part = part.strip()
        has_code = any(ln.strip() and not ln.strip().startswith("--")
                       for ln in part.splitlines())
        if has_code:
            out.append("\n".join([*pending, part]).strip())
            pending = []
        elif part:
            pending.append(part)
    return out


def header(sql: str) -> str:
    for line in sql.splitlines():
        if line.startswith("--"):
            s = line.lstrip("-").strip()
            if s and not set(s) <= set("═─ ") and not s.startswith("="):
                return s
    return ""


def render(cur) -> str:
    cols = [d[0] for d in cur.description]
    rows = cur.fetchall()

    def fmt(v):
        if v is None:
            return ""
        if isinstance(v, bool):
            return "да" if v else "нет"
        if isinstance(v, float):
            return f"{v:,.4f}".rstrip("0").rstrip(".")
        if isinstance(v, int):
            return f"{v:,}"
        return str(v)

    w = [max(len(c), *(len(fmt(r[i])) for r in rows)) if rows else len(c)
         for i, c in enumerate(cols)]
    out = ["| " + " | ".join(c.ljust(x) for c, x in zip(cols, w)) + " |",
           "|" + "|".join("-" * (x + 2) for x in w) + "|"]
    for r in rows:
        out.append("| " + " | ".join(fmt(v).rjust(x) for v, x in zip(r, w)) + " |")
    return "\n".join(out)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--md", action="store_true")
    args = ap.parse_args()

    if not WAREHOUSE.exists():
        sys.exit(f"Нет базы {WAREHOUSE}. Сначала прогоните З03 build_duckdb.py")

    con = duckdb.connect(str(WAREHOUSE))
    con.execute("SET TimeZone='UTC'")
    con.execute("SET home_directory='C:/Users/butko/AppData/Local/Temp/opencode'")

    print("=" * 78)
    print("SCD2 ПО dim_product — СБОРКА И ДЕМОНСТРАЦИЯ (З06)")
    print("=" * 78)

    for name in ["ddl_dim_product_scd2.sql", "scd2_change_2018.sql"]:
        print(f"\n>> {name} ... ", end="", flush=True)
        con.execute((SQL / name).read_text(encoding="utf-8"))
        print("ok")

    print("\nСводка:")
    for label, q in [
        ("строк в dim_product_scd2", "SELECT COUNT(*) FROM dim_product_scd2"),
        ("уникальных товаров       ", "SELECT COUNT(DISTINCT product_id) FROM dim_product_scd2"),
        ("из них с 2 версиями      ",
         "SELECT COUNT(*) FROM (SELECT product_id FROM dim_product_scd2 "
         "GROUP BY 1 HAVING COUNT(*) > 1)"),
        ("текущих версий           ", "SELECT COUNT(*) FROM dim_product_scd2 WHERE is_current"),
        ("строк в fact_sales       ", "SELECT COUNT(*) FROM fact_sales"),
        ("строк в fact_sales_scd2  ", "SELECT COUNT(*) FROM fact_sales_scd2"),
    ]:
        print(f"   {label} {con.execute(q).fetchone()[0]:>9,}")

    print("\n" + "=" * 78)
    print("ДЕМОНСТРАЦИЯ — SQL ИЗ scd2_demo.sql")
    print("=" * 78)
    for b in blocks(SQL / "scd2_demo.sql"):
        title = header(b)
        print(f"\n{'-' * 78}\n{title}\n{'-' * 78}")
        table = render(con.execute(b))
        print(table)
        if args.md:
            print(f"\n**{title}**\n")
            print(table)
            print()

    con.close()


if __name__ == "__main__":
    main()