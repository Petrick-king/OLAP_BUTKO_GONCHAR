# З08 — Запросы и ключи

**Задание:** [`ЗАДАНИЕ.md`](ЗАДАНИЕ.md) · **Разбор:** [`ОТЧЕТ_ПОЧЕМУ.md`](ОТЧЕТ_ПОЧЕМУ.md)
**Заметка про ключи:** [`notes/keys.md`](notes/keys.md)
**Результаты:** [`evidence/queries.md`](evidence/queries.md)

---

## Что получилось

Три запроса из З04 выполнены в ClickHouse. Эталон главной метрики
перенесён **без единой правки** и даёт то же число, что DuckDB:

| Запрос | DuckDB | ClickHouse | Совпало |
|--------|-------:|-----------:|:-------:|
| GMV_delivered | 13 221 498.11 | 13 221 498.11 | ✅ |
| Топ-5 категорий 2018 | 5 строк | 5 строк | ✅ |
| Помесячная динамика 2017 | 12 строк | 12 строк | ✅ |

Партиции **есть** — `PARTITION BY toYYYYMM(order_date_key)`, 24 куска.
Решение и обоснование — в [`notes/keys.md`](notes/keys.md).

---

## Как запустить

Стенд уже поднят (З01), данные в ClickHouse залиты (З07).

```bash
# три запроса: эталон + топ-5 категорий + помесячная динамика
python 08_З08_запросы_и_ключи_ch/etl/run_queries.py

# раскладка по партициям и план запроса
python 08_З08_запросы_и_ключи_ch/etl/run_queries.py --file sql/partitions.sql

# сверка DuckDB ↔ ClickHouse по строкам
python 08_З08_запросы_и_ключи_ch/etl/compare_backends.py
```

Сохранение в evidence:

```bash
python 08_З08_запросы_и_ключи_ch/etl/run_queries.py --out 08_З08_запросы_и_ключи_ch/evidence/queries_raw.txt
```

Файл `sql/queries_clickhouse.sql` — обычный SQL. Его можно выполнить целиком
и без Python: `clickhouse-client --multiquery < sql/queries_clickhouse.sql`.

---

## Файлы

| Файл | Что это |
|------|---------|
| [`sql/queries_clickhouse.sql`](sql/queries_clickhouse.sql) | три запроса З04, перенесённые в ClickHouse |
| [`sql/partitions.sql`](sql/partitions.sql) | ключи таблиц, раскладка по партициям, `EXPLAIN indexes = 1` |
| [`etl/run_queries.py`](etl/run_queries.py) | выполняет размеченный SQL и печатает каждый результат таблицей |
| [`etl/compare_backends.py`](etl/compare_backends.py) | прогоняет те же вопросы в DuckDB и ClickHouse и сравнивает по строкам |
| [`notes/keys.md`](notes/keys.md) | заметка: чем `ORDER BY` и партиция помогают типичным фильтрам |
| [`evidence/queries.md`](evidence/queries.md) | результаты с разбором |
| [`evidence/queries_raw.txt`](evidence/queries_raw.txt) | полный вывод консоли |
| [`evidence/partitions_raw.txt`](evidence/partitions_raw.txt) | раскладка по партициям и планы запросов |
| [`evidence/compare_raw.txt`](evidence/compare_raw.txt) | протокол сверки двух движков |

---

## Что пришлось поправить при переносе

| В З04 (DuckDB) | В ClickHouse | Почему |
|----------------|--------------|--------|
| `LAG(x) OVER (...)` | `lagInFrame(toNullable(x), 1, NULL) OVER (...)` | функции `lag` в ClickHouse нет, а `lagInFrame` по умолчанию отдаёт `0`, не `NULL` |
| `Decimal / Decimal` | `toFloat64(a) / toFloat64(b)` | ClickHouse делит `Decimal` на `Decimal` → доля обрезается до 2 знаков |
| `dim_date.year_month` | `formatDateTime(toStartOfMonth(order_date_key), '%Y-%m')` | джойн на 774 строки ради `'YYYY-MM'` не нужен |
| — | — | **эталон метрики не изменился вообще** |

---

## `ORDER BY` и партиция — коротко

```sql
ENGINE = MergeTree
PARTITION BY toYYYYMM(order_date_key)
ORDER BY (order_date_key, order_status, seller_id, category_name_pt,
          customer_id, product_id, order_id, order_item_id);
```

- `PARTITION BY` — на какие **папки на диске** разрезана таблица (24 месяца).
- `ORDER BY` — **порядок строк внутри папки** + разреженный первичный индекс.
- **Это не уникальный ключ.** Дубли не запрещены и не схлопываются.

На фильтре `order_date_key = '2018-01-15'` и `order_status = 'delivered'`
ClickHouse читает **1 гранулу из 29** — остальное отсекают `MinMax` по дате,
`Partition` по месяцу и `PrimaryKey` по `(order_date_key, order_status)`.
Без фильтра по дате партиции не помогают вовсе — читаются все 24 месяца.

Подробно, с цифрами из плана, — в [`notes/keys.md`](notes/keys.md).

---

## Чек-лист задания

- [x] Эталон метрики выполняется в ClickHouse — §1, 13 221 498.11 BRL
- [x] Ещё два аналитических запроса из З04 — §2 и §3, оба с окнами
- [x] Партиция **есть**: `PARTITION BY toYYYYMM(order_date_key)`, 24 куска
- [x] Заметка про ключ сортировки и партицию — [`notes/keys.md`](notes/keys.md)
- [x] Нигде не сказано, что `PRIMARY KEY` в ClickHouse = уникальность как в OLTP
      (наоборот, это явно опровергнуто в заметке и в DDL З07)
- [x] Результаты сверены с DuckDB по строкам — [`evidence/compare_raw.txt`](evidence/compare_raw.txt)
