-- =====================================================================
--  scd2_change_2018.sql  —  СЫГРАТЬ СМЕНУ АТРИБУТА (З06, Вариант A, п.2)
--
--  СЦЕНАРИЙ
--  -------
--  С 2018-01-01 маркетплейс реорганизовал каталог: часть товаров из
--  категории «Аксессуары» (informatica_acessorios) выделена в отдельную
--  категорию «Аудио» (audio).
--
--  Правило отбора (детерминированное, воспроизводится одним запросом):
--      товары категории informatica_acessorios, у которых СРЕДНЯЯ ЦЕНА
--      ПРОДАЖИ В 2017 ГОДУ > 200 BRL — это дорогие позиции (наушники,
--      акустика, игровые аксессуары), а не кабели и мыши.
--
--  Отбор даёт 185 товаров. Именно их 2017-е продажи обязаны остаться
--  в informatica_acessorios — иначе отчёт за 2017 год перепишет историю.
--
--  МЕХАНИКА SCD2 — ровно три шага, больше ничего не нужно:
--    0. зафиксировать множество затронутых товаров В ОТДЕЛЬНУЮ таблицу;
--    1. закрыть текущую версию (valid_to = момент изменения, is_current = false);
--    2. вставить новую версию с новым значением атрибута.
--  Старая строка НЕ удаляется и НЕ затирается.
--
--  ⚠️ ПОЧЕМУ ШАГ 0 ОБЯЗАТЕЛЕН
--  Первая версия этого скрипта делала только шаги 1 и 2, и вставка новой
--  версии молча не вставила !1 строки. Причина: шаг 2 джойнил
--  справочник по `is_current`, но на шаге 1 эта версия уже была
--  помечена как не текущая. Классическая ошибка SCD2 «закрыл и не открыл».
--  Множество затронутых товаров фиксируется ДО изменения и используется
--  обоими шагами — так шаги не зависят от порядка.
-- =====================================================================

-- ── Шаг 0. ФИКСИРУЕМ множество затронутых товаров ─────────────────────
DROP TABLE IF EXISTS scd2_change_set;

CREATE TABLE scd2_change_set AS
SELECT product_id
FROM (
    SELECT f.product_id,
           AVG(CASE WHEN f.order_date_key >= DATE '2017-01-01'
                     AND f.order_date_key <  DATE '2018-01-01'
                    THEN f.amount END) AS avg_price_2017
    FROM fact_sales f
    JOIN dim_product p ON p.product_id = f.product_id
    WHERE p.category_name_pt = 'informatica_acessorios'
      AND f.order_status = 'delivered'
    GROUP BY 1
) t
WHERE t.avg_price_2017 > 200;

-- ── Шаг 1. ЗАКРЫВАЕМ текущую версию у затронутых товаров ──────────────
-- valid_to = момент изменения. Интервал полуоткрытый [from, to),
-- поэтому вставка версии 2 с тем же valid_from НЕ создаёт ни наложения,
-- ни щели.
UPDATE dim_product_scd2
SET valid_to   = TIMESTAMP '2018-01-01 00:00:00',
    is_current = FALSE
WHERE is_current
  AND product_id IN (SELECT product_id FROM scd2_change_set);

-- ── Шаг 2. ВСТАВЛЯЕМ новую версию ──────────────────────────────────────
-- product_sk обязан быть УНИКАЛЬНЫМ на каждую строку. Поэтому берём не
-- (SELECT MAX(product_sk)+1 FROM ...) — это дало бы всем новым строкам
-- одно и то же значение, — а ROW_NUMBER со смещением от максимума.
--
-- Номер версии берём от МАКСИМАЛЬНОЙ версии товара, а не от is_current:
-- на шаге 1 текущая версия уже закрыта, опора на is_current здесь
-- и означала бы «ничего не вставить».
--
-- Перевод категории берём из dim_category, а не пишем строкой:
-- иначе перевод можно случайно рассинхронизировать со справочником.
INSERT INTO dim_product_scd2
WITH affected AS (
    SELECT
        p.product_id,
        p.product_name_lenght,
        p.product_description_lenght,
        p.product_photos_qty,
        p.product_weight_g,
        p.product_length_cm,
        p.product_height_cm,
        p.product_width_cm,
        (SELECT MAX(d.version_no) + 1
           FROM dim_product_scd2 d
          WHERE d.product_id = p.product_id)      AS version_no,
        ROW_NUMBER() OVER (
            PARTITION BY (SELECT MAX(d2.product_sk) FROM dim_product_scd2 d2)
            ORDER BY p.product_id)                 AS tail_no
    FROM dim_product p
    WHERE p.product_id IN (SELECT product_id FROM scd2_change_set)
),
base AS (SELECT MAX(product_sk) AS max_sk FROM dim_product_scd2)
SELECT
    base.max_sk + a.tail_no                       AS product_sk,
    a.product_id,
    a.version_no,
    'audio'                                       AS category_name_pt,
    c.category_name_en                            AS category_name_en,
    TRUE                                          AS is_category_translated,
    TIMESTAMP '2018-01-01 00:00:00'               AS valid_from,
    TIMESTAMP '2106-02-07 06:28:15'               AS valid_to,
    TRUE                                          AS is_current,
    a.product_name_lenght,
    a.product_description_lenght,
    a.product_photos_qty,
    a.product_weight_g,
    a.product_length_cm,
    a.product_height_cm,
    a.product_width_cm,
    NULL::BIGINT                                  AS positions_in_version
FROM affected a
CROSS JOIN base
JOIN dim_category c ON c.category_name_pt = 'audio';

-- ── Шаг 3. ДОПИСЫВАЕМ счётчик позиций по версиям ────────────────────────
-- Чисто для демонстрации: показывает, сколько строк факта пришлось
-- на каждую версию. В боевой витрине это обычно отдельная витрина
-- агрегатов, а не колонка справочника.
UPDATE dim_product_scd2 d
SET positions_in_version = sub.cnt
FROM (
    SELECT product_sk, COUNT(*) AS cnt
    FROM fact_sales_scd2
    GROUP BY 1
) sub
WHERE sub.product_sk = d.product_sk;

-- ══════════════════════════════════════════════════════════════════════
--  КОНТРФАКТУС SCD1 — «ЧТО БЫЛО БЫ, ЕСЛИ БЫ МЫ ЗАТИРАЛИ КАТЕГОРИЮ»
--
--  Это НЕ часть витрины. Это отдельная таблица, которая показывает
--  цену отказа от истории: берём dim_product и тупо перезаписываем
--  категорию у затронутых товаров — ровно то, что делает SCD1.
--
--  Она нужна запросу Д3, чтобы сравнение было честным: пока мы строим
--  отчёт по dim_product, где категория ещё не затёрта, разницы не видно.
--  В боевой витрине такой таблицы быть не должно — именно поэтому
--  она называется контрфактусом и живёт в демонстрационном скрипте.
-- ══════════════════════════════════════════════════════════════════════
DROP TABLE IF EXISTS dim_product_scd1;

CREATE TABLE dim_product_scd1 AS
SELECT * FROM dim_product;

-- SCD1: категория ЗАТИРАЕТСЯ. Старые продажи January 2017 получат
-- категорию «Аудио» задним числом — ровно та ошибка, которую SCD2
-- предотвращает.
UPDATE dim_product_scd1
SET category_name_pt        = 'audio',
    category_name_en        = 'audio',
    is_category_translated  = TRUE
WHERE product_id IN (SELECT product_id FROM scd2_change_set);