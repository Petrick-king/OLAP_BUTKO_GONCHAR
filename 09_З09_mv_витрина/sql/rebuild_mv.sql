-- =====================================================================
--  rebuild_mv.sql  —  пересборка сводки с нуля
--  Практика З09 «Сводная таблица под график», п.4
--
--  Зачем отдельный файл, если есть materialized view:
--  загрузка факта в этом проекте — ПОЛНЫЙ REFRESH (TRUNCATE + INSERT,
--  см. З07). View реагирует на каждую вставку, поэтому после перезагрузки
--  в сводке оказались бы и старые, и новые строки. Лечится так:
--  сначала очистить сводку, потом залить её заново из факта.
--
--  Порядок шагов жёсткий:
--      1) TRUNCATE сводки;
--      2) INSERT ... SELECT из факта;
--      3) сверка суммы с эталоном.
--
--  Запуск:  python 09_З09_mv_витрина/etl/build_mv.py
-- =====================================================================


-- @step 1. Очистка сводки

-- TRUNCATE для MergeTree-семейства — это удаление всех частей таблицы.
-- Партиции при этом сохраняются как определение, данные — нет.
TRUNCATE TABLE IF EXISTS olist.mv_gmv_day_category;


-- @step 2. Заливка сводки из детального факта

-- Тот же фильтр и та же мера, что в canonical_metric.sql:
--   order_status = 'delivered', SUM(amount).
-- Если поменять здесь определение метрики — на З11 карточка разойдётся
-- с эталонным SQL, и починить это можно будет только здесь.
INSERT INTO olist.mv_gmv_day_category
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
