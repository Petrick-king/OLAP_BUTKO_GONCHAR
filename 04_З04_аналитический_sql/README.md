# З04 — Аналитический SQL

**Задание:** [`ЗАДАНИЕ.md`](ЗАДАНИЕ.md) · **Разбор:** [`ОТЧЕТ_ПОЧЕМУ.md`](ОТЧЕТ_ПОЧЕМУ.md)
**Результаты:** [`evidence/analytics_results.md`](evidence/analytics_results.md) ·
**Эталон метрики:** [`evidence/canonical_metric.md`](evidence/canonical_metric.md)

---

## Что получилось

Три вопроса из паспорта закрыты запросами, в двух из них — оконные функции.
Главная метрика зафиксирована отдельным файлом и **одним числом**:

```
GMV_delivered = 13 221 498.11 BRL   (110 197 позиций, 96 478 заказов)
```

| § | Вопрос | Тип SQL | Оконная функция |
|---|--------|---------|-----------------|
| 1 | Оборот по регионам **продавца** в 2017 | `WHERE` + `GROUP BY` | — |
| 2 | Топ-5 категорий и их **доля** оборота 2018 | `GROUP BY` + окно | `SUM() OVER ()`, `RANK()`, кадровая рамка `ROWS` |
| 3 | Помесячная динамика оборота 2017 | `GROUP BY` + окно | `LAG() OVER (ORDER BY …)` |
| 4 | Своевременность доставки по регионам **покупателя** | `GROUP BY` + окно | `SUM() OVER ()` |

---

## Как запустить

```bash
python 04_З04_аналитический_sql/etl/run_analytics.py
```

Скрипт выполняет блоки из `sql/canonical_metric.sql` и `sql/analytics.sql`
и печатает их в табличном виде. База должна быть собрана (З03):

```bash
python 03_З03_витрина_duckdb/etl/build_duckdb.py
```

---

## Файлы

| Файл | Что это |
|------|---------|
| [`sql/analytics.sql`](sql/analytics.sql) | 4 аналитических запроса, перед каждым — сам вопрос |
| [`sql/canonical_metric.sql`](sql/canonical_metric.sql) | **эталон** главной метрики |
| [`etl/run_analytics.py`](etl/run_analytics.py) | исполнитель: режет SQL по разделителям и печатает результат |
| [`evidence/analytics_results.md`](evidence/analytics_results.md) | результаты с выводами |
| [`evidence/canonical_metric.md`](evidence/canonical_metric.md) | зафиксированное число метрики |
| [`evidence/analytics_raw.txt`](evidence/analytics_raw.txt) | полный лог консоли |

---

## Эталон главной метрики

Определение целиком живёт в [`sql/canonical_metric.sql`](sql/canonical_metric.sql)
и **дословно продублировано** в паспорте ([`passport.md` §7](../../02_З02_паспорт_star/passport.md)).

```sql
SELECT
    'GMV_delivered'           AS metric_name,
    SUM(amount)               AS metric_value,
    COUNT(*)                  AS positions_count,
    COUNT(DISTINCT order_id)  AS orders_count,
    SUM(qty)                  AS units_count
FROM fact_sales
WHERE order_status = 'delivered';
```

> Мера называется `amount`, а не `price`, — по образцу звезды из раздатки
> ([`star_retail.md`](../раздаточные%20материалы/star_retail.md)):
> `amount` — аддитивная сумма позиции, `qty` — число штук.
> В сырье это поле называется `price`; в витрине оно переименовано, потому
> что здесь это именно **сумма позиции**, а не цена единицы товара.

Правило, которое дальше держит весь семестр:

> Это число обязано совпасть в ClickHouse (З07) и на карточке дашборда (З11).
> Если изменилось — правятся **все четыре** места синхронно: этот файл,
> паспорт, карточка дашборда и подпись на графике. Формула метрики
> не должна жить только в голове или в подписи будущего графика.

---

## Чек-лист задания

- [x] На каждый из трёх вопросов есть запрос (`analytics.sql` §1–§3, плюс §4 сверх)
- [x] Есть оконные функции — в §2, §3 и §4
- [x] Эталон метрики гоняется одним скриптом и совпадает с паспортом
- [x] `SELECT * FROM fact` нигде нет — везде явные срезы и меры