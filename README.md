# Учебный стенд OLAP

Гончар Александр Иванович, Бутько Пётр Сергеевич

Домен: e-commerce маркетплейс (Olist)

**Исходные данные:** все сырые файлы расположены в директории `data/raw/` — 9 CSV бразильского маркетплейса Olist.

**Стек:** ClickHouse + Metabase (Docker) · DuckDB — локально у студента.

Metabase читает только ClickHouse; DuckDB — локальная лаборатория для сборки и проверок.

## Быстрый старт

```bash
cd 
docker compose --profile bi up -d
bash ./scripts/init_ch.sh   # база olist + пользователь olist (обязательный шаг)
cd ..
```

Профиль `bi` обязателен: без него Metabase не запустится. Первый запуск скачивает образы ClickHouse и Metabase, это может занять несколько минут.

Эти команды поднимают только стенд.

## Порты

| Сервис | Порт |
|--------|------|
| ClickHouse HTTP | 8123 |
| ClickHouse native | 9000 |
| Metabase | 3000 |

- ClickHouse Play — http://localhost:8123/play
- Metabase — http://localhost:3000

Доступ к ClickHouse: пользователь `olist`, пароль `olist`, база `olist`.

## Что нужно

- **Docker Desktop**;
- **Python 3.10+**;
- **Git Bash или WSL** — для `init_ch.sh`;

Все команды выполняются из корня проекта, если не указано иное. В PowerShell перед запуском скриптов можно включить UTF-8:

```powershell
$env:PYTHONUTF8="1"
```

## Подъём

Шаги 2–4 можно выполнить вручную или заменить одной командой из шага 5 — `run_all.py` делает то же самое. Скачать CSV и установить зависимости, создать администратора Metabase всё равно нужно.

### 1. Стенд

Если ещё не запускали:

```bash
cd 01_З01_стенд_и_сырьё
docker compose --profile bi up -d
bash ./scripts/init_ch.sh
cd ..
```

Проверка:

```bash
curl http://localhost:8123/ping
curl http://localhost:3000/api/health
```

ClickHouse должен ответить `Ok.`, Metabase — HTTP 200. Metabase после первого старта может подниматься 1–2 минуты.

### 2. Данные

Установите зависимости:

```bash
python -m pip install -r requirements.txt
```

Проверка:

```bash
python scripts/smoke_duckdb.py
```

Ожидаемый последний вывод: `OK — DuckDB читает сырьё, стенд рабочий.`

Соберите витрину и загрузите её в ClickHouse:

```bash
python 03_З03_витрина_duckdb/etl/build_duckdb.py --checks
python 05_З05_elt_качество/etl/run_etl.py
python 06_З06_scd2_или_cut/etl/build_scd2.py
python 07_З07_загрузка_clickhouse/etl/load_clickhouse.py
python 09_З09_mv_витрина/etl/build_mv.py --rebuild-twice
```

### 3. Эталонный SQL метрики

Определение метрики — [`04_З04_аналитический_sql/sql/canonical_metric.sql`]:

```sql
SELECT SUM(amount) AS gmv_brl
FROM fact_sales
WHERE order_status = 'delivered';
```

Ожидаемое значение: **13 221 498.11 BRL**. Прогнать аналитические запросы:

```bash
python 04_З04_аналитический_sql/etl/run_analytics.py
```

### 4. Дашборд

Первый раз на чистом стенде:

1. Откройте http://localhost:3000.
2. Создайте администратора `olap@example.com` с паролем `Olist_2026!`.

Если Metabase уже был настроен раньше, используйте существующего администратора и задайте `MB_USER` / `MB_PASS`. Если нужны другие логин и пароль, задайте их перед запуском:

```powershell
$env:MB_USER="you@example.com"
$env:MB_PASS="your_password"
```

```bash
# Git Bash / WSL
export MB_USER=you@example.com
export MB_PASS=your_password
```

Соберите дашборд:

```bash
python 10_З10_дашборд_metabase/etl/setup_dashboard.py
```

Скрипт создаст подключение к ClickHouse, 4 виджета и фильтр «Период с». 

### 5. Если что-то красное

Сквозной прогон сам проверяет все 13 этапов и останавливается на первом упавшем:

```bash
python 12_З12_интеграция_контура/etl/run_all.py --out 12_З12_интеграция_контура/evidence/full_run.log
```

Ожидаемый итог:

```text
ИТОГ: все 13 шагов прошли, суммарно ... c.
```

Если Metabase пока не нужен:

```bash
python 12_З12_интеграция_контура/etl/run_all.py --no-bi
```


Частые ошибки:

| Симптом | Что проверить |
|---------|----------------|
| `ClickHouse не отвечает` | Docker Desktop запущен? `docker compose --profile bi up -d` выполнен? `curl http://localhost:8123/ping` |
| `Metabase не отвечает` | Запускали с профилем `bi`? Подождите 1–2 минуты, проверьте http://localhost:3000/api/health |
| `Не нашёл папку с CSV` | CSV лежат в `data/raw/` или `raw data/`? Или задайте `RAW_DIR` |
| `No module named duckdb` / `clickhouse_connect` | `python -m pip install -r requirements.txt`; проверьте, что запускаете тем же `python` |
| `Не удалось войти в Metabase` | Админ создан? Логин и пароль совпадают с `MB_USER` / `MB_PASS`? |
| `Port already allocated` | Порты 8123, 9000 или 3000 заняты — остановите другой сервис; если меняете порты, обновите настройки скриптов |

## Что где лежит

| Папка | Что внутри | Команда |
|-------|------------|---------|
| `01_З01_стенд_и_сырьё/` | Docker, `init_ch.sh`, описание CSV | см. «Быстрый старт» |
| `02_З02_паспорт_star/` | Паспорт модели, схема, `cut.md` | `python 02_З02_паспорт_star/etl/check_passport.py` |
| `03_З03_витрина_duckdb/` | Сборка витрины в DuckDB | `python 03_З03_витрина_duckdb/etl/build_duckdb.py --checks` |
| `04_З04_аналитический_sql/` | Аналитические запросы, эталон метрики | `python 04_З04_аналитический_sql/etl/run_analytics.py` |
| `05_З05_elt_качество/` | ELT и 19 проверок качества | `python 05_З05_elt_качество/etl/run_etl.py` |
| `06_З06_scd2_или_cut/` | SCD2 для товаров | `python 06_З06_scd2_или_cut/etl/build_scd2.py` |
| `07_З07_загрузка_clickhouse/` | Загрузка в ClickHouse и сверка | `python 07_З07_загрузка_clickhouse/etl/load_clickhouse.py`<br>`python 07_З07_загрузка_clickhouse/etl/reconcile.py` |
| `08_З08_запросы_и_ключи_ch/` | Запросы, партиции, `EXPLAIN` | `python 08_З08_запросы_и_ключи_ch/etl/run_queries.py` |
| `09_З09_mv_витрина/` | Сводка «день × категория» | `python 09_З09_mv_витрина/etl/build_mv.py --rebuild-twice` |
| `10_З10_дашборд_metabase/` | Дашборд Metabase через API | `python 10_З10_дашборд_metabase/etl/setup_dashboard.py` |
| `11_З11_сверка_sql_bi/` | Сверка SQL ↔ BI | `python 11_З11_сверка_sql_bi/etl/compare_sql_bi.py` |
| `12_З12_интеграция_контура/` | Сквозной прогон и чек-лист | `python 12_З12_интеграция_контура/etl/run_all.py` |

Если запускать вручную, порядок: `02` (проверка) → `03 → 04 → 05 → 06 → 07 → 08 → 09 → 10 → 11`. `run_all.py` делает это сам. Полный список шагов с аргументами: `python 12_З12_интеграция_контура/etl/run_all.py --list`.

## Остановка и сброс

```bash
cd 01_З01_стенд_и_сырьё
docker compose --profile bi down       # остановить, данные сохранить
docker compose --profile bi down -v    # удалить данные ClickHouse и Metabase
```

После `down -v` Metabase снова попросит создать администратора; данные ClickHouse тоже удалятся.

## Что должно получиться

- `run_all.py` завершается с `exit 0` и итогом **13/13**.
- В `fact_sales` — **112 650** строк, `GMV_delivered` = **13 221 498.11 BRL**.
- В Metabase — дашборд из 4 виджетов; сверка SQL ↔ BI — **0 расхождений**.

