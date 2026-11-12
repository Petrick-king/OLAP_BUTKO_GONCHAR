-- =====================================================================
--  analytics.sql  —  ответы на три вопроса из паспорта
--  Практика З04 «Аналитический SQL»
--
--  База: data/warehouse.duckdb (собрана на З03).
--  Запуск:  python 04_З04_аналитический_sql/etl/run_analytics.py
--
--  ВОПРОСЫ (паспорт §6):
--    §1 Вопрос 1 — оборот по регионам продавца в 2017 году   → WHERE + GROUP BY
--    §2 Вопрос 2 — топ-5 категорий и их доля оборота 2018    → GROUP BY + ОКНО
--    §3 Вопрос 3 — помесячная динамика оборота 2017          → GROUP BY + ОКНО (LAG)
--    §4 сверх минимума — своевременность доставки по штатам   → GROUP BY + ОКНО
--
--  Заданее всегда считаем ОДИН и тот же показатель — оборот доставленных
--  заказов (см. canonical_metric.sql). Иначе суммы по срезам не будут
--  складываться в общий итог и сверка на З11 «разъедется».
--
--  ИМЕНА МЕР приведены к образцу звезды из раздаточных материалов
--  (`раздаточные материалы/star_retail.md`):
--      amount — сумма позиции (в сырье price), SUM можно;
--      qty    — число штук, SUM можно.
--  Подробное объяснение — в ddl_duckdb.sql.
-- =====================================================================


-- ══════════════════════════════════════════════════════════════════════
-- §1  ВОПРОС 1:  «Какой оборот доставленных заказов у продавцов
--                 по регионам (штатам) в 2017 году?»
--
--  Тип: условие WHERE + сворачивание GROUP BY + сортировка.
--  Здесь окно НЕ нужно: нужен просто итог по каждому штату.
--  JOIN идёт в dim_seller — регион того, кто ПРОДАЁТ, а не покупает.
--
--  ⚠️ ВНИМАНИЕ, что можно и что нельзя складывать по этой таблице:
--    gmv_brl и freight_brl  — аддитивны. Сумма по всем штатам = обороту
--                             года (5 962 902.01) без задвоений.
--    orders                  — НЕ аддитивен! 190 заказов 2017 года содержат
--                             позиции от продавцов из разных штатов, поэтому
--                             в сумме по регионам заказов 43 621,
--                             а заказов на самом деле 43 428.
--                             Складывать orders по регионам нельзя,
--                             а делить gmv_brl на orders внутри региона —
--                             можно: внутри одного региона заказ учтён один раз.
-- ══════════════════════════════════════════════════════════════════════

SELECT
    s.seller_state                              AS region,          -- штат продавца
    COUNT(DISTINCT f.order_id)                  AS orders,
    COUNT(*)                                    AS positions,
    SUM(f.qty)                                  AS units,           -- штук
    SUM(f.amount)                               AS gmv_brl,
    SUM(f.freight_value)                        AS freight_brl,
    SUM(f.amount) / COUNT(DISTINCT f.order_id)  AS gmv_per_order
FROM fact_sales f
JOIN dim_seller s ON s.seller_id = f.seller_id
WHERE f.order_status = 'delivered'
  AND f.order_date_key >= DATE '2017-01-01'
  AND f.order_date_key <  DATE '2018-01-01'
GROUP BY s.seller_state
ORDER BY gmv_brl DESC;


-- ══════════════════════════════════════════════════════════════════════
-- §2  ВОПРОС 2:  «Какие топ-5 категорий товаров по обороту в 2018 году
--                 и какую долю оборота они дают?»
--
--  Тип: GROUP BY + ОКОННАЯ ФУНКЦИЯ.
--  Здесь окно обязательно: обычный GROUP BY схлопывает строки в итог
--  и не может сказать «а какую долю это от всего?». Долю даёт
--  SUM(...) OVER () — сумма по всем категориям КАК ОКНО, не как группа.
--
--  RANK() — место в рейтинге. RANK, а не ROW_NUMBER: при равном обороте
--  две категории должны получить одно место, а не «победила та,
--  что случайно раньше отсортировалась».
-- ══════════════════════════════════════════════════════════════════════

WITH by_category AS (
    SELECT
        f.category_name_pt                                   AS category,
        SUM(f.amount)                                        AS gmv_brl,
        SUM(f.qty)                                           AS units
    FROM fact_sales f
    WHERE f.order_status = 'delivered'
      AND f.order_date_key >= DATE '2018-01-01'
      AND f.order_date_key <  DATE '2019-01-01'
    GROUP BY 1
)
SELECT
    category,
    gmv_brl,
    units,
    -- доля категории в общем обороте года — сумма по всем категориям как ОКНО
    gmv_brl / SUM(gmv_brl) OVER ()                            AS share_of_gmv,
    -- место в рейтинге
    RANK() OVER (ORDER BY gmv_brl DESC)                       AS place,
    -- накопительная доля сверху — «сколько категорий закрывают половину оборота»
    SUM(gmv_brl) OVER (ORDER BY gmv_brl DESC
                      ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)
        / SUM(gmv_brl) OVER ()                                AS cumulative_share
FROM by_category
ORDER BY gmv_brl DESC
LIMIT 5;


-- ══════════════════════════════════════════════════════════════════════
-- §3  ВОПРОС 3:  «Как менялся помесячный оборот 2017 года — сколько
--                 заказов в месяце и какой прирост к предыдущему месяцу?»
--
--  Тип: GROUP BY + ОКОННАЯ ФУНКЦИЯ LAG() с собственным окном упорядочивания.
--  LAG смотрит В СОБСТВЕННУЮ строку результата GROUP BY — это то, что
--  обычный GROUP BY не умеет: «строка минус предыдущая строка».
--  Первый месяц: предыдущего нет — прирост неизвестен, а не 0 %,
--  иначе график покажет фальшивый рост в январе.
-- ══════════════════════════════════════════════════════════════════════

WITH monthly AS (
    SELECT
        d.year_month                                   AS year_month,
        COUNT(DISTINCT f.order_id)                      AS orders,
        COUNT(*)                                        AS positions,
        SUM(f.qty)                                      AS units,
        SUM(f.amount)                                   AS gmv_brl
    FROM fact_sales f
    JOIN dim_date d ON d.date_key = f.order_date_key
    WHERE f.order_status = 'delivered'
      AND f.order_date_key >= DATE '2017-01-01'
      AND f.order_date_key <  DATE '2018-01-01'
    GROUP BY 1
)
SELECT
    year_month,
    orders,
    positions,
    units,
    gmv_brl,
    LAG(gmv_brl) OVER (ORDER BY year_month)             AS prev_month_gmv,
    gmv_brl - LAG(gmv_brl) OVER (ORDER BY year_month)    AS gmv_delta_brl,
    ROUND(
        100.0 * (gmv_brl - LAG(gmv_brl) OVER (ORDER BY year_month))
        / NULLIF(LAG(gmv_brl) OVER (ORDER BY year_month), 0),
    2)                                                  AS mom_percent
FROM monthly
ORDER BY year_month;


-- ══════════════════════════════════════════════════════════════════════
-- §4  СВЕРХ МИНИМУМА:  «Насколько продавцы доставляют вовремя и как это
--                      отличается по регионам ПОКУПАТЕЛЯ?»
--
--  Тип: GROUP BY + оконная доля. Доля считается окном по ВСЕМ регионам,
--  чтобы увидеть не абсолютный процент (он у всех ~90 %), а вклад
--  региона в общий объём доставок.
-- ══════════════════════════════════════════════════════════════════════

WITH by_region AS (
    SELECT
        c.customer_state                            AS region,
        COUNT(*)                                    AS positions,
        SUM(f.qty)                                  AS units,
        SUM(f.is_late)                              AS late_positions,
        ROUND(AVG(f.delivery_days), 2)              AS avg_delivery_days
    FROM fact_sales f
    JOIN dim_customer c ON c.customer_id = f.customer_id
    WHERE f.is_delivered = 1
    GROUP BY 1
)
SELECT
    region,
    positions,
    units,
    late_positions,
    late_positions::DOUBLE / positions             AS late_share,
    avg_delivery_days,
    -- доля доставок региона во всех доставках — окно по всем регионам
    positions::DOUBLE / SUM(positions) OVER ()     AS share_of_all_deliveries
FROM by_region
ORDER BY late_share DESC, positions DESC
LIMIT 10;