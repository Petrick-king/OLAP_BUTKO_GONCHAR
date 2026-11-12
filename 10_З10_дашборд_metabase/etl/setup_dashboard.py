#!/usr/bin/env python3
"""Сборка дашборда Metabase поверх ClickHouse (практика З10).

    python 10_З10_дашборд_metabase/etl/setup_dashboard.py
    python 10_З10_дашборд_metabase/etl/setup_dashboard.py --out evidence/metabase_setup.log

ЗАЧЕМ СКРИПТ, А НЕ «ПОКЛИКАЛ В UI». Дашборд, собранный мышкой, невозможно
воспроизвести: его нет в репозитории, и на чистом стенде его придётся
собирать заново. Здесь всё то же самое делается через REST API Metabase:
подключение к ClickHouse, четыре вопроса (карточки), дашборд и фильтр
«Период с». Скрипт идемпотентен: объекты с теми же именами удаляются
и создаются заново.

Важно: вопросы создаются как NATIVE SQL (тип `native`), а не кликами
в конструкторе. Это требование задания — «хотя бы один виджет с текстом
SQL»; здесь таких все четыре.

Настройки: MB_URL (по умолчанию http://localhost:3000),
MB_USER / MB_PASS — учётка администратора Metabase.

ПРЕДУСЛОВИЯ
  • стенд поднят:      docker compose --profile bi up -d
  • данные в ClickHouse: 07_З07_загрузка_clickhouse/etl/load_clickhouse.py
  • сводка З09:        09_З09_mv_витрина/etl/build_mv.py
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

WORK = Path(__file__).resolve().parents[1]
PROJECT = WORK.parents[0]

MB_URL = os.environ.get("MB_URL", "http://localhost:3000").rstrip("/")
MB_USER = os.environ.get("MB_USER", "olap@example.com")
MB_PASS = os.environ.get("MB_PASS", "Olist_2026!")

DB_NAME = "Olist ClickHouse"
DASH_NAME = "Olist: оборот и категории"

# Текст SQL для каждого виджета лежит и здесь, и в dashboards/*.sql —
# чтобы его можно было прочитать и сравнить, не поднимая Metabase.
CARDS = [
    {
        "name": "GMV_delivered за период (эталон)",
        "display": "scalar",
        "file": "01_gmv_delivered.sql",
        "sql": """-- Эталон главной метрики. Тот же текст, что в canonical_metric.sql,
-- плюс фильтр по периоду. Метрика считается по ДЕТАЛЬНОМУ факту,
-- а не по сводке: правда метрики живёт здесь.
SELECT sum(amount) AS gmv_brl
FROM olist.fact_sales
WHERE order_status = 'delivered'
  AND order_date_key >= toDate({{start_date}})""",
        "viz": {"scalar.field": "gmv_brl"},
        "size": (12, 3),
    },
    {
        "name": "Позиций за период",
        "display": "scalar",
        "file": "02_positions.sql",
        "sql": """-- Сколько строк факта попало в период. Это «знаменатель»:
-- gmv_brl / positions = средняя стоимость позиции.
SELECT count() AS positions
FROM olist.fact_sales
WHERE order_status = 'delivered'
  AND order_date_key >= toDate({{start_date}})""",
        "viz": {"scalar.field": "positions"},
        "size": (12, 3),
    },
    {
        "name": "Оборот по дням",
        "display": "line",
        "file": "03_gmv_by_day.sql",
        "sql": """-- Читает СВОДКУ З09, а не факт: одна строка сводки уже содержит
-- готовую сумму за день × категорию, поэтому день складывается из
-- 74 строк вместо тысяч строк факта.
SELECT order_date_key AS day,
       sum(gmv_brl)   AS gmv_brl
FROM olist.mv_gmv_day_category
WHERE order_date_key >= toDate({{start_date}})
GROUP BY order_date_key
ORDER BY order_date_key""",
        "viz": {"graph.dimensions": ["day"], "graph.metrics": ["gmv_brl"]},
        "size": (24, 7),
    },
    {
        "name": "Топ-10 категорий за период",
        "display": "bar",
        "file": "04_top_categories.sql",
        "sql": """-- Тот же разрез «день × категория», свёрнутый по категориям.
-- Сводка позволяет ответить на вопрос «топ категорий» без чтения
-- 112 650 строк факта.
SELECT category_name_pt AS category,
       sum(gmv_brl)     AS gmv_brl
FROM olist.mv_gmv_day_category
WHERE order_date_key >= toDate({{start_date}})
GROUP BY category_name_pt
ORDER BY gmv_brl DESC
LIMIT 10""",
        "viz": {"graph.dimensions": ["category"], "graph.metrics": ["gmv_brl"]},
        "size": (24, 7),
    },
]

TAG_ID = "b3a1c4d2-0000-4000-8000-000000000001"
FILTER_ID = "f1e2d3c4-0000-4000-8000-000000000001"


class Metabase:
    def __init__(self, base: str, session: str) -> None:
        self.base = base
        self.session = session
        self.log: list[str] = []

    def call(self, method: str, path: str, payload: object | None = None) -> tuple[int, object]:
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        req = urllib.request.Request(self.base + path, data=data, method=method)
        req.add_header("Content-Type", "application/json")
        req.add_header("X-Metabase-Session", self.session)
        try:
            with urllib.request.urlopen(req, timeout=300) as r:
                body = r.read().decode("utf-8")
                out = json.loads(body) if body.strip() else None
                return r.status, out
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8")
            try:
                out = json.loads(body)
            except Exception:                                  # noqa: BLE001
                out = body
            return e.code, out


def login() -> str:
    req = urllib.request.Request(
        MB_URL + "/api/session",
        data=json.dumps({"username": MB_USER, "password": MB_PASS}).encode(),
        method="POST")
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read().decode())["id"]
    except urllib.error.HTTPError as e:
        sys.exit(f"Не удалось войти в Metabase ({MB_URL}): {e.code} {e.read().decode()[:200]}")


def ensure_database(mb: Metabase) -> int:
    st, body = mb.call("GET", "/api/database")
    for db in body.get("data", []):
        if db["name"] == DB_NAME:
            mb.log.append(f"БД уже есть: id={db['id']} ({DB_NAME})")
            return db["id"]

    payload = {
        "name": DB_NAME,
        "engine": "clickhouse",
        "details": {
            "host": os.environ.get("MB_CH_HOST", "clickhouse"),
            "port": int(os.environ.get("MB_CH_PORT", 8123)),
            "user": os.environ.get("CH_USER", "olist"),
            "password": os.environ.get("CH_PASS", "olist"),
            "dbname": os.environ.get("CH_DB", "olist"),
            "scan-all-databases": False,
            "ssl": False,
        },
        "is_full_sync": True,
        "is_on_demand": False,
        "auto_run_queries": True,
    }
    st, body = mb.call("POST", "/api/database", payload)
    if st != 200:
        sys.exit(f"БД не создалась: {st} {json.dumps(body, ensure_ascii=False)[:500]}")
    mb.log.append(f"БД создана: id={body['id']} ({DB_NAME})")
    return body["id"]


def delete_by_name(mb: Metabase, kind: str, name: str) -> None:
    """Удаляет объекты с тем же именем — чтобы прогон был воспроизводимым."""
    if kind == "card":
        st, body = mb.call("GET", "/api/card")
        items = body if isinstance(body, list) else body.get("data", [])
        for c in items:
            if c.get("name") == name:
                mb.call("DELETE", f"/api/card/{c['id']}")
                mb.log.append(f"  удалена старая карточка id={c['id']}: {name}")
    elif kind == "dashboard":
        st, body = mb.call("GET", "/api/dashboard")
        items = body if isinstance(body, list) else body.get("data", [])
        for d in items:
            if d.get("name") == name and not d.get("archived"):
                mb.call("DELETE", f"/api/dashboard/{d['id']}")
                mb.log.append(f"  удалён старый дашборд id={d['id']}: {name}")


def create_card(mb: Metabase, db_id: int, spec: dict) -> int:
    payload = {
        "name": spec["name"],
        "display": spec["display"],
        "dataset_query": {
            "type": "native",
            "database": db_id,
            "native": {
                "query": spec["sql"],
                "template-tags": {
                    "start_date": {
                        "id": TAG_ID,
                        "name": "start_date",
                        "display-name": "Период с",
                        "type": "date",
                        "default": "2017-01-01",
                    }
                },
            },
        },
        "visualization_settings": spec["viz"],
        "description": spec["sql"].strip().splitlines()[0].lstrip("- ").strip(),
    }
    st, body = mb.call("POST", "/api/card", payload)
    if st != 200:
        sys.exit(f"Карточка «{spec['name']}» не создалась: {st} "
                 f"{json.dumps(body, ensure_ascii=False)[:500]}")
    card_id = body["id"]
    mb.log.append(f"  карточка id={card_id}: {spec['name']} ({spec['display']})")

    # Сразу проверяем, что запрос выполняется и возвращает строки.
    st, res = mb.call("POST", f"/api/card/{card_id}/query")
    rows = ((res or {}).get("data") or {}).get("rows") if isinstance(res, dict) else None
    if st != 202 or not rows:
        sys.exit(f"Запрос карточки «{spec['name']}» не отработал: {st} "
                 f"{json.dumps(res, ensure_ascii=False)[:500]}")
    mb.log.append(f"    запрос вернул {len(rows)} строк(и), первая: {rows[0]}")
    return card_id


def build_dashboard(mb: Metabase, card_ids: list[int]) -> int:
    st, body = mb.call("POST", "/api/dashboard", {
        "name": DASH_NAME,
        "description": ("Оборот доставленных заказов Olist: эталон метрики "
                        "по детальному факту + динамика по дням и топ категорий "
                        "из сводки З09. Источник — ClickHouse."),
    })
    if st != 200:
        sys.exit(f"Дашборд не создался: {st} {json.dumps(body, ensure_ascii=False)[:500]}")
    dash_id = body["id"]
    mb.log.append(f"  дашборд id={dash_id}: {DASH_NAME}")

    # Раскладка 12-колоночной сетки: два скаляра сверху, два графика снизу.
    layout = [(0, 0), (0, 12), (3, 0), (10, 0)]
    dashcards = []
    for i, (card_id, (row, col)) in enumerate(zip(card_ids, layout)):
        spec = CARDS[i]
        dashcards.append({
            "id": -(i + 1),
            "card_id": card_id,
            "row": row,
            "col": col,
            "size_x": spec["size"][0],
            "size_y": spec["size"][1],
            "parameter_mappings": [{
                "parameter_id": FILTER_ID,
                "card_id": card_id,
                "target": ["variable", ["template-tag", "start_date"]],
            }],
            "visualization_settings": {},
        })

    payload = {
        "name": DASH_NAME,
        "description": body.get("description"),
        "parameters": [{
            "id": FILTER_ID,
            "name": "Период с",
            "slug": "period_start",
            "type": "date/single",
            "sectionId": "date",
            "default": "2017-01-01",
        }],
        "dashcards": dashcards,
    }
    st, body = mb.call("PUT", f"/api/dashboard/{dash_id}", payload)
    if st != 200:
        sys.exit(f"Дашборд не обновился: {st} {json.dumps(body, ensure_ascii=False)[:800]}")
    mb.log.append("  карточки и фильтр привязаны")
    return dash_id


def write_sql_files() -> None:
    """Кладёт SQL каждого виджета в dashboards/ — «текст важнее скриншота».

    Файлы перезаписываются при каждом прогоне setup_dashboard.py, поэтому
    репозиторий и живой дашборд не могут разойтись: источник один.
    """
    out_dir = WORK / "dashboards"
    out_dir.mkdir(parents=True, exist_ok=True)
    for i, spec in enumerate(CARDS, 1):
        path = out_dir / spec["file"]
        header = (
            f"-- =====================================================================\n"
            f"--  {spec['file']}\n"
            f"--  Виджет дашборда З10: «{spec['name']}»\n"
            f"--  Тип отображения: {spec['display']}\n"
            f"--\n"
            f"--  Файл сгенерирован etl/setup_dashboard.py из спецификации\n"
            f"--  виджета — править его руками бессмысленно, правьте спецификацию.\n"
            f"--\n"
            f"--  Параметр {{start_date}} — фильтр дашборда «Период с»\n"
            f"--  (тип date/single). В SQL подставляется как дата-литерал,\n"
            f"--  поэтому обёрнут в toDate(...).\n"
            f"-- =====================================================================\n\n"
        )
        path.write_text(header + spec["sql"] + "\n", encoding="utf-8")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out",
                    default=str(WORK / "evidence" / "metabase_setup.log"),
                    help="куда писать протокол (по умолчанию evidence/metabase_setup.log)")
    args = ap.parse_args()

    mb = Metabase(MB_URL, login())
    mb.log.append(f"Metabase: {MB_URL}, вход {MB_USER}")

    db_id = ensure_database(mb)
    for spec in CARDS:
        delete_by_name(mb, "card", spec["name"])
    delete_by_name(mb, "dashboard", DASH_NAME)

    card_ids = [create_card(mb, db_id, spec) for spec in CARDS]
    dash_id = build_dashboard(mb, card_ids)
    write_sql_files()
    mb.log.append(f"  SQL виджетов выгружен в {WORK / 'dashboards'}")

    print("\n".join(mb.log))
    print()
    print(f"Дашборд: {MB_URL}/dashboard/{dash_id}")
    print(f"Вопросы: {MB_URL}/question/" + f", {MB_URL}/question/".join(map(str, card_ids)))

    if args.out:
        p = Path(args.out)
        if not p.is_absolute():
            p = PROJECT / p
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("\n".join(mb.log) + f"\n\ndash_id={dash_id}\n"
                     f"card_ids={card_ids}\n", encoding="utf-8")
        print(f"Протокол сохранён: {p}")


if __name__ == "__main__":
    main()

