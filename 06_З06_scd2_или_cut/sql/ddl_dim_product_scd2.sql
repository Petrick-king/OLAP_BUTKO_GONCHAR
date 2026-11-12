-- =====================================================================
--  ddl_dim_product_scd2.sql  —  ИСТОРИЯ ВЕРСИЙ ТОВАРА (SCD Type 2)
--  Практика З06 «История справочника или отказ», Вариант A
--
--  Паспорт (§4) выбрал для dim_product режим SCD2: категория товара
--  меняется, и старые продажи обязаны остаться в старой категории.
--
--  Почему именно этот справочник: категория товара — единственный атрибут
--  в проекте, который РЕАЛЬНО меняется во времени и по которому строится
--  отчёт. Всё остальное (город продавца, штат покупателя, координаты)
--  меняется настолько редко, что историзировать его дороже, чем полезно.
--
--  ЧТО ПОЯВЛЯЕТСЯ ПО СРАВНЕНИЮ С dim_product:
--
--   product_sk   BIGINT       суррогатный ключ ВЕРСИИ.
--                               Он, а не product_id, теперь настоящий ключ:
--                               на один product_id приходится несколько строк.
--   version_no   INTEGER      номер версии, 1, 2, 3…
--   valid_from   TIMESTAMP    с какого момента версия действует, включительно.
--   valid_to     TIMESTAMP    по какой moment действует, НЕ включительно.
--   is_current   BOOLEAN      текущая ли это версия.
--
--   Атрибуты категории дублируются в КАЖДОЙ версии. Это не избыточность:
--   именно так история и хранится — версия 1 несёт свою категорию,
--   версия 2 свою. Иначе старая категория была бы затёрта.
--
--  ИНТЕРВАЛ ПОЛУОТКРЫТЫЙ: [valid_from, valid_to).
--  Так интервалы можно присоединять к факту без двусмысленности
--  и без +1 день в SQL.
-- =====================================================================

DROP TABLE IF EXISTS dim_product_scd2;

CREATE TABLE dim_product_scd2 (
    product_sk                 BIGINT       NOT NULL,   -- суррогатный ключ ВЕРСИИ
    product_id                 VARCHAR      NOT NULL,   -- натуральный ключ товара
    version_no                 INTEGER      NOT NULL,   -- 1, 2, 3…
    category_name_pt           VARCHAR      NOT NULL,
    category_name_en           VARCHAR      NOT NULL,
    is_category_translated     BOOLEAN      NOT NULL,

    valid_from                 TIMESTAMP    NOT NULL,   -- включительно
    valid_to                   TIMESTAMP    NOT NULL,   -- НЕ включительно
    is_current                 BOOLEAN      NOT NULL,

    product_name_lenght        INTEGER,
    product_description_lenght INTEGER,
    product_photos_qty         INTEGER,
    product_weight_g           INTEGER,
    product_length_cm          INTEGER,
    product_height_cm          INTEGER,
    product_width_cm           INTEGER,

    -- сколько позиций продано по этой версии (для контроля демонстрации)
    positions_in_version       BIGINT
);

-- ══════════════════════════════════════════════════════════════════════
--  ШАГ 1. БЭКФИЛЛ: каждая строка dim_product становится версией 1.
--  До изменения атрибута у каждого товара ровно одна версия.
--
--  ГРАНИЦЫ ИНТЕРВАЛА. Используются 1970-01-01 («версия существует всегда»)
--  и 2106-02-07 06:28:15 («бесконечно»).
--  Это не прихоть, а ограничение ClickHouse: тип DateTime хранит
--  32-битное число секунд и покрывает только 1970…2106. Привычные
--  1900-01-01 и 9999-12-31 в него просто не влезают, и на З07 та же
--  витрина не загрузилась бы. Раз границы одинаковые в обеих базах,
--  история одна и та же и сверяется без оговорок.
-- ══════════════════════════════════════════════════════════════════════
INSERT INTO dim_product_scd2
SELECT
    ROW_NUMBER() OVER (ORDER BY p.product_id)         AS product_sk,
    p.product_id,
    1                                               AS version_no,
    p.category_name_pt,
    p.category_name_en,
    p.is_category_translated,
    TIMESTAMP '1970-01-01 00:00:00'                  AS valid_from,
    TIMESTAMP '2106-02-07 06:28:15'                  AS valid_to,
    TRUE                                            AS is_current,
    p.product_name_lenght,
    p.product_description_lenght,
    p.product_photos_qty,
    p.product_weight_g,
    p.product_length_cm,
    p.product_height_cm,
    p.product_width_cm,
    NULL::BIGINT                                    AS positions_in_version
FROM dim_product p;

-- ══════════════════════════════════════════════════════════════════════
--  ШАГ 2. КОНСТРУКЦИЯ ДЛЯ ФАКТА ПОД ИСТОРИЮ.
--
--  Факт присоединяется к версии НА ДАТУ ПОКУПКИ:
--      order_purchase_timestamp ∈ [valid_from, valid_to)
--
--  Строка позиции «Наушники X» от 20.12.2017 уходит в версию 1
--  (informatica_acessorios), а от 15.03.2018 — в версию 2 (audio).
--  Одна и та же сущность, две строки справочника, ноль строк в факте.
--
--  Это и есть ответ на вопрос задания «как факт попадает в нужную
--  версию на дату события»: по дате события, полуоткрытым интервалом.
-- ══════════════════════════════════════════════════════════════════════
CREATE OR REPLACE VIEW fact_sales_scd2 AS
SELECT
    f.*,
    p.product_sk,
    p.version_no,
    p.category_name_pt                     AS category_at_sale_pt,
    p.category_name_en                     AS category_at_sale_en,
    p.valid_from,
    p.valid_to,
    p.is_current
FROM fact_sales f
JOIN dim_product_scd2 p
      ON  p.product_id = f.product_id
      AND f.order_purchase_timestamp >= p.valid_from
      AND f.order_purchase_timestamp <  p.valid_to;

-- Проверка: каждая строка факта должна попасть ровно в одну версию.
-- Строк во view столько же, сколько в fact_sales, — иначе часть факта
-- выпала бы из интервалов (типичная ошибка: забыли 9999-12-31 в valid_to).