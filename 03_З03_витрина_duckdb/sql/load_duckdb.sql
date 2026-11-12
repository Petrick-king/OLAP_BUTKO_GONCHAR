-- =====================================================================
--  load_duckdb.sql  —  загрузка сырья в витрину DuckDB
--  Практика З03 «Таблицы в DuckDB»
--
--  Считывает CSV из {RAW} (по умолчанию ../../data/raw) и кладёт в
--  справочники, затем в факт. Порядок обязателен: dim_product нужен
--  для денормализации category_name_pt в факт.
--
--  ИДЕМПОТЕНТНОСТЬ. Первой строкой идёт «очистить целиком и загрузить
--  заново» — полный refresh. Второй прогон даёт ровно тот же результат,
--  а не вдвое больше строк. Подробнее: README.md § «Повторная загрузка».
--
--  Плейсхолдер {RAW} подставляет etl/build_duckdb.py.
-- =====================================================================

-- ─────────────────────────────── СПРАВОЧНИКИ ───────────────────────────

-- dim_geo: в сырье 1 000 163 строки и 17 972 повторяющихся префикса.
-- Усредняем координаты, чтобы ключ стал уникальным.
INSERT INTO dim_geo
SELECT
    CAST(geolocation_zip_code_prefix AS VARCHAR),
    ROUND(AVG(geolocation_lat), 6),
    ROUND(AVG(geolocation_lng), 6),
    ANY_VALUE(geolocation_city),
    ANY_VALUE(geolocation_state),
    COUNT(*)
FROM read_csv_auto('{RAW}/olist_geolocation_dataset.csv')
GROUP BY 1;

-- dim_category: словарь переводов + те категории, что реально в товарах,
-- но без перевода (2 категории, 13 товаров), + категория-заглушка для
-- 610 товаров без категории.
INSERT INTO dim_category
WITH used AS (
    SELECT DISTINCT product_category_name
    FROM read_csv_auto('{RAW}/olist_products_dataset.csv')
    WHERE product_category_name IS NOT NULL
    UNION ALL
    SELECT 'nao_categorizado'
)
SELECT
    CAST(u.product_category_name AS VARCHAR),
    CAST(t.product_category_name_english AS VARCHAR),
    (t.product_category_name_english IS NOT NULL)
FROM used u
LEFT JOIN read_csv_auto('{RAW}/product_category_name_translation.csv') t
       ON t.product_category_name = u.product_category_name;

-- dim_date: календаря в сырье нет — генерируем диапазон дат покупок.
INSERT INTO dim_date
WITH bounds AS (
    SELECT
        CAST(MIN(order_purchase_timestamp) AS DATE) AS min_d,
        CAST(MAX(order_purchase_timestamp) AS DATE) AS max_d
    FROM read_csv_auto('{RAW}/olist_orders_dataset.csv')
)
SELECT
    date_key,
    CAST(EXTRACT(YEAR    FROM date_key) AS SMALLINT),
    CAST(EXTRACT(QUARTER FROM date_key) AS TINYINT),
    CAST(EXTRACT(MONTH   FROM date_key) AS TINYINT),
    CASE CAST(EXTRACT(MONTH FROM date_key) AS TINYINT)
        WHEN 1 THEN 'janeiro'   WHEN 2 THEN 'fevereiro' WHEN 3 THEN 'março'
        WHEN 4 THEN 'abril'     WHEN 5 THEN 'maio'      WHEN 6 THEN 'junho'
        WHEN 7 THEN 'julho'     WHEN 8 THEN 'agosto'    WHEN 9 THEN 'setembro'
        WHEN 10 THEN 'outubro'  WHEN 11 THEN 'novembro' ELSE 'dezembro'
    END,
    STRFTIME(date_key, '%Y-%m'),
    DAYOFWEEK(date_key) IN (0, 6)
FROM (
    SELECT UNNEST(GENERATE_SERIES(min_d, max_d, INTERVAL 1 DAY))::DATE AS date_key
    FROM bounds
);

-- dim_customer: аккаунты + координаты из dim_geo по почтовому префиксу.
INSERT INTO dim_customer
WITH c AS (
    SELECT * FROM read_csv_auto('{RAW}/olist_customers_dataset.csv')
)
SELECT
    c.customer_id,
    c.customer_unique_id,
    CAST(c.customer_zip_code_prefix AS VARCHAR),
    c.customer_city,
    c.customer_state,
    g.geo_lat,
    g.geo_lng
FROM c
LEFT JOIN dim_geo g
       ON g.zip_code_prefix = CAST(c.customer_zip_code_prefix AS VARCHAR);

-- dim_seller: продавцы + координаты.
INSERT INTO dim_seller
WITH s AS (
    SELECT * FROM read_csv_auto('{RAW}/olist_sellers_dataset.csv')
)
SELECT
    s.seller_id,
    CAST(s.seller_zip_code_prefix AS VARCHAR),
    s.seller_city,
    s.seller_state,
    g.geo_lat,
    g.geo_lng
FROM s
LEFT JOIN dim_geo g
       ON g.zip_code_prefix = CAST(s.seller_zip_code_prefix AS VARCHAR);

-- dim_product: 610 товаров без категории получают `nao_categorizado`,
-- 13 товаров без перевода — PT-название с is_translated = false.
-- Ни один товар не теряется и не остаётся с NULL.
INSERT INTO dim_product
WITH p AS (
    SELECT * FROM read_csv_auto('{RAW}/olist_products_dataset.csv')
)
SELECT
    p.product_id,
    COALESCE(p.product_category_name, 'nao_categorizado'),
    COALESCE(c.category_name_en, p.product_category_name, 'nao_categorizado'),
    COALESCE(c.is_translated, FALSE),
    CAST(p.product_name_lenght        AS INTEGER),
    CAST(p.product_description_lenght AS INTEGER),
    CAST(p.product_photos_qty         AS INTEGER),
    CAST(p.product_weight_g           AS INTEGER),
    CAST(p.product_length_cm          AS INTEGER),
    CAST(p.product_height_cm          AS INTEGER),
    CAST(p.product_width_cm           AS INTEGER)
FROM p
LEFT JOIN dim_category c
       ON c.category_name_pt = p.product_category_name;

-- ───────────────────────────────── ФАКТ ────────────────────────────────

-- fact_sales: одна строка = одна позиция одного заказа.
-- Платежи и отзывы — сущности уровня ЗАКАЗА, поэтому сначала схлопываем
-- их до order_id, и только потом джойним к позициям.
-- Колонки перечислены ЯВНО, а не позиционно: иначе добавление меры
-- (например qty на З03) молча сдвинуло бы все колонки вставки.
INSERT INTO fact_sales (
    order_item_sk, order_id, order_item_id,
    order_date_key, customer_id, seller_id, product_id, category_name_pt,
    order_status, order_purchase_timestamp, order_delivered_carrier_date,
    order_delivered_customer_date, order_estimated_delivery_date, shipping_limit_date,
    amount, qty, freight_value, payment_value_order,
    is_delivered, delivery_days, is_late,
    payment_type, review_score
)
WITH
payments AS (
    SELECT
        order_id,
        SUM(CAST(payment_value AS DECIMAL(12,2)))                            AS payment_value_order,
        -- основной способ оплаты = способ платежа с наибольшей суммой.
        -- при равенстве — наименьший payment_sequential
        FIRST(payment_type ORDER BY CAST(payment_value AS DECIMAL(12,2)) DESC,
                                     payment_sequential ASC)                 AS payment_type
    FROM read_csv_auto('{RAW}/olist_order_payments_dataset.csv')
    GROUP BY 1
),
reviews AS (
    SELECT
        order_id,
        ROUND(AVG(review_score), 2)::TINYINT                                 AS review_score
    FROM read_csv_auto('{RAW}/olist_order_reviews_dataset.csv')
    GROUP BY 1
),
orders AS (
    SELECT
        order_id,
        customer_id,
        order_status,
        order_purchase_timestamp,
        order_delivered_carrier_date,
        order_delivered_customer_date,
        order_estimated_delivery_date,
        CAST(order_purchase_timestamp AS DATE)                               AS order_date_key
    FROM read_csv_auto('{RAW}/olist_orders_dataset.csv')
),
items AS (
    SELECT * FROM read_csv_auto('{RAW}/olist_order_items_dataset.csv')
)
SELECT
    ROW_NUMBER() OVER (ORDER BY i.order_id, i.order_item_id),

    i.order_id,
    CAST(i.order_item_id AS INTEGER),

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

    CAST(i.price                     AS DECIMAL(12,2)),   -- amount: сумма позиции
    1,                                                  -- qty: одна позиция = одна единица
    CAST(i.freight_value             AS DECIMAL(12,2)),
    CAST(pay.payment_value_order     AS DECIMAL(12,2)),

    (o.order_status = 'delivered')::INTEGER,
    DATE_DIFF('day', CAST(o.order_purchase_timestamp AS DATE),
                       CAST(o.order_delivered_customer_date AS DATE)),
    (o.order_delivered_customer_date IS NOT NULL
     AND o.order_delivered_customer_date > o.order_estimated_delivery_date)::INTEGER,

    pay.payment_type,
    rv.review_score
FROM items i
JOIN      orders   o   ON o.order_id  = i.order_id
LEFT JOIN payments pay ON pay.order_id = i.order_id
LEFT JOIN reviews  rv  ON rv.order_id = i.order_id
LEFT JOIN dim_product pr ON pr.product_id = i.product_id;