# З10 — Дашборд Metabase

**Задание:** [`ЗАДАНИЕ.md`](ЗАДАНИЕ.md) · **Разбор:** [`ОТЧЕТ_ПОЧЕМУ.md`](ОТЧЕТ_ПОЧЕМУ.md)
**Описание и SQL виджетов:** [`dashboards/README.md`](dashboards/README.md)
**Доказательства:** [`evidence/dashboard.md`](evidence/dashboard.md)

---

## Что получилось

Дашборд **«Olist: оборот и категории»** в Metabase поверх ClickHouse.
Точный адрес печатает `etl/setup_dashboard.py`; актуальный id — в
[`evidence/metabase_setup.log`](evidence/metabase_setup.log).

| № | Виджет | Тип | Таблица |
|---|--------|-----|---------|
| 1 | GMV_delivered за период (эталон) | число | `fact_sales` |
| 2 | Позиций за период | число | `fact_sales` |
| 3 | Оборот по дням | линия | `mv_gmv_day_category` |
| 4 | Топ-10 категорий за период | столбики | `mv_gmv_day_category` |

Фильтр **«Период с»** (`date/single`) привязан ко всем четырём виджетам.
При `2016-09-04` карточка показывает **13 221 498.11** — ровно эталон
[`canonical_metric.sql`](../04_З04_аналитический_sql/sql/canonical_metric.sql).

Скриншот: [`evidence/dashboard.png`](evidence/dashboard.png)

---

## Как запустить

```bash
# 1. Стенд: ClickHouse + Metabase с драйвером ClickHouse
cd 01_З01_стенд_и_сырьё
docker compose --profile bi up -d
bash ./scripts/init_ch.sh
cd ..

# 2. Данные и сводка
python 03_З03_витрина_duckdb/etl/build_duckdb.py
python 05_З05_elt_качество/etl/run_etl.py
python 06_З06_scd2_или_cut/etl/build_scd2.py
python 07_З07_загрузка_clickhouse/etl/load_clickhouse.py
python 09_З09_mv_витрина/etl/build_mv.py

# 3. Дашборд
#    первый раз: откройте http://localhost:3000 и создайте админа
#    (olap@example.com / Olist_2026! — или задайте MB_USER / MB_PASS)
python 10_З10_дашборд_metabase/etl/setup_dashboard.py
```

Скрипт идемпотентен: объекты с теми же именами удаляются и создаются заново,
поэтому повторный прогон даёт тот же дашборд (новые id — это нормально).

Учётка Metabase: `olap@example.com` / `Olist_2026!`
(переопределяется `MB_USER` / `MB_PASS`).

---

## Про драйвер ClickHouse

Metabase **не умеет** ClickHouse из коробки: в образе нет драйвера.

| Что сделано | Где |
|-------------|-----|
| Драйвер `clickhouse.metabase-driver.jar` 1.52.0 скачан под Metabase 0.52.x | [`01_З01_стенд_и_сырьё/metabase-plugins/`](../01_З01_стенд_и_сырьё/metabase-plugins/) |
| Примонтирован в контейнер | `./metabase-plugins:/plugins` + `MB_PLUGINS_DIR=/plugins` в [`docker-compose.yml`](../01_З01_стенд_и_сырьё/docker-compose.yml) |

Версия драйвера подобрана под версию Metabase (`1.52.x` ↔ `0.52.x`).
С другим драйвером движок `clickhouse` просто не появится в списке.

---

## Файлы

| Файл | Что это |
|------|---------|
| [`etl/setup_dashboard.py`](etl/setup_dashboard.py) | подключение к ClickHouse, 4 карточки, дашборд, фильтр — через REST API Metabase |
| [`dashboards/README.md`](dashboards/README.md) | что на экране, как устроен фильтр, ограничения |
| [`dashboards/01_gmv_delivered.sql`](dashboards/01_gmv_delivered.sql) | SQL карточки метрики (детальный факт) |
| [`dashboards/02_positions.sql`](dashboards/02_positions.sql) | SQL карточки «позиций» |
| [`dashboards/03_gmv_by_day.sql`](dashboards/03_gmv_by_day.sql) | SQL «оборот по дням» (сводка) |
| [`dashboards/04_top_categories.sql`](dashboards/04_top_categories.sql) | SQL «топ-10 категорий» (сводка) |
| [`evidence/dashboard.md`](evidence/dashboard.md) | подключение, числа под фильтром, скриншот |
| [`evidence/dashboard.png`](evidence/dashboard.png) | скриншот живого дашборда |
| [`evidence/metabase_setup.log`](evidence/metabase_setup.log) | лог сборки: id карточек и дашборда |

---

## Почему дашборд собирается скриптом, а не мышкой

Дашборд, собранный в UI, невозможно воспроизвести: его нет в репозитории,
и на чистом стенде всё придётся кликать заново. Здесь через REST API
Metabase делается всё то же самое, а результат лежит в git.

Побочный эффект: **SQL виджетов не может разойтись с живым дашбордом**.
Файлы `dashboards/*.sql` генерируются из той же спецификации, что и карточки.

---

## Чек-лист задания

- [x] **Данные с ClickHouse, не с `.duckdb`-файла —** все четыре виджета
      подключены к базе `Olist ClickHouse` (движок `clickhouse`,
      `clickhouse:8123`, база `olist`); подключения к DuckDB нет
- [x] **Фильтр работает** — «Период с» меняет число:
      `2016-09-04 → 13 221 498.11`, `2018-01-01 → 7 218 125.12`
      ([`evidence/dashboard.md` §3](evidence/dashboard.md))
- [x] **SQL виджетов сохранён в проекте** — [`dashboards/`](dashboards/),
      все четыре виджета native SQL
- [x] Виджетов больше трёх (4) — карточки и графики
- [x] Хотя бы один виджет с текстом SQL — таких все четыре, эталон метрики
      взят из `canonical_metric.sql`
