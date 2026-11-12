#!/usr/bin/env python3
"""Сверка «эталонный SQL ↔ карточка на дашборде» (практика З11).

    python 11_З11_сверка_sql_bi/etl/compare_sql_bi.py
    python 11_З11_сверка_sql_bi/etl/compare_sql_bi.py --out evidence/compare.md

ЧТО СРАВНИВАЕТСЯ
  A. Число из эталонного SQL.  Берётся файл
     04_З04_аналитический_sql/sql/canonical_metric.sql — целиком, без
     переписывания, — в него лишь добавляется условие по периоду.
  B. Число с карточки Metabase. Запрос выполняется через API по тому же
     пути, что и кнопка «Обновить» на дашборде:
       POST /api/card/{id}/query
       POST /api/dashboard/{d}/dashcard/{dc}/card/{id}/query
  C. Текст формулы. SQL карточки, вытащенный из Metabase, сравнивается
     с файлом 10_З10_дашборд_metabase/dashboards/01_gmv_delivered.sql.
     Требование задания: «формула метрики живёт в SQL-файле, а не только
     в подписи виджета».

УСЛОВИЯ СВЕРКИ (одинаковые для A и B)
  один и тот же фильтр «Период с» и одна и та же база — ClickHouse.
  Проверяются три значения, чтобы совпадение не было случайным на одной
  точке: начало данных, 2017 год, 2018 год.

ПОЧЕМУ НЕ «ПОДОГНАТЬ КАРТОЧКУ». FAQ дисциплины: «SQL и виджет разошлись?
UI-фильтр, другая таблица, Float. Чините канон». Поэтому при расхождении
скрипт падает и печатает, ЧТО именно разошлось, — а не правит карточку.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request
from datetime import date
from pathlib import Path

try:
    import clickhouse_connect
except ModuleNotFoundError:
    sys.exit("Нужен clickhouse-connect:  pip install clickhouse-connect")

WORK = Path(__file__).resolve().parents[1]
PROJECT = WORK.parents[0]

CANONICAL = PROJECT / "04_З04_аналитический_sql" / "sql" / "canonical_metric.sql"
# Текст карточки берём не копией, а из папки З10: копия рано или поздно
# разойдётся с оригиналом, и сверка станет бессмысленной.
CARD_SQL = (PROJECT / "10_З10_дашборд_metabase" / "dashboards"
            / "01_gmv_delivered.sql")

CARD_NAME = "GMV_delivered за период (эталон)"
DASH_NAME = "Olist: оборот и категории"

MB_URL = os.environ.get("MB_URL", "http://localhost:3000").rstrip("/")
MB_USER = os.environ.get("MB_USER", "olap@example.com")
MB_PASS = os.environ.get("MB_PASS", "Olist_2026!")

# Три точки: начало данных, полный 2017-й, полный 2018-й.
CHECKS = [
    ("весь период (с 2016-09-04)", "2016-09-04"),
    ("с 2017-01-01", "2017-01-01"),
    ("с 2018-01-01", "2018-01-01"),
]


# ── работа с текстом SQL ────────────────────────────────────────────────────

def apply_period(sql: str, start: str | None) -> str:
    """Добавляет условие по периоду в эталонный SQL, не переписывая его.

    В canonical_metric.sql уже есть `WHERE order_status = 'delivered'`.
    Мы дописываем к этому условию `AND order_date_key >= ...`. Ничего
    другого в файле не меняется — поэтому и число остаётся эталонным.
    """
    body = sql.strip().rstrip(";").strip()
    if start is None:
        return body
    needle = "WHERE order_status = 'delivered'"
    if needle not in body:
        sys.exit("canonical_metric.sql изменился: не найден ожидаемый WHERE")
    return body.replace(needle, f"{needle}\n  AND order_date_key >= toDate('{start}')")


def normalize(sql: str) -> str:
    """Убирает комментарии, пустые строки и лишние пробелы — для сравнения."""
    lines = [ln for ln in sql.splitlines()
             if ln.strip() and not ln.strip().startswith("--")]
    text = "\n".join(lines)
    return re.sub(r"\s+", " ", text).strip()


# ── Metabase ───────────────────────────────────────────────────────────────

class Metabase:
    def __init__(self) -> None:
        self.base = MB_URL
        self.session = self._login()

    def _login(self) -> str:
        req = urllib.request.Request(
            self.base + "/api/session",
            data=json.dumps({"username": MB_USER, "password": MB_PASS}).encode(),
            method="POST")
        req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.loads(r.read().decode())["id"]
        except urllib.error.HTTPError as e:
            sys.exit(f"Metabase недоступен ({self.base}): {e.code}")

    def call(self, method: str, path: str, payload: object | None = None) -> object:
        data = json.dumps(payload).encode() if payload is not None else None
        req = urllib.request.Request(self.base + path, data=data, method=method)
        req.add_header("Content-Type", "application/json")
        req.add_header("X-Metabase-Session", self.session)
        with urllib.request.urlopen(req, timeout=300) as r:
            body = r.read().decode()
            return json.loads(body) if body.strip() else None

    def find_card(self, name: str) -> dict:
        for card in self.call("GET", "/api/card"):
            if card.get("name") == name:
                return card
        sys.exit(f"В Metabase нет карточки «{name}». Сначала "
                 f"python 10_З10_дашборд_metabase/etl/setup_dashboard.py")

    def find_dashcard(self, dashboard_name: str, card_id: int) -> tuple[int, int, str]:
        for dash in self.call("GET", "/api/dashboard"):
            if dash.get("name") != dashboard_name:
                continue
            full = self.call("GET", f"/api/dashboard/{dash['id']}")
            for dc in full.get("dashcards", []):
                if dc.get("card_id") == card_id:
                    params = full.get("parameters", [])
                    return dash["id"], dc["id"], params[0]["id"] if params else ""
        sys.exit(f"Карточка {card_id} не найдена на дашборде «{dashboard_name}»")


def card_value(mb: Metabase, card_id: int, tag_id: str, start: str) -> float:
    payload = {"parameters": [{
        "id": tag_id, "type": "date/single", "value": start,
        "target": ["variable", ["template-tag", "start_date"]],
    }]}
    res = mb.call("POST", f"/api/card/{card_id}/query", payload)
    return float(res["data"]["rows"][0][0])


def dashboard_value(mb: Metabase, dash_id: int, dashcard_id: int,
                    card_id: int, filter_id: str, start: str) -> float:
    payload = {"parameters": [{"id": filter_id, "type": "date/single", "value": start}]}
    res = mb.call("POST", f"/api/dashboard/{dash_id}/dashcard/{dashcard_id}/card/"
                          f"{card_id}/query", payload)
    return float(res["data"]["rows"][0][0])


def canonical_value(client, sql: str) -> float:
    """Берёт из результата колонку `metric_value`, а не «первую попавшуюся».

    canonical_metric.sql возвращает несколько колонок (имя метрики,
    определение, значение, счётчики). Первая — текст `'GMV_delivered'`,
    поэтому `result_rows[0][0]` дал бы строку, а не число.
    """
    result = client.query(sql)
    names = list(result.column_names)
    if "metric_value" not in names:
        sys.exit(f"В результате нет колонки metric_value: {names}")
    return float(result.result_rows[0][names.index("metric_value")])


def summary_value(client, start: str) -> float:
    """То же число, но посчитанное по сводке З09, а не по детальному факту.

    Это проверка типовой причины расхождения «виджет смотрит в другую
    таблицу»: сводка обязана давать ту же сумму, что и факт.
    """
    sql = ("SELECT sum(gmv_brl) FROM olist.mv_gmv_day_category "
           f"WHERE order_date_key >= toDate('{start}')")
    return float(client.query(sql).result_rows[0][0])


# ── основное ───────────────────────────────────────────────────────────────

def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out",
                    default=str(WORK / "evidence" / "compare.md"),
                    help="куда писать протокол (по умолчанию evidence/compare.md)")
    args = ap.parse_args()

    canonical_text = CANONICAL.read_text(encoding="utf-8")
    card_file_text = CARD_SQL.read_text(encoding="utf-8")

    ch = clickhouse_connect.get_client(
        host=os.environ.get("CH_HOST", "localhost"),
        port=int(os.environ.get("CH_PORT", 8123)),
        username=os.environ.get("CH_USER", "olist"),
        password=os.environ.get("CH_PASS", "olist"),
        database=os.environ.get("CH_DB", "olist"))

    mb = Metabase()
    card = mb.find_card(CARD_NAME)
    card_id = card["id"]
    tag_id = card["dataset_query"]["native"]["template-tags"]["start_date"]["id"]
    dash_id, dashcard_id, filter_id = mb.find_dashcard(DASH_NAME, card_id)

    print(f"Карточка: id={card_id} «{CARD_NAME}»")
    print(f"Дашборд:  id={dash_id}, dashcard={dashcard_id}, фильтр={filter_id}")
    print()

    rows: list[tuple[str, str, str, str, str]] = []
    failures = 0

    for label, start in CHECKS:
        sql_a = apply_period(canonical_text, start)
        value_sql = canonical_value(ch, sql_a)
        value_summary = summary_value(ch, start)
        value_card = card_value(mb, card_id, tag_id, start)
        value_dash = dashboard_value(mb, dash_id, dashcard_id, card_id, filter_id, start)

        ok_sql_card = abs(value_sql - value_card) < 0.005
        ok_card_dash = abs(value_card - value_dash) < 0.005
        ok_summary = abs(value_sql - value_summary) < 0.005
        if not (ok_sql_card and ok_card_dash and ok_summary):
            failures += 1

        rows.append((
            label,
            f"{value_sql:,.2f}",
            f"{value_summary:,.2f}",
            f"{value_card:,.2f}",
            f"{value_dash:,.2f}",
            "OK" if ok_sql_card and ok_card_dash and ok_summary else "РАСХОЖДЕНИЕ",
        ))
        print(f"  {label:28} SQL={value_sql:>14,.2f}  сводка={value_summary:>14,.2f}  "
              f"карточка={value_card:>14,.2f}  дашборд={value_dash:>14,.2f}  "
              f"{'OK' if ok_sql_card and ok_card_dash and ok_summary else 'FAIL'}")

    # Проверка, что формула живёт в файле, а не только в подписи виджета.
    card_sql = card["dataset_query"]["native"]["query"]
    formula_match = normalize(card_sql) == normalize(card_file_text)
    if not formula_match:
        failures += 1

    print()
    print(f"Формула карточки совпадает с «{CARD_SQL.relative_to(PROJECT)}»: "
          f"{'да' if formula_match else 'НЕТ'}")

    header = ["условия (Период с)", "число из SQL", "число из сводки",
              "число с карточки", "число с дашборда", "итог"]
    md = ["# Протокол сверки SQL ↔ BI (З11)", "",
          f"Дата прогона: {date.today().isoformat()}", "",
          "**Условия:** база ClickHouse (`olist`), метрика `GMV_delivered`, "
          "фильтр «Период с» одинаковый для SQL и карточки.", "",
          f"**Объекты Metabase:** карточка `{card_id}` "
          f"(«{CARD_NAME}»), дашборд `{dash_id}`, "
          f"dashcard `{dashcard_id}`, фильтр `{filter_id}`. "
          "Идентификаторы меняются при пересборке дашборда — актуальные "
          "всегда здесь и в `10_З10_дашборд_metabase/evidence/metabase_setup.log`.", "",
          "| " + " | ".join(header) + " |",
          "|" + "|".join("---" for _ in header) + "|"]
    for row in rows:
        md.append("| " + " | ".join(row) + " |")
    md += ["",
           f"**Формула:** SQL карточки совпадает с "
           f"`10_З10_дашборд_metabase/dashboards/01_gmv_delivered.sql` — "
           f"{'да' if formula_match else '**нет**'}.", ""]

    if failures:
        verdict = f"ИТОГ: расхождений — {failures}. Чинить канон, не карточку."
    else:
        verdict = ("ИТОГ: числа совпали во всех точках; формула метрики "
                   "живёт в SQL-файле.")
    print(verdict)
    md += [verdict, ""]

    ch.close()

    if args.out:
        p = Path(args.out)
        if not p.is_absolute():
            p = PROJECT / p
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("\n".join(md), encoding="utf-8")
        print(f"Протокол сохранён: {p}")

    if failures:
        sys.exit(1)


if __name__ == "__main__":
    main()
