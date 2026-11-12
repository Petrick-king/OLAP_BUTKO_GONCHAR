-- ══════════════════════════════════════════════════════════════════════
-- Д1. ДВЕ ВЕРСИИ ОДНОЙ СУЩНОСТИ ВИДНЫ ЗАПРОСОМ
--
-- Верхние товары продавались и до, и после реорганизации каталога,
-- поэтому у них две строки в справочнике: закрытая версия 1
-- и текущая версия 2. Именно это и означает «историю версий».
-- ══════════════════════════════════════════════════════════════════════

SELECT
    product_sk,
    product_id,
    version_no,
    category_name_pt,
    category_name_en,
    valid_from,
    valid_to,
    is_current,
    positions_in_version
FROM dim_product_scd2
WHERE product_id IN (
        SELECT product_id FROM dim_product_scd2
        GROUP BY 1 HAVING COUNT(*) > 1
        ORDER BY 1
        LIMIT 3
     )
ORDER BY product_id, version_no;


-- ══════════════════════════════════════════════════════════════════════
-- Д2. ФАКТ ПОПАДАЕТ В НУЖНУЮ ВЕРСИЮ НА ДАТУ СОБЫТИЯ
--
-- Одна и та же позиция товара в разные годы уходит в разные версии:
--   category_at_sale_pt — категория, действовавшая НА ДАТУ ПОКУПКИ;
--   category_name_pt    — категория в СПРАВОЧНИКЕ СЕЙЧАС (для сравнения).
-- Видно переход товаров с informatica_acessorios на audio ровно с 2018 года.
-- ══════════════════════════════════════════════════════════════════════

SELECT
    EXTRACT(YEAR FROM s.order_date_key)          AS year,
    s.category_at_sale_pt                       AS category_at_sale,
    s.version_no,
    COUNT(*)                                    AS positions,
    COUNT(DISTINCT s.order_id)                  AS orders,
    SUM(s.amount)                                AS gmv_brl
FROM fact_sales_scd2 s
WHERE s.order_status = 'delivered'
  AND s.product_id IN (
        SELECT product_id FROM dim_product_scd2
        GROUP BY 1 HAVING COUNT(*) > 1
     )
GROUP BY 1, 2, 3
ORDER BY 1, 2, 3;


-- ══════════════════════════════════════════════════════════════════════
-- Д3. ЦЕНА ОШИБКИ: ЧТО БЫЛО БЫ, ЕСЛИ БЫ СПРАВОЧНИК ЗАТИРАЛСЯ (SCD1)
--
-- Оба столбца — один и тот же отчёт «оборот по категориям за 2017 год»,
-- один и тот же факт, разница только в справочнике:
--   gmv_scd1 — по dim_product_scd1, где категория ЗАТЁРТА (SCD1);
--   gmv_scd2 — по dim_product_scd2, где категория берётся НА ДАТУ ПОКУПКИ.
-- scd1_error_brl — сколько денег SCD1 переписал бы задним числом.
-- ══════════════════════════════════════════════════════════════════════

WITH scd1 AS (
    SELECT p.category_name_pt AS category, SUM(f.amount) AS gmv_2017
    FROM fact_sales f
    JOIN dim_product_scd1 p ON p.product_id = f.product_id
    WHERE f.order_status = 'delivered'
      AND f.order_date_key >= DATE '2017-01-01'
      AND f.order_date_key <  DATE '2018-01-01'
      AND p.category_name_pt IN ('informatica_acessorios', 'audio')
    GROUP BY 1
),
scd2 AS (
    SELECT s.category_at_sale_pt AS category, SUM(s.amount) AS gmv_2017
    FROM fact_sales_scd2 s
    WHERE s.order_status = 'delivered'
      AND s.order_date_key >= DATE '2017-01-01'
      AND s.order_date_key <  DATE '2018-01-01'
      AND s.category_at_sale_pt IN ('informatica_acessorios', 'audio')
    GROUP BY 1
)
SELECT
    COALESCE(scd1.category, scd2.category)              AS category,
    COALESCE(scd1.gmv_2017, 0)                         AS gmv_scd1,
    COALESCE(scd2.gmv_2017, 0)                         AS gmv_scd2,
    COALESCE(scd1.gmv_2017, 0) - COALESCE(scd2.gmv_2017, 0)
                                                         AS scd1_error_brl,
    ROUND(100 * (COALESCE(scd1.gmv_2017, 0) - COALESCE(scd2.gmv_2017, 0))
               / COALESCE(scd2.gmv_2017, 0), 2)         AS scd1_error_percent
FROM scd1
FULL OUTER JOIN scd2 ON scd2.category = scd1.category
ORDER BY category;


-- ══════════════════════════════════════════════════════════════════════
-- Д4. ЦЕЛОСТНОСТЬ ИНТЕРВАЛОВ (САМОПРОВЕРКА SCD2)
--
-- Проверка, которую обязательно надо написать, иначе история
-- «вроде есть», а часть факта в неё не попадает.
--   lost_positions — строки факта, выпавшие из интервалов (должно быть 0);
--   double_mapped  — строки факта, попавшие в ДВЕ версии (должно быть 0).
-- ══════════════════════════════════════════════════════════════════════

SELECT
    (SELECT COUNT(*) FROM fact_sales)                                  AS fact_positions,
    (SELECT COUNT(*) FROM fact_sales_scd2)                             AS joined_positions,
    (SELECT COUNT(*) FROM fact_sales) - (SELECT COUNT(*) FROM fact_sales_scd2)
                                                                     AS lost_positions,
    (SELECT COUNT(*) FROM (
        SELECT f.order_id, f.order_item_id
        FROM fact_sales f
        JOIN dim_product_scd2 p
          ON  p.product_id = f.product_id
          AND f.order_purchase_timestamp >= p.valid_from
          AND f.order_purchase_timestamp <  p.valid_to
        GROUP BY 1, 2 HAVING COUNT(*) > 1
     ))                                                                AS double_mapped;


-- ══════════════════════════════════════════════════════════════════════
-- Д5. ОТЧЁТ ПО КАТЕГОРИЯМ ЗА 2016-2018 ПОД SCD2 — ЧТО ПОЛУЧАЕТСЯ
--
-- Тот же вопрос, что и в З04 §2, но уже на справочнике с историей.
-- Перенесённые товары меняют категорию ровно с 2018 года,
-- а 2017 год остаётся прежним.
-- ══════════════════════════════════════════════════════════════════════

SELECT
    EXTRACT(YEAR FROM s.order_date_key) AS year,
    s.category_at_sale_pt              AS category,
    COUNT(*)                           AS positions,
    SUM(s.amount)                       AS gmv_brl
FROM fact_sales_scd2 s
WHERE s.order_status = 'delivered'
  AND s.category_at_sale_pt IN ('informatica_acessorios', 'audio')
GROUP BY 1, 2
ORDER BY 2, 1;