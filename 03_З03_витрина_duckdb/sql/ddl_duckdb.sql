-- =====================================================================
--  ddl_duckdb.sql  —  схема витрины Olist в DuckDB
--  Практика З03 «Таблицы в DuckDB»
--
--  Здесь ТОЛЬКО описание таблиц: типы, ключи, комментарии.
--  Данные заливает load_duckdb.sql.
--
--  Порядок: сначала справочники, потом факт — иначе в факте окажутся
--  ссылки на продавцов и товары, которых ещё нет.
--
--  Модель описана в ../../02_З02_паспорт_star/passport.md
--
--  Повторный запуск безопасен: DROP ... IF EXISTS + CREATE.
-- =====================================================================

-- ─────────────────────────────── СПРАВОЧНИКИ ───────────────────────────

-- dim_geo — география. 1 строка = 1 почтовый префикс.
-- В сырье префикс встречается многократно (таких 17 972), поэтому витрина
-- схлопывает его агрегатом. Иначе ЛЕВЫЙ ДЖОЙН по префиксу размножит строки.
CREATE OR REPLACE TABLE dim_geo (
    zip_code_prefix VARCHAR     NOT NULL,   -- ключ
    geo_lat         DOUBLE,
    geo_lng         DOUBLE,
    geo_city        VARCHAR,
    geo_state       VARCHAR,
    geo_points_cnt  BIGINT                  -- сколько точек усреднили
);

-- dim_category — 1 строка = 1 категория товара.
-- 71 перевод + 2 категории, встречающиеся в товарах, но отсутствующие
-- в словаре: для них category_name_en IS NULL и is_translated = false.
CREATE OR REPLACE TABLE dim_category (
    category_name_pt VARCHAR NOT NULL,       -- ключ, вид как в сырье (pt)
    category_name_en VARCHAR,               -- перевод, может быть NULL
    is_translated    BOOLEAN NOT NULL
);

-- dim_date — календарь. 1 строка = 1 день.
-- В сырье календаря нет, генерируем диапазоном дат покупок.
-- Месяц назван по-португальски (pt-BR) — это локаль отгрузки, не России.
CREATE OR REPLACE TABLE dim_date (
    date_key      DATE     NOT NULL,        -- ключ, = CAST(order_purchase_timestamp AS DATE)
    year          SMALLINT,
    quarter       TINYINT,
    month_num     TINYINT,
    month_name_pt VARCHAR,
    year_month    VARCHAR,                  -- 'YYYY-MM', удобно для сортировки и оси графика
    is_weekend    BOOLEAN
);

-- dim_customer — 1 строка = ОДИН АККАУНТ (customer_id), не человек.
-- В сырье 99 441 аккаунт на 96 096 уникальных customer_unique_id:
-- один и тот же человек заводил несколько аккаунтов.
CREATE OR REPLACE TABLE dim_customer (
    customer_id             VARCHAR NOT NULL,   -- ключ
    customer_unique_id      VARCHAR,            -- человек; аккаунтов может быть несколько
    customer_zip_code_prefix VARCHAR,
    customer_city           VARCHAR,
    customer_state          VARCHAR,
    geo_lat                 DOUBLE,             -- из dim_geo по префиксу
    geo_lng                 DOUBLE
);

-- dim_seller — 1 строка = 1 продавец.
CREATE OR REPLACE TABLE dim_seller (
    seller_id            VARCHAR NOT NULL,      -- ключ
    seller_zip_code_prefix VARCHAR,
    seller_city          VARCHAR,
    seller_state         VARCHAR,
    geo_lat              DOUBLE,
    geo_lng              DOUBLE
);

-- dim_product — 1 строка = 1 товар.
-- Историю версий категории (SCD2: valid_from / valid_to / is_current)
-- добавляем на З06 — здесь ещё плоская таблица.
CREATE OR REPLACE TABLE dim_product (
    product_id                VARCHAR NOT NULL,  -- ключ
    category_name_pt          VARCHAR NOT NULL,  -- 'nao_categorizado', если в сырье NULL (610 товаров)
    category_name_en          VARCHAR NOT NULL,  -- откат на PT-название, если перевода нет (13 товаров)
    is_category_translated    BOOLEAN NOT NULL,
    product_name_lenght       INTEGER,
    product_description_lenght INTEGER,
    product_photos_qty        INTEGER,
    product_weight_g          INTEGER,
    product_length_cm         INTEGER,
    product_height_cm         INTEGER,
    product_width_cm          INTEGER
);

-- ───────────────────────────────── ФАКТ ────────────────────────────────

-- fact_sales — ОДНА СТРОКА = ОДНА ПОЗИЦИЯ ОДНОГО ЗАКАЗА.
-- Уникальность строки задаёт пара (order_id, order_item_id).
--
-- ДЕНЬГИ — DECIMAL(12,2), а не DOUBLE/FLOAT.
-- Иначе на сверке SQL ↔ дашборд (З11) разъедется копейка.
--
-- МЕРЫ и что с ними можно делать:
--   amount               SUM   ✅ аддитивная (сумма позиции)
--   qty                  SUM   ✅ аддитивная (число штук)
--   freight_value        SUM   ✅ аддитивная (доставка по позиции)
--   is_delivered         SUM   ✅ = число доставленных позиций
--   is_late              SUM   ✅ = число опоздавших доставок
--   delivery_days        SUM/AVG ✅ NULL там, где заказ не доставлен
--   payment_value_order  SUM   ⛔ ЗАПРЕЩЕНО напрямую: платёж один на ЗАКАЗ,
--                                  а строка — на ПОЗИЦИЮ. Сначала GROUP BY order_id.
--   review_score         AVG  ⚠️ не SUM: оценка, а не количество
--
-- НАЗВАНИЯ МЕР ПРИВЕДЕНЫ К ОБРАЗЦУ ЗВЕЗДЫ ИЗ РАЗДАТКИ
-- (`раздаточные материалы/star_retail.md`: FACT_SALES(sale_id, date_id,
-- store_id, product_id, qty, amount); amount — sum ok, qty — sum ok,
-- unit_price — sum НЕльзя). Соответствие:
--   sale_id  → order_item_sk   (суррогатный ключ строки факта)
--   date_id  → order_date_key  (у нас ключ измерения — сам DATE, суррогат
--                                  не нужен: dim_date всего 774 строки)
--   store_id → seller_id       (в маркетплейсе «магазин» = продавец;
--                                  об этом прямо сказано в паспорте)
--   amount   → amount          (в сырье поле называется price)
--   qty      → qty             (в Olist количества нет, см. ниже)
--
-- ПРО `qty`. В Olist количество товара в позиции НЕ хранится: одна позиция
-- = одна единица. Колонка введена явно, потому что:
--   а) зерно строки становится видно в данных, а не только в комментарии;
--   б) на вопрос «сколько штук продали» отвечает SUM(qty), а не COUNT(*);
--   в) если в источнике появится quantity, правильная модель — это
--      amount = qty * unit_price, где amount аддитивна, а unit_price
--      (цена за штуку) аддитивной НЕ является. Разводить их заранее
--      дешевле, чем переделывать витрину.
-- Сейчас qty = 1 во всех строках, поэтому amount одновременно является
-- и ценой за штуку, и суммой позиции.
CREATE OR REPLACE TABLE fact_sales (
    order_item_sk              BIGINT       NOT NULL,  -- суррогатный ключ строки (аналог sale_id)

    -- ключ строки (натуральный составной)
    order_id                   VARCHAR      NOT NULL,
    order_item_id              INTEGER      NOT NULL,

    -- ключи измерений
    order_date_key             DATE         NOT NULL,  -- → dim_date
    customer_id                VARCHAR      NOT NULL,  -- → dim_customer
    seller_id                  VARCHAR      NOT NULL,  -- → dim_seller
    product_id                 VARCHAR      NOT NULL,  -- → dim_product
    category_name_pt           VARCHAR,                 -- денормализовано: срез по категориям без джойна

    -- контекст события
    order_status                     VARCHAR,
    order_purchase_timestamp         TIMESTAMP,
    order_delivered_carrier_date     TIMESTAMP,
    order_delivered_customer_date    TIMESTAMP,
    order_estimated_delivery_date    TIMESTAMP,
    shipping_limit_date              TIMESTAMP,

    -- ── меры ──
    amount               DECIMAL(12,2) NOT NULL,   -- сумма позиции (в сырье: price)
    qty                  INTEGER       NOT NULL,   -- число штук; всегда 1 по зерну строки
    freight_value        DECIMAL(12,2) NOT NULL,
    payment_value_order  DECIMAL(12,2),            -- ⚠️ не суммировать напрямую
    is_delivered         INTEGER       NOT NULL,   -- 1 / 0
    delivery_days        INTEGER,                  -- NULL, если доставки не было
    is_late              INTEGER       NOT NULL,   -- 1 / 0

    -- атрибуты
    payment_type          VARCHAR,
    review_score          TINYINT                   -- 1..5, NULL, если отзыва нет
);