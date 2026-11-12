#!/usr/bin/env python3
"""Вывод проверочных запросов из sql/checks_duckdb.sql (практика З03, п.3).

    python 03_З03_витрина_duckdb/etl/run_checks.py
    python 03_З03_витрина_duckdb/etl/run_checks.py --out evidence/checks_raw.txt

ЗАЧЕМ ОТДЕЛЬНЫЙ СКРИПТ. В `sql/checks_duckdb.sql` восемь отдельных
проверочных запросов. Выполнить их «одним куском» и увидеть все
результаты нельзя: `con.execute()` с многооператорным текстом выполняет
все запросы, но возвращает результат ТОЛЬКО ПОСЛЕДНЕГО и ничего не
печатает. Поэтому файл разбирается на запросы, каждый выполняется
отдельно, и его результат печатается таблицей.

Точка с запятой внутри комментария `--` не должна делить файл на
запросы — разбор посимвольный, с учётом комментариев и строковых
литералов.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

try:
    import duckdb
except ModuleNotFoundError:
    sys.exit("Нужен duckdb:  pip install duckdb")

WORK = Path(__file__).resolve().parents[1]
PROJECT = WORK.parents[0]
SQL = WORK / "sql" / "checks_duckdb.sql"
WAREHOUSE = PROJECT / "data" / "warehouse.duckdb"

# Служебные строки-разделители: только эти символы — не содержание.
SEP_CHARS = set("─═-=—_~*#• \t")


def clean(line: str) -> str:
    """Убирает служебные символы по краям строки комментария."""
    s = line.strip().lstrip("-").strip()
    while s and s[0] in SEP_CHARS:
        s = s[1:]
    while s and s[-1] in SEP_CHARS:
        s = s[:-1]
    return s


def split_statements(sql: str) -> list[tuple[str, str, list[str]]]:
    """Делит текст на (заголовок, запрос, примечания).

    Между двумя запросами в файле лежат блоки комментариев:
      * последний блок перед запросом — его ЗАГОЛОВОК (первая осмысленная
        строка блока);
      * все предыдущие блоки — ПРИМЕЧАНИЯ к предыдущему запросу
        (например «Ожидание по сырью: …»).

    Блоки разделяются пустой строкой. Точка с запятой внутри комментария
    `--` запрос не завершает — разбор посимвольный, с учётом комментариев
    и строковых литералов.
    """
    # 1. Разбиваем файл на куски: код, комментарий, пустая строка.
    chunks: list[tuple[str, str]] = []          # (тип, текст)
    buf: list[str] = []
    kind = "code"
    i, n = 0, len(sql)
    in_line_comment = False
    in_string = False

    def flush():
        nonlocal buf, kind
        if buf:
            chunks.append((kind, "".join(buf)))
        buf = []

    while i < n:
        ch = sql[i]
        nxt = sql[i + 1] if i + 1 < n else ""

        if in_line_comment:
            buf.append(ch)
            if ch == "\n":
                in_line_comment = False
                flush()
                kind = "comment"
            i += 1
            continue

        if in_string:
            buf.append(ch)
            if ch == "'":
                if nxt == "'":
                    buf.append(nxt)
                    i += 2
                    continue
                in_string = False
            i += 1
            continue

        if ch == "-" and nxt == "-":
            flush()
            kind = "comment"
            in_line_comment = True
            buf.append(ch)
            i += 1
            continue
        if ch == "'":
            if not buf:
                flush()
                kind = "code"
            in_string = True
            buf.append(ch)
            i += 1
            continue
        if ch == ";":
            buf.append(ch)
            flush()
            kind = "code"
            i += 1
            continue

        if not buf and ch == "\n":
            flush()
            kind = "blank"
            buf.append(ch)
            flush()
            kind = "code"
            i += 1
            continue

        if not buf:
            flush()
            kind = "code"
        buf.append(ch)
        i += 1
    flush()

    # 2. Собираем запросы и комментарии между ними.
    stmts: list[tuple[str, str, list[str]]] = []
    code_parts: list[str] = []
    blocks: list[list[str]] = []              # блоки комментариев между запросами
    cur_block: list[str] = []
    pending: list[str] = []                   # накопленные блоки (не разобранные)

    def close_block():
        nonlocal cur_block
        if cur_block:
            blocks.append(cur_block)
            cur_block = []

    for kind, text in chunks:
        if kind == "comment":
            for line in text.splitlines():
                c = clean(line)
                if c:
                    cur_block.append(c)
        elif kind == "blank":
            close_block()
        else:                                   # код
            if not _has_code(text):
                continue
            code_parts.append(text)
            if ";" in text:
                code = "".join(code_parts).strip()
                code_parts = []
                close_block()
                # последний блок — заголовок этого запроса, остальные — примечания
                title = blocks[-1][0] if blocks else ""
                notes: list[str] = []
                for b in blocks[:-1]:
                    notes += b
                if stmts:
                    stmts[-1] = (stmts[-1][0], stmts[-1][1], stmts[-1][2] + notes)
                stmts.append((title, code, []))
                blocks = []
    return stmts


def _has_code(chunk: str) -> bool:
    for line in chunk.splitlines():
        s = line.strip()
        if s and not s.startswith("--"):
            return True
    return False


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
    ap.add_argument("--out", help="записать вывод в файл")
    args = ap.parse_args()

    if not WAREHOUSE.exists():
        sys.exit(f"Нет базы {WAREHOUSE}. Сначала: python 03_З03_витрина_duckdb/etl/build_duckdb.py")

    raw = PROJECT / "data" / "raw"
    sql = SQL.read_text(encoding="utf-8")
    sql = sql.replace("{RAW}", raw.as_posix()).replace("{ROOT}", PROJECT.as_posix())

    stmts = split_statements(sql)
    if not stmts:
        sys.exit("Не нашлось ни одного запроса — проверь формат sql/checks_duckdb.sql")

    con = duckdb.connect(str(WAREHOUSE), read_only=True)
    con.execute("SET TimeZone='UTC'")

    out: list[str] = []
    for i, (title, code, notes) in enumerate(stmts, 1):
        head = title or f"Проверка {i}"
        # в файле заголовки уже пронумерованы — второй раз не нумеруем
        label = head if head[:1].isdigit() else f"{i}. {head}"
        print("=" * 78)
        print(label)
        print("=" * 78)
        try:
            table = render(con.execute(code))
        except Exception as e:                               # noqa: BLE001
            table = f"!! ОШИБКА: {e}"
        print(table)
        for n in notes:
            print(f"    {n}")
        print()
        out.append(f"--- {label}")
        out.append(table)
        for n in notes:
            out.append(f"    {n}")
        out.append("")

    con.close()

    if args.out:
        p = Path(args.out)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("\n".join(out), encoding="utf-8")
        print(f"Вывод сохранён: {p}")


if __name__ == "__main__":
    main()