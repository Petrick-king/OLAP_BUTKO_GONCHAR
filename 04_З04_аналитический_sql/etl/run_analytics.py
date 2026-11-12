#!/usr/bin/env python3
"""Прогон аналитических запросов З04 и печать результатов.

    python 04_З04_аналитический_sql/etl/run_analytics.py
    python 04_З04_аналитический_sql/etl/run_analytics.py --md > evidence/analytics_results.md

Разбирает sql/analytics.sql и sql/canonical_metric.sql по разделителям
«-- ═══...» и выполняет каждый блок отдельно.
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
    """Режет файл по разделителям.

    Разделитель — строка из «═» или «─». Куски, где нет исполняемого SQL
    (только комментарии), приклеиваются к следующему куску: иначе
    заголовок вопроса оторвался бы от самого запроса.
    """
    text = path.read_text(encoding="utf-8")
    parts = SEP.split(text)
    out, pending = [], []
    for part in parts:
        part = part.strip()
        has_code = any(ln.strip() and not ln.strip().startswith("--")
                       for ln in part.splitlines())
        if has_code:
            out.append("\n".join([*(c for c in pending if c), part]).strip())
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
        if isinstance(v, float):
            return f"{v:,.4f}".rstrip("0").rstrip(".")
        if isinstance(v, int):
            return f"{v:,}"
        return str(v)

    widths = [max(len(c), *(len(fmt(r[i])) for r in rows)) if rows else len(c)
              for i, c in enumerate(cols)]
    out = ["| " + " | ".join(c.ljust(w) for c, w in zip(cols, widths)) + " |",
           "|" + "|".join("-" * (w + 2) for w in widths) + "|"]
    for r in rows:
        out.append("| " + " | ".join(fmt(v).rjust(w) for v, w in zip(r, widths)) + " |")
    return "\n".join(out)


def run(con, path: Path, md: bool) -> None:
    for i, b in enumerate(blocks(path), 1):
        title = header(b)
        print(f"\n{'=' * 78}\n{title}\n{'=' * 78}")
        cur = con.execute(b)
        table = render(cur)
        print(table)
        if md:
            print(f"\n**{title}**\n")
            print(table)
            print()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--md", action="store_true", help="формат для отчёта")
    args = ap.parse_args()

    if not WAREHOUSE.exists():
        sys.exit(f"Нет базы {WAREHOUSE}. Сначала: python 03_З03_витрина_duckdb/etl/build_duckdb.py")

    con = duckdb.connect(str(WAREHOUSE), read_only=True)
    con.execute("SET TimeZone='UTC'")

    print("####################  ЭТАЛОН ГЛАВНОЙ МЕТРИКИ  ####################")
    run(con, SQL / "canonical_metric.sql", args.md)

    print("\n\n####################  АНАЛИТИЧЕСКИЕ ЗАПРОСЫ  ####################")
    run(con, SQL / "analytics.sql", args.md)

    con.close()


if __name__ == "__main__":
    main()