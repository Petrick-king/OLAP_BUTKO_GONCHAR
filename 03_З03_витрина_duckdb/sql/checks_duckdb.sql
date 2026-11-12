-- =====================================================================
--  checks_duckdb.sql  —  проверки после загрузки (З03, п.3 «Проверки»)
--
--  Что смотрим и зачем — в комментарии напротив каждого блока.
--  Полный отчёт: ../evidence/checks.md
-- =====================================================================

-- ── 1. Сколько строк в каждой таблице: сходится ли с сырьём? ─────────────
SELECT 'fact_sales'      AS table_name, COUNT(*) AS row_count FROM fact_sales
UNION ALL SELECT 'dim_product',  COUNT(*) FROM dim_product
UNION ALL SELECT 'dim_customer', COUNT(*) FROM dim_customer
UNION ALL SELECT 'dim_seller',   COUNT(*) FROM dim_seller
UNION ALL SELECT 'dim_category', COUNT(*) FROM dim_category
UNION ALL SELECT 'dim_date',     COUNT(*) FROM dim_date
UNION ALL SELECT 'dim_geo',      COUNT(*) FROM dim_geo
ORDER BY 1;

-- Ожидание по сырью:
--   fact_sales 112 650 = столько позиций в olist_order_items_dataset.csv
--   dim_product  32 951 = столько товаров в olist_products_dataset.csv
--   dim_customer 99 441 = столько строк в olist_customers_dataset.csv
--   dim_seller    3 095 = столько строк в olist_sellers_dataset.csv
--   dim_category     74 = 71 перевод + 2 категории, которых в словаре нет
--                          + 1 заглушка nao_categorizado для товаров без категории
--   dim_date        774 = дней с 2016-09-04 по 2018-10-17 включительно
--   dim_geo      19 015 = из 1 000 163 точек после схлопывания по префиксу

-- ── 2. Фраза «одна строка = одна позиция одного заказа» — проверяема? ────
-- Дублей ключа строки быть не должно: 0 = фраза верна.
SELECT
    COUNT(*) - COUNT(DISTINCT (order_id || '#' || CAST(order_item_id AS VARCHAR))) AS duplicate_rows,
    COUNT(*)                                                                       AS total_rows
FROM fact_sales;

-- ── 3. Пустые ключи: битые строки ───────────────────────────────────────
SELECT
    SUM(CASE WHEN order_id       IS NULL THEN 1 ELSE 0 END) AS null_order_id,
    SUM(CASE WHEN order_item_id  IS NULL THEN 1 ELSE 0 END) AS null_order_item_id,
    SUM(CASE WHEN order_date_key IS NULL THEN 1 ELSE 0 END) AS null_date_key,
    SUM(CASE WHEN customer_id    IS NULL THEN 1 ELSE 0 END) AS null_customer_id,
    SUM(CASE WHEN seller_id      IS NULL THEN 1 ELSE 0 END) AS null_seller_id,
    SUM(CASE WHEN product_id     IS NULL THEN 1 ELSE 0 END) AS null_product_id,
    SUM(CASE WHEN category_name_pt IS NULL THEN 1 ELSE 0 END) AS null_category
FROM fact_sales;
-- Ожидание: все нули.

-- ── 4. «Сироты»: ключ факта есть не в справочнике ───────────────────────
SELECT
    (SELECT COUNT(*) FROM fact_sales f LEFT JOIN dim_customer c USING (customer_id)
      WHERE c.customer_id IS NULL)                                   AS orphan_customer,
    (SELECT COUNT(*) FROM fact_sales f LEFT JOIN dim_seller   s USING (seller_id)
      WHERE s.seller_id IS NULL)                                     AS orphan_seller,
    (SELECT COUNT(*) FROM fact_sales f LEFT JOIN dim_product  p USING (product_id)
      WHERE p.product_id IS NULL)                                    AS orphan_product,
    (SELECT COUNT(*) FROM fact_sales f LEFT JOIN dim_date     d ON d.date_key = f.order_date_key
      WHERE d.date_key IS NULL)                                      AS orphan_date;
-- Ожидание: все нули.

-- ── 5. Диапазоны: отрицательные деньги, невозможные даты ────────────────
SELECT
    MIN(amount)                   AS min_amount,
    MAX(amount)                   AS max_amount,
    MIN(qty)                      AS min_qty,
    MAX(qty)                      AS max_qty,
    SUM(CASE WHEN amount < 0 THEN 1 ELSE 0 END)          AS negative_amount_rows,
    SUM(CASE WHEN freight_value < 0 THEN 1 ELSE 0 END)  AS negative_freight_rows,
    SUM(CASE WHEN qty <> 1 THEN 1 ELSE 0 END)           AS bad_qty_rows,
    SUM(CASE WHEN is_delivered NOT IN (0,1) THEN 1 ELSE 0 END) AS bad_flag_rows,
    SUM(CASE WHEN delivery_days < 0 THEN 1 ELSE 0 END) AS negative_delivery_days,
    MIN(order_date_key)           AS min_date,
    MAX(order_date_key)           AS max_date
FROM fact_sales;
-- Ожидание: amount >= 0, отрицательных строк нет, qty = 1 везде,
-- даты в 2016..2018.

-- ── 6. Первое зафиксированное число главной меры ───────────────────────
-- Это то самое число, которое на З04 станет эталоном, а на З11 будет
-- сверено с дашбордом. Записано в паспорт.
SELECT
    SUM(amount)                                        AS gmv_delivered_brl,
    COUNT(*)                                            AS delivered_positions,
    COUNT(DISTINCT order_id)                            AS delivered_orders,
    SUM(qty)                                            AS delivered_units,
    SUM(freight_value)                                  AS freight_delivered_brl
FROM fact_sales
WHERE order_status = 'delivered';

-- ── 7. Сверка факта с сырьём: ничего не потерялось и не размножилось ────
SELECT
    (SELECT COUNT(*) FROM read_csv_auto('{RAW}/olist_order_items_dataset.csv'))      AS raw_positions,
    (SELECT COUNT(*) FROM fact_sales)                                               AS fact_positions,
    (SELECT COUNT(*) FROM read_csv_auto('{RAW}/olist_order_items_dataset.csv'))
      - (SELECT COUNT(*) FROM fact_sales)                                           AS diff;
-- Ожидание: raw = fact = 112 650, diff = 0.

-- ── 8. Покрытие: сколько товаров и покупателей не попали в факт ─────────
SELECT
    (SELECT COUNT(*) FROM dim_customer)                                        AS dim_customers,
    (SELECT COUNT(DISTINCT customer_id) FROM fact_sales)                      AS fact_customers,
    (SELECT COUNT(*) FROM dim_product)                                        AS dim_products,
    (SELECT COUNT(DISTINCT product_id) FROM fact_sales)                       AS fact_products;
-- Ожидание: fact_customers = 98 666 < dim_customers = 99 441.
-- Разница ровно 775 — это аккаунты, чьи заказы остались без позиций
-- (таких заказов в сырье 775). Они не попали в факт и в оборот — это корректно.
-- dim_product = fact_products = 32 951: каждый товар из справочника хоть раз
-- продался, «непроданных» товаров в справочнике не осталось.