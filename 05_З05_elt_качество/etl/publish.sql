-- =====================================================================
--  publish.sql  —  ПУБЛИКАЦИЯ ПРОВЕРЕННЫХ ДАННЫХ В ВИТРИНУ (З05)
--
--  Вызывается ТОЛЬКО после того, как quality_checks.sql вернул
--  passed = true для всех проверок с severity = `block`.
--
--  Стратегия повтора: ПОЛНЫЙ REFRESH.
--  При каждом запуске чистим целевые таблицы целиком и заливаем заново
--  из staging. Поэтому второй прогон даёт тот же результат, а не вдвое
--  больше строк. Частичное удаление «за период» здесь не нужно:
--  витрина маленькая, а инкремент по дате — источник типичных ошибок
--  «догрузил дважды» и «потерял границу».
--
--  Порядок: FK-ссылки удаляем ПЕРВЫМИ (факт), вставляем ПОСЛЕДНИМИ.
--  Иначе на время перезагрузки в витрине будут ссылки на пустые
--  справочники — ровно то состояние, ради которого существуют проверки.
--
--  Плейсхолдер {RAW} не используется.
-- =====================================================================

-- ── очистка: сначала факт, потом справочники (порядок обратный вставке)

DELETE FROM fact_sales;
DELETE FROM dim_product;
DELETE FROM dim_seller;
DELETE FROM dim_customer;
DELETE FROM dim_date;
DELETE FROM dim_category;
DELETE FROM dim_geo;

-- ── вставка: сначала справочники, потом факт

INSERT INTO dim_geo      SELECT * FROM stg_dim_geo;
INSERT INTO dim_category SELECT * FROM stg_dim_category;
INSERT INTO dim_date     SELECT * FROM stg_dim_date;
INSERT INTO dim_customer SELECT * FROM stg_dim_customer;
INSERT INTO dim_seller   SELECT * FROM stg_dim_seller;
INSERT INTO dim_product  SELECT * FROM stg_dim_product;

INSERT INTO fact_sales SELECT * FROM stg_fact_sales;