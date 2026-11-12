-- =====================================================================
--  queries_clickhouse.sql  —  запросы З04, выполненные в ClickHouse
--  Практика З08 «Запросы и ключи», п.1
--
--  ВЗЯТО ИЗ З04 (sql/canonical_metric.sql и sql/analytics.sql):
--     §1  эталон главной метрики            — переносится БЕЗ единой правки
--     §2  топ-5 категорий 2018             — правка: Decimal → toFloat64
--     §3  помесячная динамика 2017         — правки: LAG → lagInFrame,
--                                            dim_date → toStartOfMonth
--
--  ПОЧЕМУ ИМЕННО ЭТИ ТРИ. Задание требует «эталон + ещё два запроса».
--  Взяты те два, где есть окна: если ClickHouse посчитает их неправильно,
--  это будет видно по числу, а не по «запрос выполнился».
--
--  ФОРМАТ ФАЙЛА. Строка `-- @query N. заголовок` открывает очередной
--  запрос. Для clickhouse-client это обычный комментарий, поэтому файл
--  целиком выполняется и так:
--      clickhouse-client --multiquery < sql/queries_clickhouse.sql
--  Маркер нужен только скрипту etl/run_queries.py, чтобы он знал, как
--  назвать каждый результат: у ClickHouse нет «одного запроса на файл».
--
--  БАЗА: olist @ localhost:8123, пользователь olist.
-- =====================================================================


-- @query 1. Эталон главной метрики — GMV_delivered

-- Это ДОСЛОВНО тот же SQL, что в 04_З04_аналитический_sql/sql/canonical_metric.sql.
-- Ни одного изменения: имена колонок и типы в ClickHouse совпадают с DuckDB.
-- Ожидаемое число — 13 221 498.11 BRL.
SELECT
    'GMV_delivered'                     AS metric_name,
    'Сумма amount по заказам в статусе delivered, BRL'
                                        AS metric_definition,
    SUM(amount)                         AS metric_value,
    COUNT(*)                            AS positions_count,
    COUNT(DISTINCT order_id)            AS orders_count,
    SUM(qty)                            AS units_count
FROM olist.fact_sales
WHERE order_status = 'delivered';


-- @query 2. Топ-5 категорий 2018 и их доля оборота

-- Правка против З04 ровно одна: в ClickHouse деление Decimal на Decimal
-- даёт Decimal, и доля обрезается до двух знаков. Поэтому обе стороны
-- приводятся к Float64 явно. Это НЕ потеря точности денег: деньги
-- остаются Decimal, во Float64 уходит только безразмерная доля.
--
-- RANK() в ClickHouse есть и ведёт себя как в стандарте: при равном
-- обороте две категории получают одно место.
WITH by_category AS (
    SELECT
        category_name_pt                       AS category,
        SUM(amount)                            AS gmv_brl,
        SUM(qty)                               AS units
    FROM olist.fact_sales
    WHERE order_status = 'delivered'
      AND order_date_key >= DATE '2018-01-01'
      AND order_date_key <  DATE '2019-01-01'
    GROUP BY category_name_pt
)
SELECT
    category,
    gmv_brl,
    units,
    toFloat64(gmv_brl) / toFloat64(SUM(gmv_brl) OVER ())
                                            AS share_of_gmv,
    RANK() OVER (ORDER BY gmv_brl DESC)      AS place,
    toFloat64(SUM(gmv_brl) OVER (
        ORDER BY gmv_brl DESC
        ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW))
      / toFloat64(SUM(gmv_brl) OVER ())      AS cumulative_share
FROM by_category
ORDER BY gmv_brl DESC
LIMIT 5;


-- @query 3. Помесячная динамика оборота 2017 и прирост к прошлому месяцу

-- Три правки против З04:
--   1) LAG() в ClickHouse называется lagInFrame(). Функции lag() нет —
--      запрос падает с UNKNOWN_AGGREGATE_FUNCTION. Это и есть та «мелочь»,
--      которую задание просит поправить.
--   2) В З04 месяц брался из dim_date.year_month. Здесь он считается
--      прямо из даты: toStartOfMonth + formatDateTime. Джойн на 774 строки
--      ради строки 'YYYY-MM' не нужен, а результат тот же.
--   3) lagInFrame по умолчанию возвращает НОЛЬ нужного типа, а не NULL.
--      Поэтому колонка оборачивается в toNullable() и передаётся
--      явный default = NULL: первый месяц должен быть «неизвестно»,
--      а не «0.00». Иначе график покажет фальшивый рост в январе.
WITH monthly AS (
    SELECT
        formatDateTime(toStartOfMonth(order_date_key), '%Y-%m')  AS year_month,
        COUNT(DISTINCT order_id)                                 AS orders,
        COUNT(*)                                                 AS positions,
        SUM(qty)                                                 AS units,
        SUM(amount)                                              AS gmv_brl
    FROM olist.fact_sales
    WHERE order_status = 'delivered'
      AND order_date_key >= DATE '2017-01-01'
      AND order_date_key <  DATE '2018-01-01'
    GROUP BY year_month
)
SELECT
    year_month,
    orders,
    positions,
    units,
    gmv_brl,
    lagInFrame(toNullable(gmv_brl), 1, NULL) OVER (
        ORDER BY year_month
        ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)        AS prev_month_gmv,
    gmv_brl - lagInFrame(toNullable(gmv_brl), 1, NULL) OVER (
        ORDER BY year_month
        ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)        AS gmv_delta_brl,
    ROUND(
        100.0 * (gmv_brl - lagInFrame(toNullable(gmv_brl), 1, NULL) OVER (
            ORDER BY year_month
            ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW))
        / nullIf(lagInFrame(toNullable(gmv_brl), 1, NULL) OVER (
            ORDER BY year_month
            ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW), 0),
    2)                                                           AS mom_percent
FROM monthly
ORDER BY year_month;
