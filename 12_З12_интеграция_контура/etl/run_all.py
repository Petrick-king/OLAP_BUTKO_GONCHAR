#!/usr/bin/env python3
"""Сквозной прогон контура З01–З11 одной командой (практика З12).

    python 12_З12_интеграция_контура/etl/run_all.py
    python 12_З12_интеграция_контура/etl/run_all.py --no-bi
    python 12_З12_интеграция_контура/etl/run_all.py --out 12_З12_интеграция_контура/evidence/full_run.log

ЧТО ДЕЛАЕТ. По очереди запускает скрипты всех этапов — от проверки паспорта
до сверки «SQL ↔ карточка» — и останавливается на первом же ненулевом коде.
Это и есть проверка «проект поднимается, а не только лежит».

ПОЧЕМУ НЕ BASH-ОДНОСТРОЧНИК. Нужны: единый лог с кодами возврата и
временем, предварительная проверка стенда (Docker/ClickHouse/Metabase)
и понятное сообщение «что именно упало». Плюс на Windows `&&` в PowerShell
и в cmd работают по-разному — Python одинаков везде.

ПРЕДУСЛОВИЯ
  • Docker Desktop запущен;
  • стенд поднят:  cd 01_З01_стенд_и_сырьё  →  docker compose --profile bi up -d
  • init выполнен:  bash ./scripts/init_ch.sh
  Скрипт сам проверяет ClickHouse и Metabase и подсказывает, чего не хватает.

ФЛАГИ
  --no-bi   пропустить шаги З10–З11 (не поднимать браузерную часть).
            Полезно, когда Metabase не запущен, а проверить остальное надо.
  --list    показать список шагов и выйти.
  --out     записать полный лог в файл.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

WORK = Path(__file__).resolve().parents[1]
PROJECT = WORK.parents[0]

PY = sys.executable

# (этап, что проверяет, скрипт, аргументы)
STEPS: list[tuple[str, str, str, list[str]]] = [
    ("З02", "паспорт заполнен по шаблону раздатки",
     "02_З02_паспорт_star/etl/check_passport.py", []),
    ("З03", "витрина в DuckDB: 8 проверок качества",
     "03_З03_витрина_duckdb/etl/build_duckdb.py", ["--checks"]),
    ("З04", "аналитические запросы и эталон метрики",
     "04_З04_аналитический_sql/etl/run_analytics.py", []),
    ("З05", "ELT: checks до загрузки, dim → fact",
     "05_З05_elt_качество/etl/run_etl.py", []),
    ("З06", "SCD2 для dim_product",
     "06_З06_scd2_или_cut/etl/build_scd2.py", []),
    ("З07", "загрузка витрины в ClickHouse (полный refresh)",
     "07_З07_загрузка_clickhouse/etl/load_clickhouse.py", []),
    ("З07", "сверка DuckDB ↔ ClickHouse по всем таблицам",
     "07_З07_загрузка_clickhouse/etl/reconcile.py", []),
    ("З08", "три запроса З04 выполняются в ClickHouse",
     "08_З08_запросы_и_ключи_ch/etl/run_queries.py", []),
    ("З08", "раскладка факта по партициям и планы запросов",
     "08_З08_запросы_и_ключи_ch/etl/run_queries.py", ["--file", "sql/partitions.sql"]),
    ("З08", "DuckDB и ClickHouse отвечают одинаково",
     "08_З08_запросы_и_ключи_ch/etl/compare_backends.py", []),
    ("З09", "сводка под график: сборка ×2 и сверка с фактом",
     "09_З09_mv_витрина/etl/build_mv.py", ["--rebuild-twice"]),
    ("З10", "дашборд Metabase: подключение, 4 виджета, фильтр",
     "10_З10_дашборд_metabase/etl/setup_dashboard.py", []),
    ("З11", "SQL и карточка дают одно число",
     "11_З11_сверка_sql_bi/etl/compare_sql_bi.py", []),
]

BI_STEPS = {"З10", "З11"}


def ping(url: str, timeout: int = 5) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return 200 <= r.status < 300
    except (urllib.error.URLError, OSError):
        return False


def preflight(need_bi: bool) -> list[str]:
    """Возвращает список проблем. Пустой список — можно запускать."""
    problems: list[str] = []
    if not ping("http://localhost:8123/ping"):
        problems.append(
            "ClickHouse не отвечает на http://localhost:8123/ping.\n"
            "    Запустите Docker Desktop и стенд:\n"
            "      cd 01_З01_стенд_и_сырьё\n"
            "      docker compose --profile bi up -d\n"
            "      bash ./scripts/init_ch.sh")
    if need_bi and not ping("http://localhost:3000/api/health", timeout=10):
        problems.append(
            "Metabase не отвечает на http://localhost:3000/api/health.\n"
            "    Он поднимается профилем `bi`. Либо запустите его, либо\n"
            "    выполните прогон без браузерной части: --no-bi")
    return problems


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-bi", action="store_true",
                    help="пропустить шаги З10–З11 (Metabase)")
    ap.add_argument("--list", action="store_true", help="показать шаги и выйти")
    ap.add_argument("--out", help="записать полный лог в файл")
    args = ap.parse_args()

    steps = [s for s in STEPS if not (args.no_bi and s[0] in BI_STEPS)]

    if args.list:
        for i, (lab, title, script, argv) in enumerate(steps, 1):
            print(f"{i:2}. [{lab}] {title}")
            print(f"    python {script} {' '.join(argv)}".rstrip())
        return

    need_bi = any(s[0] in BI_STEPS for s in steps)
    problems = preflight(need_bi)
    if problems:
        print("Стенд не готов:\n")
        for p in problems:
            print("  • " + p)
        sys.exit(1)

    log: list[str] = []
    started_all = time.perf_counter()
    failures = 0

    for i, (lab, title, script, argv) in enumerate(steps, 1):
        path = PROJECT / script
        if not path.exists():
            sys.exit(f"{lab}: нет файла {script}")

        print("=" * 78)
        print(f"{i:2}/{len(steps)}  {lab}  {title}")
        print("=" * 78)

        started = time.perf_counter()
        proc = subprocess.run(
            [PY, str(path), *argv],
            cwd=PROJECT, capture_output=True, text=True,
            encoding="utf-8", errors="replace",
            env={**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"},
        )
        elapsed = time.perf_counter() - started

        out = (proc.stdout or "").rstrip()
        err = (proc.stderr or "").rstrip()
        if out:
            # Хвост, а не весь вывод: у загрузки ClickHouse он длинный.
            tail = out.splitlines()[-12:]
            print("\n".join(tail))
        if err:
            print("--- stderr ---")
            print("\n".join(err.splitlines()[-20:]))

        status = "OK" if proc.returncode == 0 else f"FAIL (exit={proc.returncode})"
        print(f"\n-> {status}  за {elapsed:.1f} c")

        log += [f"===== {i}/{len(steps)} [{lab}] {title}",
                f"$ python {script} {' '.join(argv)}".rstrip(),
                f"exit={proc.returncode}  {elapsed:.2f} c",
                "--- stdout ---", out,
                "--- stderr ---", err, ""]

        if proc.returncode != 0:
            failures += 1
            print("\nОстановка: следующий шаг зависит от этого.")
            break

    total = time.perf_counter() - started_all
    print()
    print("=" * 78)
    if failures:
        verdict = f"ИТОГ: прогон остановлен на шаге {i} из {len(steps)}."
    else:
        verdict = f"ИТОГ: все {len(steps)} шагов прошли, суммарно {total:.1f} c."
    print(verdict)
    print("=" * 78)
    log += ["", verdict, ""]

    if args.out:
        p = Path(args.out)
        if not p.is_absolute():
            p = PROJECT / p
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("\n".join(log), encoding="utf-8")
        print(f"Лог сохранён: {p}")

    if failures:
        sys.exit(1)


if __name__ == "__main__":
    main()
