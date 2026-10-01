-- Учебная БД курса OLAP (ClickHouse)
CREATE DATABASE IF NOT EXISTS retail_dw;

CREATE TABLE IF NOT EXISTS retail_dw.dim_store
(
    store_id UInt32,
    store_name String,
    city String,
    region String
)
ENGINE = MergeTree
ORDER BY store_id;

CREATE TABLE IF NOT EXISTS retail_dw.dim_product
(
    product_id UInt32,
    product_name String,
    category String,
    brand String
)
ENGINE = MergeTree
ORDER BY product_id;

CREATE TABLE IF NOT EXISTS retail_dw.dim_date
(
    date_id UInt32,
    full_date Date,
    year UInt16,
    month UInt8,
    month_name String,
    day_of_week UInt8
)
ENGINE = MergeTree
ORDER BY date_id;

-- Партиции по месяцу добавим на P07–P08; на старте — простой MergeTree
CREATE TABLE IF NOT EXISTS retail_dw.fact_sales
(
    sale_id UInt64,
    date_id UInt32,
    store_id UInt32,
    product_id UInt32,
    qty Int32,
    amount Decimal(12, 2)
)
ENGINE = MergeTree
ORDER BY (date_id, store_id, product_id);
