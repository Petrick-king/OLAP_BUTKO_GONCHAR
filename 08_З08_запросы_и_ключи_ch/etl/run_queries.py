#!/usr/bin/env python3
"""Выполнение запросов З04 против ClickHouse (практика З08).

    python 08_З08_запросы_и_ключи_ch/etl/run_queries.py
    python 08_З08_запросы_и_ключи_ch/etl/run_queries.py --file sql/partitions.sql
    python 08_З08_запросы_и_ключи_ch/etl/run_queries.py \
        --out 08_З08_запросы_и_ключи_ch/evidence/queries_raw.txt

ЗАЧЕМ ОТДЕЛЬНЫЙ СКРИПТ. В ClickHouse нет «запроса на файл», а
`clickhouse_connect` не возвращает несколько результатов из одной строки.
Файл размечен строкой `-- @query N. заголовок`: для clickhouse-client это
обычный комментарий, для скрипта — граница между запросами. Скрипт идёт
по маркерам, выполняет запросы по одному и печатает каждый результат
таблицей — чтобы в отчёте было видно не «ok», а сами числа.

Файл при этом остаётся обычным SQL: его можно скормить клиенту целиком,
  clickhouse-client --multiquery < sql/queries_clickhouse.sql

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

MARKER = "-- @query"


def parse_queries(text: str) -> list[tuple[str, str]]:
    """Делит файл по маркерам `-- @query N. заголовок`.

    Без маркеров возвращает один запрос — весь файл. Пустые куски
    (например, хвост из одних комментариев) отбрасываются.
    """
    queries: list[tuple[str, str]] = []
    title = ""
    body: list[str] = []

    def flush() -> None:
        code = "\n".join(body).strip()
        if _has_code(code):
            queries.append((title or f"Запрос {len(queries) + 1}", code))

    for line in text.splitlines():
        if line.startswith(MARKER):
            flush()
            title = line[len(MARKER):].strip()
            body = []
        else:
            body.append(line)
    flush()
    return queries


def _has_code(chunk: str) -> bool:
    """Есть ли в куске хоть одна строка, не являющаяся комментарием."""
    for line in chunk.splitlines():
        s = line.strip()
        if s and not s.startswith("--"):
            return True
    return False


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
    ap.add_argument("--file", default="sql/queries_clickhouse.sql",
                    help="SQL-файл с маркерами (по умолчанию sql/queries_clickhouse.sql)")
    ap.add_argument("--out", help="записать вывод в файл (для evidence/)")
    args = ap.parse_args()

    path = Path(args.file)
    if not path.is_absolute():
        path = WORK / path if (WORK / path).exists() else Path(args.file)
    if not path.exists():
        sys.exit(f"Нет файла {path}")

    queries = parse_queries(path.read_text(encoding="utf-8"))
    if not queries:
        sys.exit(f"В {path.name} нет ни одного запроса")

    client = clickhouse_connect.get_client(
        host=CH_HOST, port=CH_PORT, username=CH_USER,
        password=CH_PASS, database=CH_DB)

    log: list[str] = []
    failed = 0
    for i, (title, code) in enumerate(queries, 1):
        header = title if title[:1].isdigit() else f"{i}. {title}"
        print("=" * 78)
        print(header)
        print("=" * 78)
        try:
            result = client.query(code)
            cols = list(result.column_names)
            if cols == ["explain"]:
                # EXPLAIN возвращает одну строковую колонку — план читают
                # построчно, а не таблицей с одной ячейкой на строку.
                table = "\n".join(row[0] for row in result.result_rows)
            else:
                table = render(cols, result.result_rows)
        except Exception as exc:                              # noqa: BLE001
            table = f"!! ОШИБКА: {exc}"
            failed += 1
        print(table)
        print()
        log += [f"--- {header}", table, ""]

    client.close()

    if args.out:
        p = Path(args.out)
        if not p.is_absolute():
            p = PROJECT / p
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("\n".join(log), encoding="utf-8")
        print(f"Вывод сохранён: {p}")

    if failed:
        sys.exit(f"Запросов с ошибкой: {failed}")


if __name__ == "__main__":
    main()
