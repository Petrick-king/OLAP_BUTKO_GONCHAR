-- =====================================================================
--  ddl_clickhouse.sql  —  ВИТРИНА OLIST В CLICKHOUSE
--  Практика З07 «Данные в ClickHouse»
--
--  Порядок: справочники → факт.
--
--  ── ЧТО ВАЖНО ПОНИМАТЬ ПРО ORDER BY ────────────────────────────────────
--  В ClickHouse НЕТ обычного первичного ключа, который запрещает повтор
--  строки. То, что здесь называют «ключом», — это ПОРЯДОК ХРАНЕНИЯ строк
--  на диске. Он даёт три вещи:
--    1) сжатие (соседние строки с похожими значениями жмутся вместе);
--    2) разреженный первичный индекс: можно пропускать целые блоки
--       данных, не читая их (data skipping);
--    3) быстрый доступ к «недавним» данным при запросе без фильтров.
--
--  ORDER BY — ЭТО НЕ УНИКАЛЬНЫЙ КЛЮЧ. Две одинаковые строки в ORDER BY
--  не запрещены и не схлопываются. Схлопывание даёт отдельный движок —
--  ReplacingMergeTree (см. dim_product_scd2), и даже он работает при
--  слиянии частей, а не в момент вставки.
--
--  Обоснование конкретного порядка: ../../07_З07_загрузка_clickhouse/notes/order_by.md
--
--  ДЕНЬГИ — Decimal. Float64 на сумме в 13 миллионов даст расхождение
--  с дашбордом уже на копейках.
-- =====================================================================

-- ═══════════════════════════ СПРАВОЧНИКИ ═══════════════════════════════

-- dim_geo: 1 строка = почтовый префикс. Справочник меняется редко,
-- но при повторной загрузке строки могут задваиться — поэтому
-- ReplacingMergeTree: повтор строки с тем же ключом схлопнётся при слиянии.
CREATE TABLE IF NOT EXISTS olist.dim_geo
(
    zip_code_prefix String              COMMENT 'почтовый префикс, ключ справочника',
    geo_lat         Nullable(Float64),
    geo_lng         Nullable(Float64),
    geo_city        Nullable(String),
    geo_state       Nullable(String),
    geo_points_cnt  UInt64              COMMENT 'сколько точек усреднили в префиксе'
)
ENGINE = ReplacingMergeTree
ORDER BY zip_code_prefix;

-- dim_category: справочник переводов. 74 строки — читается целиком,
-- поэтому порядок хранения не важен, но всё равно задаётся по ключу.
CREATE TABLE IF NOT EXISTS olist.dim_category
(
    category_name_pt String   COMMENT 'название как в сырье (pt), ключ',
    category_name_en Nullable(String) COMMENT 'перевод; NULL, если перевода нет',
    is_translated    UInt8
)
ENGINE = ReplacingMergeTree
ORDER BY category_name_pt;

-- dim_date: календарь. ORDER BY = сам ключ, справочник адресуется
-- точечно и по нему же делаются все запросы с фильтром по датам.
CREATE TABLE IF NOT EXISTS olist.dim_date
(
    date_key      Date   COMMENT 'ключ',
    year          UInt16,
    quarter       UInt8,
    month_num     UInt8,
    month_name_pt LowCardinality(String),
    year_month    LowCardinality(String) COMMENT 'YYYY-MM, для оси графика',
    is_weekend    UInt8
)
ENGINE = ReplacingMergeTree
ORDER BY date_key;

-- dim_customer: 99 441 строка, адресуется по customer_id.
CREATE TABLE IF NOT EXISTS olist.dim_customer
(
    customer_id              String   COMMENT 'ключ: АККАУНТ, не человек',
    customer_unique_id       String   COMMENT 'человек; аккаунтов может быть несколько',
    customer_zip_code_prefix Nullable(String),
    customer_city            Nullable(String),
    customer_state           Nullable(String),
    geo_lat                  Nullable(Float64),
    geo_lng                  Nullable(Float64)
)
ENGINE = ReplacingMergeTree
ORDER BY customer_id;

-- dim_seller: 3 095 строк.
CREATE TABLE IF NOT EXISTS olist.dim_seller
(
    seller_id              String   COMMENT 'ключ',
    seller_zip_code_prefix Nullable(String),
    seller_city            Nullable(String),
    seller_state           Nullable(String),
    geo_lat                Nullable(Float64),
    geo_lng                Nullable(Float64)
)
ENGINE = ReplacingMergeTree
ORDER BY seller_id;

-- dim_product: 32 951 строка.
CREATE TABLE IF NOT EXISTS olist.dim_product
(
    product_id                 String   COMMENT 'ключ',
    category_name_pt           String   COMMENT 'nao_categorizado, если в сырье был NULL',
    category_name_en           String,
    is_category_translated     UInt8,
    product_name_lenght        Nullable(Int32),
    product_description_lenght Nullable(Int32),
    product_photos_qty         Nullable(Int32),
    product_weight_g           Nullable(Int32),
    product_length_cm          Nullable(Int32),
    product_height_cm          Nullable(Int32),
    product_width_cm           Nullable(Int32)
)
ENGINE = ReplacingMergeTree
ORDER BY product_id;

-- dim_product_scd2: справочник с ИСТОРИЕЙ ВЕРСИЙ (З06).
--
-- Здесь ReplacingMergeTree выбран осознанно, и это единственное место
-- в проекте, где ORDER BY фактически работает как ключ дедупликации:
-- повторная загрузка той же версии не должна плодить строки.
--
-- Второй параметр — valid_to: при слиянии останется строка с БОЛЬШИМ
-- valid_to, то есть более поздняя версия. Именно это нам нужно.
--
-- ВАЖНО: схлопывание происходит при СЛИЯНИИ частей (асинхронно),
-- а не в момент INSERT. Поэтому повторный тест сразу после загрузки
-- может показать дубли — их нужно либо подождать, либо выполнить
-- OPTIMIZE TABLE ... FINAL, либо читать с FINAL. Это НЕ уникальный
-- ключ в смысле Postgres.
CREATE TABLE IF NOT EXISTS olist.dim_product_scd2
(
    product_sk              UInt64    COMMENT 'суррогатный ключ ВЕРСИИ',
    product_id              String    COMMENT 'натуральный ключ товара',
    version_no              UInt16    COMMENT '1, 2, 3...',
    category_name_pt        String,
    category_name_en        String,
    is_category_translated  UInt8,
    valid_from              DateTime('UTC')  COMMENT 'включительно',
    valid_to                DateTime('UTC')  COMMENT 'НЕ включительно; версия параметра дедупликации',
    is_current              UInt8,
    product_name_lenght        Nullable(Int32),
    product_description_lenght Nullable(Int32),
    product_photos_qty         Nullable(Int32),
    product_weight_g           Nullable(Int32),
    product_length_cm          Nullable(Int32),
    product_height_cm          Nullable(Int32),
    product_width_cm           Nullable(Int32),
    positions_in_version    Nullable(UInt64)
)
ENGINE = ReplacingMergeTree(valid_to)
-- Ключ дедупликации = товар + момент начала версии.
-- Одна и та же версия, загруженная дважды, схлопнется;
-- РАЗНЫЕ версии (valid_from 1900 и 2018) останутся обе — так и надо.
ORDER BY (product_id, valid_from);

-- ═══════════════════════════════ ФАКТ ════════════════════════════════════

-- fact_sales: ОДНА СТРОКА = ОДНА ПОЗИЦИЯ ОДНОГО ЗАКАЗА.
--
-- Мерные колонки повторяют паспорт и образец звезды из раздаточных
-- материалов (`раздаточные материалы/star_retail.md`):
--   amount, qty, freight_value, is_delivered, is_late, delivery_days
--       — можно SUM;
--   payment_value_order — НЕЛЬЗЯ SUM напрямую (платёж на уровне заказа).
--
-- Имена колонок совпадают с DuckDB, поэтому эталонный SQL главной
-- метрики из З04 выполняется в обеих базах без правок.
--
-- PARTITION BY — это ДРУГОЕ, не ключ и не уникальность. Разбиение на
-- месяцы нужно не для скорости, а чтобы можно было дешево удалять
-- старые данные (TTL) и не читать лишние части при запросе.
-- При 112 650 строках это не ускорение, а задел на рост.
--
-- ORDER BY разобран в notes/order_by.md.
CREATE TABLE IF NOT EXISTS olist.fact_sales
(
    order_item_sk               UInt64    COMMENT 'суррогатный ключ строки',
    order_id                    String    COMMENT 'натуральный ключ заказа',
    order_item_id               UInt32    COMMENT 'номер позиции в заказе',

    -- ключи измерений
    order_date_key              Date      COMMENT 'дата покупки, главный разрез',
    customer_id                 String,
    seller_id                   String,
    product_id                  String,
    category_name_pt            LowCardinality(String),

    -- контекст события
    order_status                LowCardinality(String),
    order_purchase_timestamp    DateTime('UTC'),
    order_delivered_carrier_date    Nullable(DateTime('UTC')),
    order_delivered_customer_date   Nullable(DateTime('UTC')),
    order_estimated_delivery_date   Nullable(DateTime('UTC')),
    shipping_limit_date             Nullable(DateTime('UTC')),

    -- ── меры ──
    amount                   Decimal(12, 2) COMMENT 'сумма позиции, BRL (в сырье price)',
    qty                      UInt32    COMMENT 'число штук; всегда 1 по зерну строки',
    freight_value            Decimal(12, 2) COMMENT 'доставка по позиции, BRL',
    payment_value_order      Nullable(Decimal(12, 2)) COMMENT 'НЕ суммировать напрямую',
    is_delivered             UInt8,
    delivery_days            Nullable(Int32),
    is_late                  UInt8,

    -- атрибуты
    payment_type                Nullable(String),
    review_score                Nullable(Int8),

    -- версия товара на дату покупки (SCD2, З06).
    -- В DuckDB это сделано представлением с джойном по интервалу;
    -- здесь версия кладётся в факт при загрузке — ClickHouse плохо
    -- индексирует неравенства, и лишний джойн на каждый отчёт дорог.
    product_sk                  Nullable(UInt64),
    version_no                  Nullable(UInt16),
    category_at_sale_pt         Nullable(String)
)
ENGINE = MergeTree
PARTITION BY toYYYYMM(order_date_key)
ORDER BY (order_date_key, order_status, seller_id, category_name_pt,
          customer_id, product_id, order_id, order_item_id);