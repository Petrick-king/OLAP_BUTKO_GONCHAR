-- =====================================================================
--  quality_checks.sql  —  ПРОВЕРКИ КАЧЕСТВА (З05, п.2)
--
--  Каждая проверка возвращает ОДНУ СТРОКУ одинаковой формы:
--      check_id | check_name        | severity | expected | actual | passed
--
--  severity = `block`  → при passed = false загрузка ОСТАНАВЛИВАЕТСЯ,
--                         рабочие таблицы не трогаются (ROLLBACK).
--  severity = `warn`   → пишется в лог, загрузка идёт дальше
--                         (так помечены находки, которые в этом датасете
--                          заложены самим источником — их нельзя «починить»
--                          загрузкой, но и пропускать молча нельзя).
--
--  Скрипт etl/run_etl.py читает passed/sseverity и решает,
--  продолжать или откатывать.
-- =====================================================================

WITH r AS (

-- ═══════════════ 1. ПУСТЫЕ СУММЫ И КЛЮЧИ ═══════════════════════════════
-- Обязательные поля не должны быть NULL. NULL в ключе = «сирота»,
-- NULL в мере = «оборот посчитан не полностью».

SELECT 'QC-01' AS check_id,
       'Ключи факта не NULL' AS check_name,
       'block' AS severity,
       0 AS expected,
       (SELECT SUM(CASE WHEN order_id       IS NULL THEN 1 ELSE 0 END
                     + CASE WHEN order_item_id  IS NULL THEN 1 ELSE 0 END
                     + CASE WHEN order_date_key IS NULL THEN 1 ELSE 0 END
                     + CASE WHEN customer_id    IS NULL THEN 1 ELSE 0 END
                     + CASE WHEN seller_id      IS NULL THEN 1 ELSE 0 END
                     + CASE WHEN product_id     IS NULL THEN 1 ELSE 0 END
                     + CASE WHEN category_name_pt IS NULL THEN 1 ELSE 0 END)
        FROM stg_fact_sales) AS actual,
       (SELECT COUNT(*) = 0 FROM stg_fact_sales
         WHERE order_id IS NULL OR order_item_id IS NULL OR order_date_key IS NULL
            OR customer_id IS NULL OR seller_id IS NULL OR product_id IS NULL
            OR category_name_pt IS NULL) AS passed

UNION ALL
-- Меры не NULL: amount и freight обязательны, payment может быть NULL
-- (заказ без платежа — легальное состояние, это warn).
SELECT 'QC-02',
       'Меры amount/freight не NULL',
       'block',
       0,
       (SELECT SUM(CASE WHEN amount IS NULL THEN 1 ELSE 0 END
                     + CASE WHEN freight_value IS NULL THEN 1 ELSE 0 END)
        FROM stg_fact_sales),
       (SELECT COUNT(*) = 0 FROM stg_fact_sales
         WHERE amount IS NULL OR freight_value IS NULL)

-- ═══════════════ 2. СТРАННЫЙ ДИАЗАПОН ══════════════════════════════════
-- Количество ≥ 0, цена не отрицательная и не ноль, даты не из 1900 года.

UNION ALL
SELECT 'QC-03',
       'amount > 0, freight_value >= 0',
       'block',
       0,
       (SELECT COUNT(*) FROM stg_fact_sales WHERE amount <= 0 OR freight_value < 0),
       (SELECT COUNT(*) = 0 FROM stg_fact_sales WHERE amount <= 0 OR freight_value < 0)

UNION ALL
-- qty по построению равен 1: одна позиция заказа = одна единица товара.
-- Если в сырье появится quantity, эта проверка начнёт падать — и это
-- правильно: придётся пересмотреть зерно строки и меры amount/qty.
SELECT 'QC-19',
       'qty = 1 в каждой позиции (зерно строки = одна единица)',
       'block',
       0,
       (SELECT COUNT(*) FROM stg_fact_sales WHERE qty <> 1),
       (SELECT COUNT(*) = 0 FROM stg_fact_sales WHERE qty <> 1)

UNION ALL
SELECT 'QC-04',
       'delivery_days >= 0 там, где доставка была',
       'block',
       0,
       (SELECT COUNT(*) FROM stg_fact_sales WHERE delivery_days < 0),
       (SELECT COUNT(*) = 0 FROM stg_fact_sales WHERE delivery_days < 0)

UNION ALL
-- 1900 год — классический признак «дату привели неявно» или «источник
-- отдаёт epoch». Диапазон задан шире данных намеренно.
SELECT 'QC-05',
       'Даты в разумном диапазоне 2015..2020 (не 1900, не epoch)',
       'block',
       0,
       (SELECT COUNT(*) FROM stg_fact_sales
         WHERE order_date_key < DATE '2015-01-01'
            OR order_date_key > DATE '2020-12-31'),
       (SELECT COUNT(*) = 0 FROM stg_fact_sales
         WHERE order_date_key < DATE '2015-01-01'
            OR order_date_key > DATE '2020-12-31')

UNION ALL
SELECT 'QC-06',
       'Флаги только 0 или 1',
       'block',
       0,
       (SELECT COUNT(*) FROM stg_fact_sales
         WHERE is_delivered NOT IN (0,1) OR is_late NOT IN (0,1)),
       (SELECT COUNT(*) = 0 FROM stg_fact_sales
         WHERE is_delivered NOT IN (0,1) OR is_late NOT IN (0,1))

-- ═══════════════ 3. ДУБЛИ ════════════════════════════════════════════
-- Нет двух строк с одним и тем же смыслом «одна строка = …».
-- Для факта это пара (order_id, order_item_id) из паспорта.
-- Для справочников — собственный ключ.

UNION ALL
SELECT 'QC-07',
       'Нет дублей ключа строки факта (order_id, order_item_id)',
       'block',
       0,
       (SELECT COALESCE(SUM(c - 1), 0) FROM (
            SELECT COUNT(*) AS c FROM stg_fact_sales
            GROUP BY order_id, order_item_id HAVING COUNT(*) > 1)),
       ((SELECT COUNT(*) FROM stg_fact_sales)
        =
        (SELECT COUNT(DISTINCT (order_id || '#' || CAST(order_item_id AS VARCHAR)))
           FROM stg_fact_sales))

UNION ALL
SELECT 'QC-08',
       'Ключи справочников уникальны',
       'block',
       0,
       (SELECT (SELECT COUNT(*) FROM (SELECT zip_code_prefix FROM stg_dim_geo
                                       GROUP BY 1 HAVING COUNT(*) > 1))
            + (SELECT COUNT(*) FROM (SELECT category_name_pt FROM stg_dim_category
                                     GROUP BY 1 HAVING COUNT(*) > 1))
            + (SELECT COUNT(*) FROM (SELECT date_key FROM stg_dim_date
                                     GROUP BY 1 HAVING COUNT(*) > 1))
            + (SELECT COUNT(*) FROM (SELECT customer_id FROM stg_dim_customer
                                     GROUP BY 1 HAVING COUNT(*) > 1))
            + (SELECT COUNT(*) FROM (SELECT seller_id FROM stg_dim_seller
                                     GROUP BY 1 HAVING COUNT(*) > 1))
            + (SELECT COUNT(*) FROM (SELECT product_id FROM stg_dim_product
                                     GROUP BY 1 HAVING COUNT(*) > 1))),
       ((SELECT COUNT(*) FROM (SELECT zip_code_prefix FROM stg_dim_geo
                               GROUP BY 1 HAVING COUNT(*) > 1))
      + (SELECT COUNT(*) FROM (SELECT category_name_pt FROM stg_dim_category
                               GROUP BY 1 HAVING COUNT(*) > 1))
      + (SELECT COUNT(*) FROM (SELECT date_key FROM stg_dim_date
                               GROUP BY 1 HAVING COUNT(*) > 1))
      + (SELECT COUNT(*) FROM (SELECT customer_id FROM stg_dim_customer
                               GROUP BY 1 HAVING COUNT(*) > 1))
      + (SELECT COUNT(*) FROM (SELECT seller_id FROM stg_dim_seller
                               GROUP BY 1 HAVING COUNT(*) > 1))
      + (SELECT COUNT(*) FROM (SELECT product_id FROM stg_dim_product
                               GROUP BY 1 HAVING COUNT(*) > 1))) = 0

-- ═══════════════ 4. «СИРОТЫ» ══════════════════════════════════════════
-- В факте есть ключ, которого нет в справочнике. Такой факт нельзя
-- показать ни по одному разрезу — он молча провалится в JOIN.

UNION ALL
SELECT 'QC-09',
       'Нет сирот: customer_id найден в dim_customer',
       'block',
       0,
       (SELECT COUNT(*) FROM stg_fact_sales f
         LEFT JOIN stg_dim_customer c USING (customer_id)
        WHERE c.customer_id IS NULL),
       (SELECT COUNT(*) = 0 FROM stg_fact_sales f
         LEFT JOIN stg_dim_customer c USING (customer_id)
        WHERE c.customer_id IS NULL)

UNION ALL
SELECT 'QC-10',
       'Нет сирот: seller_id найден в dim_seller',
       'block',
       0,
       (SELECT COUNT(*) FROM stg_fact_sales f
         LEFT JOIN stg_dim_seller s USING (seller_id)
        WHERE s.seller_id IS NULL),
       (SELECT COUNT(*) = 0 FROM stg_fact_sales f
         LEFT JOIN stg_dim_seller s USING (seller_id)
        WHERE s.seller_id IS NULL)

UNION ALL
SELECT 'QC-11',
       'Нет сирот: product_id найден в dim_product',
       'block',
       0,
       (SELECT COUNT(*) FROM stg_fact_sales f
         LEFT JOIN stg_dim_product p USING (product_id)
        WHERE p.product_id IS NULL),
       (SELECT COUNT(*) = 0 FROM stg_fact_sales f
         LEFT JOIN stg_dim_product p USING (product_id)
        WHERE p.product_id IS NULL)

UNION ALL
SELECT 'QC-12',
       'Нет сирот: order_date_key найден в dim_date',
       'block',
       0,
       (SELECT COUNT(*) FROM stg_fact_sales f
         LEFT JOIN stg_dim_date d ON d.date_key = f.order_date_key
        WHERE d.date_key IS NULL),
       (SELECT COUNT(*) = 0 FROM stg_fact_sales f
         LEFT JOIN stg_dim_date d ON d.date_key = f.order_date_key
        WHERE d.date_key IS NULL)

UNION ALL
SELECT 'QC-13',
       'Нет сирот: category_name_pt найден в dim_category',
       'block',
       0,
       (SELECT COUNT(*) FROM stg_fact_sales f
         LEFT JOIN stg_dim_category c ON c.category_name_pt = f.category_name_pt
        WHERE c.category_name_pt IS NULL),
       (SELECT COUNT(*) = 0 FROM stg_fact_sales f
         LEFT JOIN stg_dim_category c ON c.category_name_pt = f.category_name_pt
        WHERE c.category_name_pt IS NULL)

-- ═══════════════ 5. ЧИСЛО СТРОК ИЗ СЫРЬЯ ══════════════════════════════
-- Согласовано ли с файлом. Если факт «раздулся» или «съехал» —
-- где-то ошибка джойна.

UNION ALL
SELECT 'QC-14',
       'Число позиций факта равно числу строк в сырье',
       'block',
       (SELECT COUNT(*) FROM raw_items),
       (SELECT COUNT(*) FROM stg_fact_sales),
       (SELECT COUNT(*) FROM stg_fact_sales) = (SELECT COUNT(*) FROM raw_items)

-- ═══════════════ 6. ПРЕДУПРЕЖДЕНИЯ (НЕ ОСТАНАВЛИВАЮТ ЗАГРУЗКУ) ════════
-- Эти находки заложены самим датасетом Olist. Загрузка их не может
-- исправить — она обязана их показать, а не спрятать.

UNION ALL
-- 775 заказов в сырье вообще не имеют позиций. Они не попадают в факт
-- и в оборот не входят — это корректно, но цифра «заказов» и «позиций»
-- всегда будет различаться, и об этом надо знать заранее.
SELECT 'QC-15',
       'Заказы без позиций (ожидается 775, они не попадут в факт)',
       'warn',
       775,
       (SELECT 99441 - COUNT(DISTINCT order_id) FROM stg_fact_sales),
       TRUE

UNION ALL
-- 610 товаров в сырье без категории. Мы НЕ роняем загрузку:
-- заменяем на явную категорию `nao_categorizado` в stg_dim_product.
-- Но если их вдруг станет не 610 — источник изменился, и это стоит увидеть.
SELECT 'QC-16',
       'Товаров с категорией-заглушкой nao_categorizado',
       'warn',
       610,
       (SELECT COUNT(*) FROM stg_dim_product WHERE category_name_pt = 'nao_categorizado'),
       TRUE

UNION ALL
-- 13 товаров из 2 категорий, которых нет в словаре переводов.
-- Заглушку nao_categorizado (610 товаров) здесь НЕ считаем: у неё нет
-- перевода по определению, это не находка, а конструкция модели.
SELECT 'QC-17',
       'Товаров с реально непереведённой категорией (без заглушки)',
       'warn',
       13,
       (SELECT COUNT(*) FROM stg_dim_product
         WHERE NOT is_category_translated AND category_name_pt <> 'nao_categorizado'),
       TRUE

UNION ALL
-- Платёж — сущность уровня заказа, а строка факта — уровня ПОЗИЦИИ.
-- Если бы мы не схлопнули платежи до order_id, сумма оплат в факте
-- раздулась бы в разы. Сверяем две суммы по заказам, которые попали в факт:
--   expected — сумма платежей из сырья по этим же заказам;
--   actual   — сумма order_pay в обёртке stg_fact_orders.
-- Разница должна быть ровно нулём.
--
-- ВНИМАНИЕ: сравниваются только заказы, ЕСТЬ В ФАКТЕ. 775 заказов без
-- позиций в факт не попадают (см. QC-15), и их оплаты — 162 591.95 BRL —
-- в сумму факта не входят и не должны.
SELECT 'QC-18',
       'Разница сумм оплат по заказам факта (факт против сырья) равна нулю',
       'block',
       0,
       (SELECT CAST(ABS(
                   (SELECT SUM(CAST(p.payment_value AS DECIMAL(12,2)))
                      FROM raw_payments p
                      JOIN stg_fact_orders o ON o.order_id = p.order_id)
                 - (SELECT SUM(order_pay) FROM stg_fact_orders)
                ) AS DECIMAL(14,2))),
       (SELECT ABS(
                   (SELECT SUM(CAST(p.payment_value AS DECIMAL(12,2)))
                      FROM raw_payments p
                      JOIN stg_fact_orders o ON o.order_id = p.order_id)
                 - (SELECT SUM(order_pay) FROM stg_fact_orders)
                ) = 0)

)

SELECT
    check_id,
    check_name,
    severity,
    expected,
    actual,
    passed,
    CASE WHEN passed THEN 'OK'
         WHEN severity = 'warn' THEN 'WARN (не блокирует)'
         ELSE 'FAIL — ЗАГРУЗКА ОСТАНОВЛЕНА' END AS verdict
FROM r
ORDER BY check_id;