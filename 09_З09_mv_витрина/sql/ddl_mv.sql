-- =====================================================================
--  ddl_mv.sql  —  сводная таблица под график З10
--  Практика З09 «Сводная таблица под график»
--
--  ГРАФИК, ПОД КОТОРЫЙ СТРОИТСЯ СВОДКА
--  ──────────────────────────────────
--  «Оборот доставленных заказов по дням и категориям товаров»
--  (виджет на дашборде З10).
--
--  ОДНА СТРОКА СВОДКИ = один день × одна категория.
--  Это НЕ то же самое, что «одна строка факта = одна позиция заказа»:
--  112 650 строк факта схлопываются в 18 808 строк сводки. В каждой
--  строке сводки уже лежит готовая сумма, и дашборду не нужно
--  пересчитывать 112 650 строк на каждое обновление экрана.
--
--  ПОЧЕМУ ДЕНЬ × КАТЕГОРИЯ, А НЕ ДЕНЬ × МАГАЗИН
--  ────────────────────────────────────────────
--  category_name_pt уже денормализована в факт (З07). Значит сводка
--  строится без единого джойна. Для разреза «день × продавец» пришлось
--  бы джойнить dim_seller на каждый блок вставки — медленнее и сложнее.
--
--  МЕТРИКА ТА ЖЕ. В сводку попадают только строки со статусом
--  `delivered`, и сумма считается по `amount` — ровно как в
--  canonical_metric.sql. Иначе на З11 карточка и SQL разъедутся.
--
--  ЧТО ЛЕЖИТ В СТРОКЕ
--  ──────────────────
--    gmv_brl      — SUM(amount), главная мера;
--    freight_brl  — SUM(freight_value), доставка (не входит в оборот);
--    qty          — SUM(qty), штук;
--    positions    — COUNT(*), число позиций факта.
--
--  ПОЧЕМУ AggregatingMergeTree, А НЕ SummingMergeTree
--  ─────────────────────────────────────────────────
--  SummingMergeTree складывает строки ТОЛЬКО при слиянии частей и только
--  по ключу сортировки, а незакрытые части продолжают отдавать дубли.
--  AggregatingMergeTree с SimpleAggregateFunction даёт то же самое, но
--  тип колонки сам говорит «это сумма», и её можно читать как обычное
--  число — без -State/-Merge комбинаторов в каждом запросе дашборда.
-- =====================================================================


-- ── Целевая таблица сводки ──────────────────────────────────────────────

-- SimpleAggregateFunction(sum, X) — колонка хранится как обычный X,
-- но при слиянии частей значения складываются. Для дашборда это важно:
-- `sum(gmv_brl)` работает без специальных комбинаторов.
--
-- ПОЧЕМУ Decimal(38, 2), А НЕ (18, 2). `sum(Decimal(12,2))` возвращает
-- Decimal(38,2) — это правило ClickHouse, а не наш выбор. Тип хранения
-- в SimpleAggregateFunction обязан совпадать с типом возврата, иначе
-- CREATE падает:
--   "Incompatible data types between aggregate function 'sum' which
--    returns Decimal(38, 2) and column storage type Decimal(18, 2)".
-- Ширина 38 — запас на суммирование, а не «деньги стали больше»:
-- точность после запятой остаётся 2 знака.
CREATE TABLE IF NOT EXISTS olist.mv_gmv_day_category
(
    order_date_key   Date                  COMMENT 'день покупки',
    category_name_pt LowCardinality(String) COMMENT 'категория товара',
    gmv_brl          SimpleAggregateFunction(sum, Decimal(38, 2))
                                           COMMENT 'SUM(amount) по delivered',
    freight_brl      SimpleAggregateFunction(sum, Decimal(38, 2))
                                           COMMENT 'SUM(freight_value)',
    qty              SimpleAggregateFunction(sum, UInt64)
                                           COMMENT 'SUM(qty)',
    positions        SimpleAggregateFunction(sum, UInt64)
                                           COMMENT 'COUNT(*) строк факта'
)
ENGINE = AggregatingMergeTree
PARTITION BY toYYYYMM(order_date_key)
ORDER BY (order_date_key, category_name_pt);


-- ── Materialized view: то, что наполняет сводку автоматически ──────────

-- `TO olist.mv_gmv_day_category` — view не хранит свои данные, а пишет
-- прямо в целевую таблицу. Поэтому читать надо таблицу, а не view.
--
-- ВАЖНО, ЧЕГО VIEW НЕ ДЕЛАЕТ:
--   • не пересчитывает уже загруженные данные — она реагирует только
--     на НОВЫЕ вставки в olist.fact_sales;
--   • не срабатывает на INSERT прямо в mv_gmv_day_category;
--   • не «догоняет» данные, если создать её после загрузки факта.
-- Поэтому сводка наполняется явным INSERT ... SELECT (см. rebuild_mv.sql),
-- а view остаётся страховкой на случай инкрементальной дозагрузки.
CREATE MATERIALIZED VIEW IF NOT EXISTS olist.mv_gmv_day_category_mv
TO olist.mv_gmv_day_category
AS
SELECT
    order_date_key,
    category_name_pt,
    sum(amount)         AS gmv_brl,
    sum(freight_value)  AS freight_brl,
    sum(qty)            AS qty,
    count()             AS positions
FROM olist.fact_sales
WHERE order_status = 'delivered'
GROUP BY order_date_key, category_name_pt;
