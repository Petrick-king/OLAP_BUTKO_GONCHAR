-- =====================================================================
--  stage.sql  —  ЗАГРУЗКА СЫРЬЯ В ЗОНУ STAGING (stg_*)
--  Практика З05 «Загрузка и проверки»
--
--  Зачем отдельная зона staging. Проверки качества должны выполняться
--  ДО того, как данные попадут в витрину. Если грузить сразу в рабочие
--  таблицы, то плохие данные окажутся в отчётах раньше, чем их увидит
--  проверка. Поэтому:
--
--      CSV → raw_* (views) → stg_* → проверки → publish.sql → витрина
--                                                ↑
--                                    если хоть одна проверка упала —
--                                    ROLLBACK, рабочие таблицы не трогаем
--
--  Порядок: СПРАВОЧНИКИ → ФАКТ.
--  Иначе в факте появятся ссылки на продавцов и товаров, которых нет.
--
--  Плейсхолдер {RAW} подставляет etl/run_etl.py.
-- =====================================================================

-- ══════════════════════ СЛОЙ 0. СЫРЬЁ КАК ВЬЮ ═════════════════════════
-- Слой сырья объявляем представлениями, а не инлайновым
-- read_csv_auto(...) в каждом месте. Две причины:
--   1) путь к файлу пишется один раз, а не в десяти запросах;
--   2) агрегат по read_csv_auto с константным путём DuckDB не даёт
--      выполнить его не в корневом подзапросе — проверкам это нужно.

CREATE OR REPLACE VIEW raw_customers  AS SELECT * FROM read_csv_auto('{RAW}/olist_customers_dataset.csv');
CREATE OR REPLACE VIEW raw_orders     AS SELECT * FROM read_csv_auto('{RAW}/olist_orders_dataset.csv');
CREATE OR REPLACE VIEW raw_items      AS SELECT * FROM read_csv_auto('{RAW}/olist_order_items_dataset.csv');
CREATE OR REPLACE VIEW raw_payments   AS SELECT * FROM read_csv_auto('{RAW}/olist_order_payments_dataset.csv');
CREATE OR REPLACE VIEW raw_reviews    AS SELECT * FROM read_csv_auto('{RAW}/olist_order_reviews_dataset.csv');
CREATE OR REPLACE VIEW raw_products   AS SELECT * FROM read_csv_auto('{RAW}/olist_products_dataset.csv');
CREATE OR REPLACE VIEW raw_sellers    AS SELECT * FROM read_csv_auto('{RAW}/olist_sellers_dataset.csv');
CREATE OR REPLACE VIEW raw_geo        AS SELECT * FROM read_csv_auto('{RAW}/olist_geolocation_dataset.csv');
CREATE OR REPLACE VIEW raw_categories AS SELECT * FROM read_csv_auto('{RAW}/product_category_name_translation.csv');

-- ══════════════════════ СЛОЙ 1. ЗОНА STAGING ═══════════════════════════

DROP TABLE IF EXISTS stg_fact_sales;
DROP TABLE IF EXISTS stg_dim_product;
DROP TABLE IF EXISTS stg_dim_seller;
DROP TABLE IF EXISTS stg_dim_customer;
DROP TABLE IF EXISTS stg_dim_date;
DROP TABLE IF EXISTS stg_dim_category;
DROP TABLE IF EXISTS stg_dim_geo;

-- ── СПРАВОЧНИКИ ─────────────────────────────────────────────────────────

-- stg_dim_geo: 1 000 163 точки схлопываются до 19 015 префиксов.
-- Без этого джойн по префиксу размножит факт примерно в 55 раз.
CREATE TABLE stg_dim_geo AS
SELECT
    CAST(geolocation_zip_code_prefix AS VARCHAR)   AS zip_code_prefix,
    ROUND(AVG(geolocation_lat), 6)                 AS geo_lat,
    ROUND(AVG(geolocation_lng), 6)                 AS geo_lng,
    ANY_VALUE(geolocation_city)                    AS geo_city,
    ANY_VALUE(geolocation_state)                   AS geo_state,
    COUNT(*)                                       AS geo_points_cnt
FROM raw_geo
GROUP BY 1;

-- stg_dim_category: словарь переводов ∪ категории, реально встречающиеся
-- в товарах. Так попадают 2 категории без перевода (13 товаров).
--
-- В UNION добавлена категория-заглушка nao_categorizado для 610 товаров
-- без категории. Она ОБЯЗАНА быть в справочнике: строки факта несут
-- category_name_pt из dim_product, и без заглушки в справочнике 1603
-- позиции оказались бы сиротами (проверка QC-13).
CREATE TABLE stg_dim_category AS
WITH used AS (
    SELECT DISTINCT product_category_name
    FROM raw_products
    WHERE product_category_name IS NOT NULL
    UNION ALL
    SELECT 'nao_categorizado'
)
SELECT
    CAST(u.product_category_name AS VARCHAR)              AS category_name_pt,
    CAST(t.product_category_name_english AS VARCHAR)      AS category_name_en,
    (t.product_category_name_english IS NOT NULL)         AS is_translated
FROM used u
LEFT JOIN raw_categories t
       ON t.product_category_name = u.product_category_name;

-- stg_dim_date: календаря в сырье нет — генерируем диапазон дат покупок.
CREATE TABLE stg_dim_date AS
WITH bounds AS (
    SELECT
        CAST(MIN(order_purchase_timestamp) AS DATE) AS min_d,
        CAST(MAX(order_purchase_timestamp) AS DATE) AS max_d
    FROM raw_orders
)
SELECT
    date_key,
    CAST(EXTRACT(YEAR    FROM date_key) AS SMALLINT) AS year,
    CAST(EXTRACT(QUARTER FROM date_key) AS TINYINT)  AS quarter,
    CAST(EXTRACT(MONTH   FROM date_key) AS TINYINT)  AS month_num,
    CASE CAST(EXTRACT(MONTH FROM date_key) AS TINYINT)
        WHEN 1 THEN 'janeiro'   WHEN 2 THEN 'fevereiro' WHEN 3 THEN 'março'
        WHEN 4 THEN 'abril'     WHEN 5 THEN 'maio'      WHEN 6 THEN 'junho'
        WHEN 7 THEN 'julho'     WHEN 8 THEN 'agosto'    WHEN 9 THEN 'setembro'
        WHEN 10 THEN 'outubro'  WHEN 11 THEN 'novembro' ELSE 'dezembro'
    END AS month_name_pt,
    STRFTIME(date_key, '%Y-%m') AS year_month,
    DAYOFWEEK(date_key) IN (0, 6) AS is_weekend
FROM (
    SELECT UNNEST(GENERATE_SERIES(min_d, max_d, INTERVAL 1 DAY))::DATE AS date_key
    FROM bounds
);

-- stg_dim_customer: аккаунты + координаты из справочника географии.
CREATE TABLE stg_dim_customer AS
SELECT
    c.customer_id,
    c.customer_unique_id,
    CAST(c.customer_zip_code_prefix AS VARCHAR) AS customer_zip_code_prefix,
    c.customer_city,
    c.customer_state,
    g.geo_lat,
    g.geo_lng
FROM raw_customers c
LEFT JOIN stg_dim_geo g
       ON g.zip_code_prefix = CAST(c.customer_zip_code_prefix AS VARCHAR);

-- stg_dim_seller: продавцы + координаты.
CREATE TABLE stg_dim_seller AS
SELECT
    s.seller_id,
    CAST(s.seller_zip_code_prefix AS VARCHAR) AS seller_zip_code_prefix,
    s.seller_city,
    s.seller_state,
    g.geo_lat,
    g.geo_lng
FROM raw_sellers s
LEFT JOIN stg_dim_geo g
       ON g.zip_code_prefix = CAST(s.seller_zip_code_prefix AS VARCHAR);

-- stg_dim_product: 610 товаров без категории → nao_categorizado;
-- 13 товаров без перевода → PT-название с is_category_translated = false.
CREATE TABLE stg_dim_product AS
SELECT
    p.product_id,
    COALESCE(p.product_category_name, 'nao_categorizado') AS category_name_pt,
    COALESCE(c.category_name_en, p.product_category_name, 'nao_categorizado')
        AS category_name_en,
    COALESCE(c.is_translated, FALSE) AS is_category_translated,
    CAST(p.product_name_lenght        AS INTEGER) AS product_name_lenght,
    CAST(p.product_description_lenght AS INTEGER) AS product_description_lenght,
    CAST(p.product_photos_qty         AS INTEGER) AS product_photos_qty,
    CAST(p.product_weight_g           AS INTEGER) AS product_weight_g,
    CAST(p.product_length_cm          AS INTEGER) AS product_length_cm,
    CAST(p.product_height_cm          AS INTEGER) AS product_height_cm,
    CAST(p.product_width_cm           AS INTEGER) AS product_width_cm
FROM raw_products p
LEFT JOIN stg_dim_category c
       ON c.category_name_pt = p.product_category_name;

-- ── ФАКТ ────────────────────────────────────────────────────────────────

-- Платежи и отзывы — сущности уровня ЗАКАЗА. Схлопываем до order_id,
-- иначе одна оплата размножится на каждую позицию заказа.
CREATE OR REPLACE VIEW stg_payments AS
SELECT
    order_id,
    SUM(CAST(payment_value AS DECIMAL(12,2))) AS payment_value_order,
    FIRST(payment_type ORDER BY CAST(payment_value AS DECIMAL(12,2)) DESC,
                                 payment_sequential ASC) AS payment_type
FROM raw_payments
GROUP BY 1;

CREATE OR REPLACE VIEW stg_reviews AS
SELECT
    order_id,
    ROUND(AVG(review_score), 2)::TINYINT AS review_score
FROM raw_reviews
GROUP BY 1;

CREATE OR REPLACE VIEW stg_orders AS
SELECT
    order_id,
    customer_id,
    order_status,
    order_purchase_timestamp,
    order_delivered_carrier_date,
    order_delivered_customer_date,
    order_estimated_delivery_date,
    CAST(order_purchase_timestamp AS DATE) AS order_date_key
FROM raw_orders;

-- stg_fact_sales: ОДНА СТРОКА = ОДНА ПОЗИЦИЯ ОДНОГО ЗАКАЗА.
--
-- Меры приведены к образцу звезды из раздаточных материалов
-- (`раздаточные материалы/star_retail.md`): amount — сумма позиции,
-- qty — число штук. В Olist количества в позиции нет, поэтому qty = 1
-- по построению: одна позиция заказа = одна единица товара.
CREATE TABLE stg_fact_sales AS
SELECT
    ROW_NUMBER() OVER (ORDER BY i.order_id, i.order_item_id) AS order_item_sk,
    i.order_id,
    CAST(i.order_item_id AS INTEGER)      AS order_item_id,
    o.order_date_key,
    o.customer_id,
    i.seller_id,
    i.product_id,
    pr.category_name_pt,
    o.order_status,
    o.order_purchase_timestamp,
    o.order_delivered_carrier_date,
    o.order_delivered_customer_date,
    o.order_estimated_delivery_date,
    i.shipping_limit_date,
    CAST(i.price                 AS DECIMAL(12,2)) AS amount,   -- сумма позиции
    1                                                AS qty,      -- одна единица
    CAST(i.freight_value         AS DECIMAL(12,2)) AS freight_value,
    CAST(pay.payment_value_order AS DECIMAL(12,2)) AS payment_value_order,
    (o.order_status = 'delivered')::INTEGER     AS is_delivered,
    DATE_DIFF('day', CAST(o.order_purchase_timestamp AS DATE),
                       CAST(o.order_delivered_customer_date AS DATE)) AS delivery_days,
    (o.order_delivered_customer_date IS NOT NULL
     AND o.order_delivered_customer_date > o.order_estimated_delivery_date)::INTEGER AS is_late,
    pay.payment_type,
    rv.review_score
FROM raw_items i
JOIN      stg_orders   o   ON o.order_id  = i.order_id
LEFT JOIN stg_payments pay ON pay.order_id = i.order_id
LEFT JOIN stg_reviews  rv  ON rv.order_id = i.order_id
LEFT JOIN stg_dim_product pr ON pr.product_id = i.product_id;

-- stg_fact_orders: ОБЕРТКА ФАКТА ДО УРОВНЯ ЗАКАЗА (одна строка на order_id).
-- Нужна проверкам качества: платёж в факте скопирован в каждую позицию,
-- поэтому оплату нельзя сверять напрямую. Эта вью — единственное место,
-- где «сумма по заказу» вообще имеет смысл.
CREATE OR REPLACE VIEW stg_fact_orders AS
SELECT
    order_id,
    MAX(payment_value_order) AS order_pay,
    COUNT(*)                 AS positions_in_order
FROM stg_fact_sales
GROUP BY 1;