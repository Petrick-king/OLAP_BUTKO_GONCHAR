-- =====================================================================
--  partitions.sql  —  нарезка факта по месяцам и что она даёт
--  Практика З08 «Запросы и ключи», п.2
--
--  Здесь не «сделать партиции», а ПОКАЗАТЬ, что они уже есть и почему
--  именно по месяцу. DDL лежит в З07: `PARTITION BY toYYYYMM(order_date_key)`.
--
--  Три вопроса, на которые отвечает файл:
--     1. какие ключи реально стоят на таблице;
--     2. на какие куски она разрезана и сколько в куске строк;
--     3. какие куски ClickHouse пропускает на конкретном фильтре по дате.
--
--  Запуск:
--      python 08_З08_запросы_и_ключи_ch/etl/run_queries.py --file sql/partitions.sql
-- =====================================================================


-- @query 1. Ключи таблицы: партиция, сортировка, первичный индекс

-- PARTITION BY и ORDER BY — РАЗНЫЕ рычаги:
--   partition_key = toYYYYMM(order_date_key) — на какие КУСКИ НА ДИСКЕ
--                                             разрезана таблица;
--   sorting_key   = порядок строк ВНУТРИ куска.
-- Primer: партиция = папка, ORDER BY = порядок карточек в папке.
SELECT
    name                       AS table_name,
    engine,
    partition_key,
    sorting_key,
    primary_key,
    total_rows
FROM system.tables
WHERE database = 'olist'
ORDER BY name;


-- @query 2. Раскладка fact_sales по партициям

-- Одна строка = один месяц. `parts` — сколько физических кусков лежит
-- на диске для этого месяца: их несколько, пока фон не слил их в один.
-- `rows` — сколько строк факта в этом месяце.
SELECT
    partition                          AS month_partition,
    count()                            AS parts,
    sum(rows)                          AS rows,
    formatReadableSize(sum(bytes_on_disk)) AS size_on_disk
FROM system.parts
WHERE database = 'olist'
  AND table = 'fact_sales'
  AND active
GROUP BY partition
ORDER BY partition;


-- @query 3. Что пропускается на фильтре по одному дню

-- EXPLAIN не выполняет запрос, а показывает план. `indexes = 1` добавляет
-- к плану работу индексов: видно, сколько ЧАСТЕЙ (Parts) и сколько
-- ГРАНУЛ (Granules) ClickHouse реально прочитал.
--
-- Читать так:
--   Partition    — отсечены все месяцы, кроме 2018-01;
--   MinMax       — отсечены части, где январь 2018 точно не встречается;
--   PrimaryKey   — внутри оставшейся части отсечены гранулы по
--                  (order_date_key, order_status) — ровно те два поля,
--                  что стоят первыми в ORDER BY.
EXPLAIN indexes = 1
SELECT sum(amount)
FROM olist.fact_sales
WHERE order_date_key = DATE '2018-01-15'
  AND order_status = 'delivered';


-- @query 4. Контроль: тот же запрос без фильтра по дате

-- Здесь партиции не помогают вообще: читаются все 24 месяца. Это нормально:
-- партиция ускоряет запрос, только когда в нём есть условие по её ключу.
EXPLAIN indexes = 1
SELECT sum(amount)
FROM olist.fact_sales
WHERE order_status = 'delivered';
